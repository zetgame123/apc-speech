import io
import os
import yaml
from typing import Any, Dict, List, Optional, Tuple, Union
import numpy as np

from app.core.config import get_settings
from app.core.logging import get_logger
from app.asr.normalizer import RussianTextNormalizer

logger = get_logger("asr_engine")

DEFAULT_HINTS = (
    "Разговор о наружной рекламе: щит, суперсайт, ситиборд, сити-формат, пиллар, "
    "призматрон, брандмауэр, медиафасад, экран, диджитал, сторона А, сторона Б, "
    "3х6, 6х3, GRP, OTS, монтаж, демонтаж, печать, бронь, Балашиха, Химки, Мытищи, "
    "Подольск, Одинцово, Красногорск."
)


class AsrEngine:
    def __init__(
        self,
        model_name: Optional[str] = None,
        device: Optional[str] = None,
        compute_type: Optional[str] = None,
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
        hints_file = "config/vocabulary_hints.yaml"
        if os.path.exists(hints_file):
            try:
                with open(hints_file, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    terms = data.get("domain_terms", [])
                    cities = data.get("cities", [])
                    all_hints = terms + cities
                    return "Термины наружной рекламы: " + ", ".join(all_hints) + "."
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
            logger.info(
                "loading_asr_model",
                model=self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )
            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=self.threads,
                num_workers=1,
            )
            logger.info("asr_model_loaded_successfully", model=self.model_name)
        except Exception as e:
            logger.error("asr_model_load_error", error=str(e))
            raise

    def transcribe(
        self,
        audio: Union[str, np.ndarray],
        language: str = "ru",
        beam_size: int = 2,
        initial_prompt: Optional[str] = None,
        vad_filter: bool = True,
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Transcribes audio with faster-whisper with strict anti-hallucination flags.
        Returns (segments, info).
        """
        prompt = (initial_prompt or "") + " " + self.vocabulary_prompt

        segments_iter, info = self.model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            initial_prompt=prompt,
            vad_filter=vad_filter,
            vad_parameters=dict(
                threshold=0.5,
                min_silence_duration_ms=400,
                speech_pad_ms=200,
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
                "confidence": round(s.avg_logprob, 3),
                "no_speech_prob": round(s.no_speech_prob, 3),
                "words": words,
            })

        meta_info = {
            "language": info.language,
            "language_probability": round(info.language_probability, 3),
            "duration": round(info.duration, 2),
        }

        return segments, meta_info


_asr_engine_instance: Optional[AsrEngine] = None


def get_asr_engine() -> AsrEngine:
    global _asr_engine_instance
    if _asr_engine_instance is None:
        _asr_engine_instance = AsrEngine()
    return _asr_engine_instance
