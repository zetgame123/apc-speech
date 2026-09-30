import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger("role_classifier")

MANAGER_GREETING_PATTERNS = [
    re.compile(r"\bздравствуйте\b", re.IGNORECASE),
    re.compile(r"\bдобрый\s+день\b", re.IGNORECASE),
    re.compile(r"\bкомпания\b", re.IGNORECASE),
    re.compile(r"\bменя\s+зовут\b", re.IGNORECASE),
    re.compile(r"\bчем\s+могу\s+помочь\b", re.IGNORECASE),
    re.compile(r"\bотдел\s+продаж\b", re.IGNORECASE),
    re.compile(r"\bслушаю\s+вас\b", re.IGNORECASE),
]

MANAGER_SPEECH_PATTERNS = [
    re.compile(r"\bможем\s+предложить\b", re.IGNORECASE),
    re.compile(r"\bсвободна\s+сторона\b", re.IGNORECASE),
    re.compile(r"\bотправлю\s+вам\b", re.IGNORECASE),
    re.compile(r"\bподготовлю\s+расчет\b", re.IGNORECASE),
    re.compile(r"\bназовите\s+почту\b", re.IGNORECASE),
]

CLIENT_PATTERNS = [
    re.compile(r"\bподскажите\s+пожалуйста\b", re.IGNORECASE),
    re.compile(r"\bподскажите\b", re.IGNORECASE),
    re.compile(r"\bсколько\s+стоит\b", re.IGNORECASE),
    re.compile(r"\bстоимость\b", re.IGNORECASE),
    re.compile(r"\bнас\s+интересует\b", re.IGNORECASE),
    re.compile(r"\bнам\s+нужен\b", re.IGNORECASE),
    re.compile(r"\bмы\s+хотим\s+разместить\b", re.IGNORECASE),
    re.compile(r"\bкакие\s+цены\b", re.IGNORECASE),
    re.compile(r"\bцена\b", re.IGNORECASE),
]


class RoleClassifier:
    def __init__(self):
        pass

    def assign_stereo_roles(
        self,
        ch0_segments: list[dict[str, Any]],
        ch1_segments: list[dict[str, Any]],
        channel_mapping: dict[str, str] | None = None,
    ) -> tuple[list[dict[str, Any]], str]:
        """
        Maps stereo channels directly to manager and client.
        Default: channel_0 = manager, channel_1 = client.
        """
        mapping = channel_mapping or {"channel_0": "manager", "channel_1": "client"}
        ch0_role = mapping.get("channel_0", "manager")
        ch1_role = mapping.get("channel_1", "client")

        combined = []
        for s in ch0_segments:
            s_copy = dict(s)
            s_copy["speaker_label"] = "SPEAKER_0"
            s_copy["speaker_role"] = ch0_role
            combined.append(s_copy)

        for s in ch1_segments:
            s_copy = dict(s)
            s_copy["speaker_label"] = "SPEAKER_1"
            s_copy["speaker_role"] = ch1_role
            combined.append(s_copy)

        # Sort chronologically by start timestamp
        combined.sort(key=lambda x: x["start"])
        return combined, "high"

    def classify_mono_speakers(
        self,
        segments: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], str]:
        """
        Heuristic role assignment for mono audio segments labeled SPEAKER_0 and SPEAKER_1.
        Evaluates opening greetings, manager pitch markers, and client query phrases.
        """
        spk0_score = 0.0
        spk1_score = 0.0

        for s in segments:
            text = s.get("text", "")
            start = s.get("start", 0.0)
            speaker = s.get("speaker_label", "SPEAKER_0")

            # Check greetings in first 20 seconds
            is_early = start <= 20.0
            for pat in MANAGER_GREETING_PATTERNS:
                if pat.search(text):
                    weight = 0.6 if is_early else 0.2
                    if speaker == "SPEAKER_0":
                        spk0_score += weight
                    else:
                        spk1_score += weight

            # Check general manager speech patterns
            for pat in MANAGER_SPEECH_PATTERNS:
                if pat.search(text):
                    if speaker == "SPEAKER_0":
                        spk0_score += 0.2
                    else:
                        spk1_score += 0.2

            # Check client questions (inverse score)
            for pat in CLIENT_PATTERNS:
                if pat.search(text):
                    if speaker == "SPEAKER_0":
                        spk0_score -= 0.3
                    else:
                        spk1_score -= 0.3

        logger.info("role_scores", spk0_score=spk0_score, spk1_score=spk1_score)

        diff = abs(spk0_score - spk1_score)
        if diff >= 0.4:
            confidence = "high"
        elif diff >= 0.15:
            confidence = "medium"
        else:
            confidence = "low"

        if spk0_score > spk1_score:
            role_map = {"SPEAKER_0": "manager", "SPEAKER_1": "client"}
        elif spk1_score > spk0_score:
            role_map = {"SPEAKER_1": "manager", "SPEAKER_0": "client"}
        else:
            role_map = {"SPEAKER_0": "manager", "SPEAKER_1": "client"}

        updated_segments = []
        for s in segments:
            s_copy = dict(s)
            spk = s_copy.get("speaker_label", "SPEAKER_0")
            s_copy["speaker_role"] = role_map.get(spk, "participant")
            updated_segments.append(s_copy)

        return updated_segments, confidence
