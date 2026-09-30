from typing import Any


def _compute_intervals_union_duration(intervals: list[tuple[float, float]]) -> float:
    """Computes the total length of the union of intervals, correctly handling overlaps."""
    valid_intervals = [
        (max(0.0, float(start)), max(0.0, float(end))) for start, end in intervals if end > start
    ]
    if not valid_intervals:
        return 0.0

    sorted_ivs = sorted(valid_intervals, key=lambda x: x[0])
    merged: list[tuple[float, float]] = [sorted_ivs[0]]

    for start, end in sorted_ivs[1:]:
        last_start, last_end = merged[-1]
        if start <= last_end:
            merged[-1] = (last_start, max(last_end, end))
        else:
            merged.append((start, end))

    return sum(end - start for start, end in merged)


class SpeechMetricsCalculator:
    def __init__(self):
        pass

    def compute(
        self,
        segments: list[dict[str, Any]],
        call_duration_sec: float,
    ) -> dict[str, Any]:
        """Calculates deterministic acoustic speech metrics from timed segments."""
        if not segments or call_duration_sec <= 0:
            return {
                "manager_speech_share": 0.0,
                "client_speech_share": 0.0,
                "interruptions_count": 0,
                "silence_duration_sec": round(call_duration_sec, 2),
                "manager_wpm": 0.0,
                "client_wpm": 0.0,
            }

        mgr_duration = 0.0
        client_duration = 0.0
        mgr_words = 0
        client_words = 0
        interruptions = 0

        # Sort segments chronologically
        sorted_segs = sorted(segments, key=lambda x: x["start"])

        # Collect raw intervals for union silence calculation
        all_speech_intervals: list[tuple[float, float]] = []

        for i, seg in enumerate(sorted_segs):
            role = seg.get("speaker_role", "participant")
            start = max(0.0, float(seg["start"]))
            end = max(start, float(seg["end"]))
            dur = end - start
            text = seg.get("text", "")
            word_count = len(text.split())

            if dur > 0:
                all_speech_intervals.append((start, end))

            if role == "manager":
                mgr_duration += dur
                mgr_words += word_count
            elif role == "client":
                client_duration += dur
                client_words += word_count
            else:
                # Neutral participant split evenly
                mgr_duration += dur / 2.0
                client_duration += dur / 2.0
                mgr_words += word_count // 2
                client_words += word_count // 2

            # Check interruptions across all active concurrent segments
            # A segment j interrupts an ongoing segment i if i started earlier,
            # belongs to a different speaker role, and overlaps by > 0.3s
            for prev_idx in range(i):
                prev_seg = sorted_segs[prev_idx]
                prev_role = prev_seg.get("speaker_role", "participant")
                if role != prev_role:
                    overlap = min(prev_seg["end"], end) - max(prev_seg["start"], start)
                    if overlap > 0.3 and prev_seg["start"] < start:
                        interruptions += 1

        # Correct silence calculation via union of all active speech intervals
        speech_union_dur = _compute_intervals_union_duration(all_speech_intervals)
        silence_dur = max(0.0, call_duration_sec - speech_union_dur)

        mgr_share = (
            round(min(1.0, mgr_duration / call_duration_sec), 3) if call_duration_sec > 0 else 0.0
        )
        client_share = (
            round(min(1.0, client_duration / call_duration_sec), 3)
            if call_duration_sec > 0
            else 0.0
        )

        mgr_wpm = round((mgr_words / (mgr_duration / 60.0)), 1) if mgr_duration > 0 else 0.0
        client_wpm = (
            round((client_words / (client_duration / 60.0)), 1) if client_duration > 0 else 0.0
        )

        return {
            "manager_speech_share": mgr_share,
            "client_speech_share": client_share,
            "interruptions_count": interruptions,
            "silence_duration_sec": round(silence_dur, 2),
            "manager_wpm": mgr_wpm,
            "client_wpm": client_wpm,
        }
