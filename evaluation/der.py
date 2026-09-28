

def calculate_der(
    reference_segments: list[dict[str, any]],
    hypothesis_segments: list[dict[str, any]],
    collar_sec: float = 0.25,
) -> dict[str, float]:
    """
    Computes Diarization Error Rate (DER) breakdown:
    Missed Speech, False Alarm, Speaker Confusion.
    """
    total_ref_duration = sum(max(0.0, s["end"] - s["start"]) for s in reference_segments)
    if total_ref_duration <= 0:
        return {"der": 0.0, "missed_speech": 0.0, "false_alarm": 0.0, "speaker_confusion": 0.0}

    # Simplified segment overlap calculation for evaluation harness
    matched_duration = 0.0
    speaker_confusion_duration = 0.0

    for r in reference_segments:
        r_start, r_end, r_spk = r["start"], r["end"], r.get("speaker_role")
        for h in hypothesis_segments:
            h_start, h_end, h_spk = h["start"], h["end"], h.get("speaker_role")

            overlap_start = max(r_start, h_start)
            overlap_end = min(r_end, h_end)

            if overlap_end > overlap_start:
                dur = overlap_end - overlap_start
                if r_spk == h_spk:
                    matched_duration += dur
                else:
                    speaker_confusion_duration += dur

    missed_dur = max(0.0, total_ref_duration - (matched_duration + speaker_confusion_duration))
    total_hyp_duration = sum(max(0.0, s["end"] - s["start"]) for s in hypothesis_segments)
    false_alarm_dur = max(0.0, total_hyp_duration - (matched_duration + speaker_confusion_duration))

    der = (missed_dur + false_alarm_dur + speaker_confusion_duration) / total_ref_duration

    return {
        "der": round(min(1.0, der), 4),
        "missed_speech_rate": round(missed_dur / total_ref_duration, 4),
        "false_alarm_rate": round(false_alarm_dur / total_ref_duration, 4),
        "speaker_confusion_rate": round(speaker_confusion_duration / total_ref_duration, 4),
    }
