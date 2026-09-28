from typing import Any

from app.core.logging import get_logger

logger = get_logger("vad_detector")


class VadDetector:
    def __init__(self, threshold: float = 0.5):
        self.threshold = threshold

    def get_speech_timestamps(
        self,
        audio_duration_sec: float,
        vad_segments: list[dict[str, float]],
    ) -> dict[str, Any]:
        """Calculates total speech duration, silence duration, and speech ratio."""
        total_speech = sum(max(0.0, s["end"] - s["start"]) for s in vad_segments)
        total_silence = max(0.0, audio_duration_sec - total_speech)
        ratio = total_speech / audio_duration_sec if audio_duration_sec > 0 else 0.0

        return {
            "total_speech_sec": round(total_speech, 2),
            "total_silence_sec": round(total_silence, 2),
            "speech_ratio": round(ratio, 4),
            "segments_count": len(vad_segments),
        }

    def detect_answering_machine(
        self,
        initial_transcript: str,
        speech_duration_first_15s: float,
    ) -> bool:
        """Detects answering machine / IVR phrases in the first 15 seconds of the call."""
        if not initial_transcript:
            return False

        text_lower = initial_transcript.lower()
        ivr_keywords = [
            "абонент временно недоступен",
            "оставьте сообщение после сигнала",
            "перезвоните позже",
            "аппарат абонента выключен",
            "вас приветствует автоответчик",
            "звуковой сигнал",
            "номер не отвечает",
        ]

        for phrase in ivr_keywords:
            if phrase in text_lower:
                logger.info("answering_machine_detected", phrase=phrase)
                return True

        return False
