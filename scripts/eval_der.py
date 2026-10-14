#!/usr/bin/env python3
"""DER-стенд C10-4: моно (CAM++) и стерео (по каналам) + точность ролей.

Моно: whisper-small ASR -> MonoDiarizer -> roles; collar DER 0,25 с.
Стерео: поканальная энергия-речь -> роли по channel_mapping.
Выход: reports/der_suite.json. Аудио в git не входит.
"""

import io
import json
import os
import time

import numpy as np
import soundfile as sf

from evaluation.der import calculate_der

SUITE_DIR = os.path.join("tmp", "der_suite")


def asr_segments(wav_path: str) -> list[dict]:
    from app.asr.engine import AsrEngine

    eng = AsrEngine(model_name="./models/whisper-small")
    with open(wav_path, "rb") as f:
        segs, _ = eng.transcribe(
            io.BytesIO(f.read()),
            language="ru",
            beam_size=1,
            temperature=0.0,
            vad_filter=True,
            word_timestamps=False,
        )
    return segs


def channel_activity(wav_path: str, frame_sec: float = 0.1, thr: float = 0.02) -> list[dict]:
    """Поканальная речевая активность (энергетический VAD) для стерео-ветки."""
    data, sr = sf.read(wav_path, dtype="float32")
    if data.ndim < 2:
        return []
    n = int(frame_sec * sr)
    out = []
    for ch in range(data.shape[1]):
        mono = data[:, ch]
        frames = mono[: len(mono) // n * n].reshape(-1, n)
        active = np.abs(frames).mean(axis=1) > thr
        start = None
        for i, flag in enumerate(active):
            if flag and start is None:
                start = i * frame_sec
            elif not flag and start is not None:
                out.append(
                    {
                        "start": round(start, 2),
                        "end": round(i * frame_sec, 2),
                        "speaker_label": f"CH{ch}",
                    }
                )
                start = None
        if start is not None:
            out.append(
                {
                    "start": round(start, 2),
                    "end": round(len(active) * frame_sec, 2),
                    "speaker_label": f"CH{ch}",
                }
            )
    return sorted(out, key=lambda s: s["start"])


def main() -> None:
    from app.diarization.mono_diarizer import MonoDiarizer
    from app.diarization.roles import RoleClassifier

    manifest = json.load(open(os.path.join(SUITE_DIR, "manifest.json"), encoding="utf-8"))
    diarizer = MonoDiarizer()
    roles = RoleClassifier()
    results: dict = {"mono": [], "stereo": []}
    for entry in manifest:
        ref = [
            {"start": s["start"], "end": s["end"], "speaker_role": s["speaker_role"]}
            for s in entry["segments"]
        ]
        t0 = time.time()
        if entry["stereo"]:
            hyp_segs = channel_activity(entry["files"]["16k"])
            ch0 = [s for s in hyp_segs if s["speaker_label"] == "CH0"]
            ch1 = [s for s in hyp_segs if s["speaker_label"] == "CH1"]
            labeled, _ = roles.assign_stereo_roles(
                ch0, ch1, {"channel_0": "client", "channel_1": "manager"}
            )
            hyp = [
                {
                    "start": s["start"],
                    "end": s["end"],
                    "speaker_role": s.get("speaker_role", "participant_1"),
                }
                for s in labeled
            ]
            kind = "stereo"
        else:
            raw = asr_segments(entry["files"]["16k"])
            dia = diarizer.diarize(entry["files"]["16k"], raw)
            labeled, _ = roles.classify_mono_speakers(dia)
            hyp = [
                {
                    "start": s["start"],
                    "end": s["end"],
                    "speaker_role": s.get("speaker_role", "participant_1"),
                }
                for s in labeled
            ]
            kind = "mono"
        dt = time.time() - t0
        der = calculate_der(ref, hyp, collar_sec=0.25)
        # точность ролей: доля времени с верной ролью
        hit = tot = 0.0
        for r in ref:
            for h in hyp:
                ov = max(0.0, min(r["end"], h["end"]) - max(r["start"], h["start"]))
                tot += 0
                if ov > 0:
                    tot += ov
                    if r["speaker_role"] == h["speaker_role"]:
                        hit += ov
        results[kind].append(
            {
                "id": entry["id"],
                "der": der["der"],
                "role_acc": round(hit / tot, 4) if tot else 0.0,
                "seconds": round(dt, 1),
            }
        )
        print(entry["id"], kind, der, "role_acc=%.3f" % (hit / tot if tot else 0), flush=True)
    summary = {}
    for kind in ("mono", "stereo"):
        rows = results[kind]
        import statistics

        summary[kind] = {
            "n": len(rows),
            "mean_der": round(sum(r["der"] for r in rows) / len(rows), 4) if rows else 0,
            "mean_role_acc": round(sum(r["role_acc"] for r in rows) / len(rows), 4) if rows else 0,
            "median_seconds": round(statistics.median(r["seconds"] for r in rows), 1)
            if rows
            else 0,
        }
    report = {"summary": summary, "per_file": results}
    with open("reports/der_suite.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(summary, flush=True)


if __name__ == "__main__":
    main()
