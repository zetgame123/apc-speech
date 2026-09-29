import math
import os
from typing import Any

import yaml

from app.asr.normalizer import RussianTextNormalizer
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("asr_engine")

DEFAULT_HINTS = (
    "Поиск и аренда наружной рекламы в Москве и Московской области: рекламные щиты 3×6, билборды, "
    "ситиборды, сити-формат, суперсайты 5×12, медиафасады, digital-экраны, призматроны. "
    "Стоимость и бюджет: аренда щитов от 30 000 руб., до 50 000 руб., до 100 000 рублей, "
    "от 40 000 до 80 000 руб. Локации: на Автозаводской, на Таганской, на Соколе, на Ленинградке, "
    "на МКАД, на ТТК, Шоссе Энтузиастов, Варшавское шоссе, Новый Арбат, Кутузовский проспект, "
    "Балашиха, Химки, Мытищи, Подольск, Красногорск, Одинцово, Люберцы, Королёв. "
    "Параметры: сторона А, сторона Б, три на шесть, свободные щиты на месяц, бронь, монтаж, GRP, OTS."
)
DEFAULT_HOTWORDS = (
    "щит щиты щитов щитах билборд билборды суперсайт ситиборд медиафасад 3×6 5×12 6×3 "
    "до от руб рублей тысяч Автозаводская Автозаводской Ленинградка Ленинградке МКАД ТТК ЦКАД "
    "Балашиха Балашихе Химки Химках Мытищи Подольск Красногорск Одинцово Люберцы Королёв "
    "сторона поверхность бронь аренда монтаж экран экраны"
)


class AsrEngine:
    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
    ):
        settings = get_settings()
        self.model_name = model_name or settings.asr_model_name
        self.device = device or settings.asr_device
        self.compute_type = compute_type or settings.asr_compute_type
        self.threads = settings.asr_threads
        self.normalizer = RussianTextNormalizer()
        self._model = None
        self.vocabulary_prompt = self._load_vocabulary_hints()

    def _load_vocabulary_hints(self) -> str:
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        hints_file = os.path.join(base_dir, "config", "vocabulary_hints.yaml")
        if os.path.exists(hints_file):
            try:
                with open(hints_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    prompt_templates = data.get("prompt_templates")
                    if prompt_templates:
                        return " ".join(prompt_templates)

                    terms = data.get("domain_terms", [])
                    cities = data.get("cities", [])
                    highways = data.get("highways", [])
                    metro = data.get("metro_stations", [])
                    price = data.get("price_templates", [])

                    parts = []
                    if terms:
                        parts.append("Наружная реклама: " + ", ".join(terms[:25]) + ".")
                    if price:
                        parts.append("Цены: " + ", ".join(price[:6]) + ".")
                    if metro or highways:
                        locations = metro[:12] + highways[:10] + cities[:8]
                        parts.append("Локации: " + ", ".join(locations) + ".")
                    return " ".join(parts)
            except Exception as e:
                logger.warning("hints_load_failed", error=str(e))
        return DEFAULT_HINTS

    @property
    def model(self):
        if self._model is None:
            self.load_model()
        return self._model

    def load_model(self):
        """Loads faster-whisper (CTranslate2) into memory."""
        try:
            from faster_whisper import WhisperModel

            model_target = self.model_name
            if not os.path.exists(model_target):
                base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                candidate = os.path.join(base_dir, model_target.lstrip("./"))
                if os.path.exists(candidate):
                    model_target = candidate

            logger.info(
                "loading_asr_model",
                model=model_target,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._model = WhisperModel(
                model_target,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.threads,
                num_workers=1,
            )
            logger.info("asr_model_loaded_successfully", model=model_target)
        except Exception as e:
            logger.error("asr_model_load_error", error=str(e))
            raise

    def transcribe(
        self,
        audio: Any,
        language: str = "ru",
        beam_size: int = 2,
        initial_prompt: str | None = None,
        vad_filter: bool = True,
        hotwords: str | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """
        Transcribes audio with faster-whisper with strict anti-hallucination flags.
        Returns (segments, info).
        """
        resolved_lang = None if language == "auto" else (language or "ru")
        prompt = (initial_prompt or "") + (" " + self.vocabulary_prompt if resolved_lang == "ru" else "")
        applied_hotwords = hotwords or (DEFAULT_HOTWORDS if resolved_lang == "ru" else None)

        segments_iter, info = self.model.transcribe(
            audio,
            language=resolved_lang,
            beam_size=beam_size,
            initial_prompt=prompt if prompt.strip() else None,
            hotwords=applied_hotwords,
            vad_filter=vad_filter,
            vad_parameters=dict(
                threshold=0.2,
                min_silence_duration_ms=150,
                speech_pad_ms=250,
            ),
            word_timestamps=True,
            condition_on_previous_text=False,  # CRITICAL: prevents repeating loops
            no_speech_threshold=0.6,
            compression_ratio_threshold=2.4,
            log_prob_threshold=-1.0,
        )

        segments = []
        for s in segments_iter:
            # Word-level details
            words = []
            if s.words:
                for w in s.words:
                    words.append({
                        "word": w.word,
                        "start": round(w.start, 2),
                        "end": round(w.end, 2),
                        "confidence": round(w.probability, 3),
                    })

            # Normalized text
            clean_text = self.normalizer.normalize(s.text.strip())

            segments.append({
                "start": round(s.start, 2),
                "end": round(s.end, 2),
                "text": clean_text,
                "confidence": round(min(1.0, max(0.0, math.exp(s.avg_logprob))), 3) if s.avg_logprob is not None else 0.95,
                "no_speech_prob": round(s.no_speech_prob, 3),
                "words": words,
            })

        meta_info = {
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 2),
        }

        return segments, meta_info


_asr_engine_instance: AsrEngine | None = None
_voice_search_engine_instance: AsrEngine | None = None


def get_asr_engine() -> AsrEngine:
    global _asr_engine_instance
    if _asr_engine_instance is None:
        _asr_engine_instance = AsrEngine()
    return _asr_engine_instance


def get_voice_search_engine() -> AsrEngine:
    global _voice_search_engine_instance
    if _voice_search_engine_instance is None:
        settings = get_settings()
        _voice_search_engine_instance = AsrEngine(
            model_name=settings.voice_search_model_name,
            compute_type="int8",
        )
    return _voice_search_engine_instance
