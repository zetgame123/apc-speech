#!/usr/bin/env python3
"""C10-6: часовая запись -> полный пайплайн, RTF, пик RAM, время по стадиям.

Загружает WAV джобой, опрашивает статус (таймлайн шагов), параллельно
сэмплирует docker stats (пик RAM worker-long/llm-runtime/api).
Выход: reports/hour_*.json. Аудио в git не входит.
"""

import argparse
import json
import os
import subprocess
import time
import urllib.request

API = os.environ.get("API_BASE_URL", "http://localhost:8000")
TOK = os.environ["DEV_TOKEN"] if "DEV_TOKEN" in os.environ else "dev-token"

STAT_CONTAINERS = [
    "apc_small-worker-long-1",
    "apc_small-llm-runtime-1",
    "apc_small-api-1",
]


def api_req(url: str, data=None, fields=None):
    if fields is not None:
        import uuid

        boundary = uuid.uuid4().hex
        body = b""
        for name, value, filename in fields:
            body += f"--{boundary}\r\n".encode()
            if filename:
                body += (
                    (
                        f'Content-Disposition: form-data; name="{name}"; '
                        f'filename="{filename}"\r\n'
                        "Content-Type: audio/wav\r\n\r\n"
                    ).encode()
                    + value
                    + b"\r\n"
                )
            else:
                body += (
                    (f'Content-Disposition: form-data; name="{name}"\r\n\r\n').encode()
                    + value.encode()
                    + b"\r\n"
                )
        body += f"--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {TOK}",
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "Idempotency-Key": f"hour-{int(time.time())}",
            },
        )
    else:
        req = urllib.request.Request(
            url,
            data=json.dumps(data).encode() if data else None,
            headers={"Authorization": f"Bearer {TOK}"},
        )
    return json.load(urllib.request.urlopen(req, timeout=600))


def docker_stats_once() -> dict:
    try:
        p = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:
        return {}
    out = {}
    for line in p.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[0] in STAT_CONTAINERS:
            # '1.234GiB / 10GiB' -> MiB
            raw = parts[1]
            try:
                if raw.endswith("GiB"):
                    out[parts[0]] = float(raw[:-3]) * 1024
                elif raw.endswith("MiB"):
                    out[parts[0]] = float(raw[:-3])
                elif raw.endswith("KiB"):
                    out[parts[0]] = float(raw[:-3]) / 1024
            except ValueError:
                pass
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--wav", default="tmp/hour_suite/hour_mono_16k.wav")
    ap.add_argument("--namespace", default="hour-small-uncapped")
    ap.add_argument("--audio-sec", type=float, default=3600.0)
    ap.add_argument("--out", default="reports/hour_small_uncapped.json")
    ap.add_argument("--poll", type=int, default=60)
    ap.add_argument("--label", default="small-uncapped")
    args = ap.parse_args()

    with open(args.wav, "rb") as f:
        wav = f.read()
    t_up0 = time.time()
    up = api_req(
        f"{API}/v1/jobs/upload",
        fields=[
            ("namespace", args.namespace, None),
            ("file", wav, "hour.wav"),
            ("external_id", f"hour-{args.label}", None),
        ],
    )
    job_id = up["job_id"]
    print(f"uploaded {job_id} in {time.time() - t_up0:.0f}s", flush=True)
    t0 = time.time()
    timeline = []
    peak: dict = {}
    last_step = None
    while True:
        try:
            st = api_req(f"{API}/v1/jobs/{job_id}?namespace={args.namespace}")
        except Exception as e:
            print(f"poll err {type(e).__name__}", flush=True)
            time.sleep(15)
            continue
        step = f"{st.get('step')}:{st.get('progress')}"
        if step != last_step:
            timeline.append(
                {
                    "t": round(time.time() - t0, 1),
                    "status": st.get("status"),
                    "step": st.get("step"),
                    "progress": st.get("progress"),
                }
            )
            print(timeline[-1], flush=True)
            last_step = step
        for _ in range(args.poll // 15):
            for name, mib in docker_stats_once().items():
                peak[name] = max(peak.get(name, 0.0), round(mib, 1))
            time.sleep(15)
        if st.get("status") in ("succeeded", "failed"):
            total = time.time() - t0
            break

    def mhz():
        out = []
        try:
            p = subprocess.run(
                ["grep", "cpu MHz", "/proc/cpuinfo"],
                capture_output=True,
                text=True,
            )
            for line in p.stdout.splitlines()[4:6]:
                out.append(line.split(":")[1].strip())
        except Exception:
            pass
        return out

    def gov():
        try:
            return open("/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor").read().strip()
        except Exception:
            return "?"

    def cpuset(name: str) -> str:
        try:
            p = subprocess.run(
                [
                    "docker",
                    "inspect",
                    name,
                    "--format",
                    "{{.HostConfig.CpusetCpus}}/{{.HostConfig.Memory}}",
                ],
                capture_output=True,
                text=True,
            )
            return p.stdout.strip()
        except Exception:
            return "?"

    rep = {
        "label": args.label,
        "audio_sec": args.audio_sec,
        "job_id": job_id,
        "job_status": st.get("status"),
        "wall_sec": round(total, 1),
        "rtf": round(total / args.audio_sec, 3),
        "timeline": timeline,
        "peak_ram_mib": peak,
        "env": {
            "cpu": "Ryzen 7 8845HS",
            "mhz": mhz(),
            "governor": gov(),
            "cpuset": {c: cpuset(c) for c in STAT_CONTAINERS},
        },
    }
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, ensure_ascii=False, indent=2)
    print(f"RTF={rep['rtf']} wall={total:.0f}s peak={peak}", flush=True)


if __name__ == "__main__":
    main()
