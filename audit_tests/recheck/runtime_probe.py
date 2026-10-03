"""Live HTTP regression and repeated 10-second voice benchmark."""

import json
import math
import statistics
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "audit_tests/recheck/evidence"
HEADERS = {"Authorization": "Bearer recheck-admin", "X-Request-Id": "audit-recheck"}


def record(label, response):
    item = {
        "label": label,
        "status": response.status_code,
        "request_id": response.headers.get("x-request-id"),
        "body": response.text,
    }
    with (OUT / "http.jsonl").open("a") as f:
        f.write(json.dumps(item, ensure_ascii=False) + "\n")
    print(label, response.status_code, response.text[:700], flush=True)
    return response


def poll(client, job_id):
    for _ in range(90):
        r = client.get(f"/v1/jobs/{job_id}", params={"namespace": "recheck"})
        value = r.json()
        if value.get("status") in {"succeeded", "failed"}:
            record("job_terminal", r)
            return value
        time.sleep(1)
    record("job_timeout", r)
    return value


def main():
    with httpx.Client(base_url="http://127.0.0.1:18000", headers=HEADERS, timeout=180) as client:
        for path in ["/health", "/ready", "/metrics"]:
            r = record(path, client.get(path))
            if path == "/metrics":
                (OUT / "metrics.txt").write_text(r.text)
        record(
            "malformed",
            client.post(
                "/v1/texts/analyze", content="{bad", headers={"Content-Type": "application/json"}
            ),
        )
        record(
            "validation_with_pii",
            client.post(
                "/v1/texts/analyze",
                json={"namespace": "recheck", "texts": "client@audit.invalid +7 912 345 67 89"},
            ),
        )
        record(
            "cluster_zero",
            client.post(
                "/v1/texts/analyze", json={"namespace": "recheck", "texts": ["а", "я", "щит"]}
            ),
        )
        static = client.get("/openapi.json").json()
        (OUT / "runtime-openapi.json").write_text(json.dumps(static, ensure_ascii=False, indent=2))
        # Space expensive requests into separate rate-limit windows.
        time.sleep(11)
        data = (ROOT / "audit_tests/corpus/voice10.wav").read_bytes()
        measurements = []
        for i in range(11):
            start = time.perf_counter()
            r = record(
                f"voice10_{i}",
                client.post(
                    "/v1/voice/search",
                    params={"namespace": "recheck"},
                    files={"file": ("voice10.wav", data, "audio/wav")},
                ),
            )
            elapsed = (time.perf_counter() - start) * 1000
            if r.status_code == 200:
                measurements.append({"elapsed_ms": round(elapsed, 2), **r.json()})
            if i in {3, 7}:
                time.sleep(11)
        values = [x["elapsed_ms"] for x in measurements[1:]]

        def percentile(q):
            return sorted(values)[max(0, math.ceil(q * len(values)) - 1)]

        result = {
            "warmup": measurements[:1],
            "samples": measurements[1:],
            "n": len(values),
            "p50_ms": statistics.median(values),
            "p95_ms": percentile(0.95),
            "p99_ms": percentile(0.99),
            "max_ms": max(values),
        }
        (OUT / "voice-benchmark.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "BENCHMARK",
            json.dumps({k: v for k, v in result.items() if k not in {"samples", "warmup"}}),
            flush=True,
        )
        time.sleep(11)
        for explicit in [True, False]:
            hdr = {"Idempotency-Key": "recheck-corrupt"} if explicit else {}
            ids = []
            for i in range(2):
                r = record(
                    f"bad_upload_explicit_{explicit}_{i}",
                    client.post(
                        "/v1/jobs/upload",
                        headers=hdr,
                        data={"namespace": "recheck"},
                        files={"file": ("invalid.wav", b"invalid media recheck", "audio/wav")},
                    ),
                )
                if r.status_code in {200, 202}:
                    job_id = r.json()["job_id"]
                    ids.append(job_id)
                    if i == 0:
                        poll(client, job_id)
            print("IDEMPOTENCY", explicit, ids, flush=True)
        time.sleep(11)
        data = (ROOT / "data/evaluation_suite/sample_clean_01_clean.wav").read_bytes()
        r = record(
            "small_upload",
            client.post(
                "/v1/jobs/upload",
                headers={"Idempotency-Key": "recheck-small"},
                data={"namespace": "recheck"},
                files={"file": ("sample.wav", data, "audio/wav")},
            ),
        )
        if r.status_code in {200, 202}:
            terminal = poll(client, r.json()["job_id"])
            (OUT / "small-job.json").write_text(json.dumps(terminal, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
