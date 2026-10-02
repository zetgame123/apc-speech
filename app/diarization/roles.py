import re
from typing import Any

from app.core.logging import get_logger

logger = get_logger("role_classifier")

MANAGER_GREETING_PATTERNS = [
    re.compile(r"\b(компания|агентство|сервис)\s+[A-ZА-ЯЁ«\"]", re.IGNORECASE),
    re.compile(r"\b(отдел\s+продаж|служба\s+поддержки|колл-центр)\b", re.IGNORECASE),
    re.compile(r"\b(чем\s+могу|могу\s+ли\s+я\s+вам)\s+помочь\b", re.IGNORECASE),
    re.compile(r"\bслушаю\s+вас\b", re.IGNORECASE),
    re.compile(r"\bвас\s+приветствует\b", re.IGNORECASE),
]

MANAGER_SPEECH_PATTERNS = [
    # Official representation & company identity
    re.compile(r"\b(я\s+)?являюсь\s+представителем\b", re.IGNORECASE),
    re.compile(
        r"\bпредставитель\s+(компании|организационного|оргкомитета|департамента|отдела)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bорганизационн(ый|ого)\s+комитет", re.IGNORECASE),
    re.compile(r"\bоргкомитет", re.IGNORECASE),
    re.compile(
        r"\b(вас\s+беспокоит|звоню\s+вам|звоним\s+вам|обращаюсь\s+к\s+вам)\b", re.IGNORECASE
    ),
    # Qualification, pitches & invites
    re.compile(r"\b(планируете|желаете|рассматриваете)\s+ли\s+вы\b", re.IGNORECASE),
    re.compile(r"\b(хотел|хотела)\s+бы\s+уточнить\b", re.IGNORECASE),
    re.compile(r"\bу\s+меня\s+есть\s+возможность\b", re.IGNORECASE),
    re.compile(r"\bзарегистрировать\s+вас\b", re.IGNORECASE),
    re.compile(r"\b(приглашаем|пригласить)\s+вас\b", re.IGNORECASE),
    re.compile(r"\bдля\s+участия\s+в\b", re.IGNORECASE),
    re.compile(r"\bпосетить\s+(конференцию|вебинар|мероприятие|форум)\b", re.IGNORECASE),
    re.compile(
        r"\bвы\s+являетесь\s+(генеральным\s+директором|руководителем|собственником|директором)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bу\s+вас\s+в\s+организации\b", re.IGNORECASE),
    re.compile(r"\bорганизация\s+у\s+вас\s+называется\b", re.IGNORECASE),
    re.compile(r"\bвам\s+так\s+будет\s+удобно\b", re.IGNORECASE),
    re.compile(r"\bудобно\s+сейчас\s+говорить\b", re.IGNORECASE),
    re.compile(r"\bминуты\s+две\b", re.IGNORECASE),
    re.compile(r"\bзаймет\s+(пару|несколько)\s+минут\b", re.IGNORECASE),
    # Data collection & confirmation
    re.compile(
        r"\bподскажите(\s+также)?(\s+пожалуйста)?\s+(вашу\s+)?(фамилию|имя|отчество|номер|почту|телефон|индекс|адрес)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(ваш\s+индекс|вашу\s+почту|ваш\s+телефон)\s+уточнить\b", re.IGNORECASE),
    re.compile(r"\bдиктуйте\b", re.IGNORECASE),
    re.compile(r"\bзаписываю\b", re.IGNORECASE),
    re.compile(r"\b(можем|могу)\s+предложить\b", re.IGNORECASE),
    re.compile(r"\b(отправлю|направлю|вышлю|пришлю)\s+вам\b", re.IGNORECASE),
    re.compile(r"\bподготовлю\s+расчет\b", re.IGNORECASE),
    re.compile(r"\bназовите\s+почту\b", re.IGNORECASE),
    re.compile(r"\bсвободна\s+сторона\b", re.IGNORECASE),
    re.compile(r"\bстоимость\s+.*?рубл", re.IGNORECASE),
    # Closing & thanks
    re.compile(r"\bвы\s+успешно\s+зарегистрированы\b", re.IGNORECASE),
    re.compile(r"\bспасибо\s+за\s+(уделенное|предоставленное)\s+время\b", re.IGNORECASE),
    re.compile(r"\bвсего\s+доброго(,\s+до\s+свидания)?\b", re.IGNORECASE),
]

CLIENT_PATTERNS = [
    re.compile(
        r"\b(сколько\s+стоит|какая\s+стоимость|какие\s+цены|какова\s+цена)\b", re.IGNORECASE
    ),
    re.compile(
        r"\bподскажите\s+(пожалуйста\s+)?(стоимость|цену|цены|прайс|тариф)\b", re.IGNORECASE
    ),
    re.compile(r"\bподскажите\s+по\s+ценам\b", re.IGNORECASE),
    re.compile(r"\bнас\s+интересует\b", re.IGNORECASE),
    re.compile(r"\bнам\s+(нужен|нужно|требуется)\b", re.IGNORECASE),
    re.compile(r"\bмы\s+хотим\s+(разместить|заказать|купить)\b", re.IGNORECASE),
    re.compile(r"\b(я\s+)?получу\s+ваше\s+письмо\b", re.IGNORECASE),
    re.compile(r"\b(вы\s+мне\s+)?присылали\b", re.IGNORECASE),
    re.compile(r"\b(надо|нужно)\s+(пройти|зарегистрироваться)\b", re.IGNORECASE),
    re.compile(r"\bдолгий\s+процесс\b", re.IGNORECASE),
    re.compile(r"\b(ну\s+)?давайте\b", re.IGNORECASE),
    re.compile(r"\b(да\s*,?\s*)?в\s+принципе(\s*,?\s*можно)?\b", re.IGNORECASE),
    re.compile(r"\bоставляйте\s+(этот|тот)\b", re.IGNORECASE),
    re.compile(r"\bдостаточно\s+(почты|e-?mail)\b", re.IGNORECASE),
    re.compile(r"\bбуду\s+ждать\s+подтверждение\b", re.IGNORECASE),
    re.compile(r"\bкакая\s+компания\b", re.IGNORECASE),
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
        Evaluates opening greetings, manager pitch/qualification markers, and client query phrases.
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
                    weight = 0.6 if is_early else 0.3
                    if speaker == "SPEAKER_0":
                        spk0_score += weight
                    else:
                        spk1_score += weight

            # Check manager qualification, pitch and workflow speech patterns
            for pat in MANAGER_SPEECH_PATTERNS:
                if pat.search(text):
                    if speaker == "SPEAKER_0":
                        spk0_score += 0.4
                    else:
                        spk1_score += 0.4

            # Check client questions and confirmations (inverse score)
            for pat in CLIENT_PATTERNS:
                if pat.search(text):
                    if speaker == "SPEAKER_0":
                        spk0_score -= 0.4
                    else:
                        spk1_score -= 0.4

        logger.info("role_scores", spk0_score=spk0_score, spk1_score=spk1_score)

        diff = abs(spk0_score - spk1_score)
        if diff >= 0.4:
            confidence = "high"
        elif diff >= 0.15:
            confidence = "medium"
        else:
            confidence = "low"

        unique_spks = {s.get("speaker_label", "SPEAKER_0") for s in segments}
        if len(unique_spks) <= 1:
            if spk0_score >= 0.3:
                role = "manager"
            elif spk0_score <= -0.3:
                role = "client"
            else:
                role = "participant"
            updated_segments = []
            for s in segments:
                s_copy = dict(s)
                s_copy["speaker_role"] = role
                updated_segments.append(s_copy)
            return updated_segments, "high"

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
