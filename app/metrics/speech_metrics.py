from typing import Any


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

        for i, seg in enumerate(sorted_segs):
            role = seg.get("speaker_role", "participant")
            dur = max(0.0, seg["end"] - seg["start"])
            text = seg.get("text", "")
            word_count = len(text.split())

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

            # Check interruptions with next segment
            if i < len(sorted_segs) - 1:
                next_seg = sorted_segs[i + 1]
                next_role = next_seg.get("speaker_role", "participant")
                # If different speaker and overlap > 0.3s
                if role != next_role and seg["end"] > next_seg["start"] + 0.3:
                    interruptions += 1

        total_speech = mgr_duration + client_duration
        silence_dur = max(0.0, call_duration_sec - total_speech)

        mgr_share = round(min(1.0, mgr_duration / call_duration_sec), 3) if call_duration_sec > 0 else 0.0
        client_share = round(min(1.0, client_duration / call_duration_sec), 3) if call_duration_sec > 0 else 0.0

        mgr_wpm = round((mgr_words / (mgr_duration / 60.0)), 1) if mgr_duration > 0 else 0.0
        client_wpm = round((client_words / (client_duration / 60.0)), 1) if client_duration > 0 else 0.0

        return {
            "manager_speech_share": mgr_share,
            "client_speech_share": client_share,
            "interruptions_count": interruptions,
            "silence_duration_sec": round(silence_dur, 2),
            "manager_wpm": mgr_wpm,
            "client_wpm": client_wpm,
        }
