"""Экспериментальный движок быстрого пути на T-one (t-tech, Apache 2.0).

Faithful-порт инференс-рантайма T-one (чанкинг, состояние, logprob-сплиттер,
logprob-сплиттер, greedy-декодер) на имеющихся onnxruntime+numpy — без
зависимости от PyPI-пакета `tone` (у него пустые Requires-Dist и безусловный
import torch). Проверен посимвольным совпадением с эталоном.

Включается только VOICE_SEARCH_ENGINE=tone (дефолт whisper). Выход —
строчные без пунктуации (алфавит CTC: 33 буквы + пробел).
"""

import io
import os
import wave
from dataclasses import dataclass, field
from itertools import groupby

import numpy as np

from app.asr.normalizer import RussianTextNormalizer
from app.core.logging import get_logger

logger = get_logger("tone_engine")

LABELS = "абвгдеёжзийклмнопрстуфхцчшщъыьэюя "
BLANK_ID = len(LABELS)

SAMPLE_RATE = 8000
CHUNK_SAMPLES = 2400  # 300 мс @ 8 кГц
PADDING_SAMPLES = 2400
STATE_SIZE = 219729
FRAME_SIZE_SEC = 0.03
MEAN_TIME_BIAS_SEC = 0.33

SILENCE_THRESHOLD = 0.9
MIN_SILENCE_FRAMES = 20
SPEECH_EXPAND_FRAMES = 3
MAX_PHRASE_FRAMES = 2000


@dataclass
class _SplitterState:
    past_logprobs: np.ndarray = field(
        default_factory=lambda: np.zeros((0, BLANK_ID + 1), dtype=np.float32)
    )
    offset: int = 0


def _greedy_decode(logprobs: np.ndarray) -> tuple[str, float, float]:
    """Жадный CTC-декодер. Возвращает (текст, уверенность, доля тишины).

    Уверенность — средний max-posterior по непустым кадрам; доля тишины —
    средняя P(space)+P(blank) по фреймам фразы. Честные прокси без posterior-скоров.
    """
    tokens = logprobs.argmax(axis=-1).tolist()
    collapsed = [t for t, _ in groupby(tokens)]
    chars = [LABELS[t] for t in collapsed if t < len(LABELS)]
    probs = np.exp(logprobs[np.arange(len(logprobs)), tokens])
    mask = np.array(tokens) != BLANK_ID
    confidence = float(probs[mask].mean()) if mask.any() else 0.0
    silence_share = float(np.exp(logprobs[:, -2:]).sum(axis=-1).mean())
    return "".join(chars).strip(), confidence, silence_share


def _split_phrases(is_speech: np.ndarray, *, is_last: bool) -> list[tuple[int, int]]:
    speech_len = len(is_speech)
    is_speech = np.pad(is_speech, (MIN_SILENCE_FRAMES, MIN_SILENCE_FRAMES if is_last else 0))
    changes = np.diff(np.pad(~is_speech, (1, 1)).astype(np.int32))
    starts = (changes == 1).nonzero()[0] - MIN_SILENCE_FRAMES
    ends = (changes == -1).nonzero()[0] - MIN_SILENCE_FRAMES
    durations = ends - starts
    keep = durations >= MIN_SILENCE_FRAMES
    assert keep[0], "первая тишина обязана быть разделителем"
    starts, ends = starts[keep], ends[keep]
    out: list[tuple[int, int]] = []
    speech_starts, speech_ends = ends.tolist(), starts.tolist()[1:] + [speech_len]
    for i, (s, e) in enumerate(zip(speech_starts, speech_ends)):
        while e - s >= MAX_PHRASE_FRAMES:
            out.append((s, s + MAX_PHRASE_FRAMES))
            s += MAX_PHRASE_FRAMES
        if i < len(ends) - 1:
            out.append((s, e))
    return out


class ToneEngine:
    """CTC-движок T-one (greedy). Интерфейс как у AsrEngine.transcribe."""

    def __init__(
        self,
        model_path: str,
        intra_threads: int = 0,
        inter_threads: int = 0,
        graph_opt: str = "all",
        disable_arena: bool = False,
        disable_mem_pattern: bool = False,
    ):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        if intra_threads > 0:
            opts.intra_op_num_threads = intra_threads
            opts.inter_op_num_threads = inter_threads if inter_threads > 0 else 1
        levels = {
            "disabled": ort.GraphOptimizationLevel.ORT_DISABLE_ALL,
            "basic": ort.GraphOptimizationLevel.ORT_ENABLE_BASIC,
            "extended": ort.GraphOptimizationLevel.ORT_ENABLE_EXTENDED,
            "all": ort.GraphOptimizationLevel.ORT_ENABLE_ALL,
        }
        if graph_opt not in levels:
            raise ValueError(f"TONE_GRAPH_OPT: {graph_opt!r}, допустимо {sorted(levels)}")
        opts.graph_optimization_level = levels[graph_opt]
        if disable_arena:
            opts.enable_cpu_mem_arena = False
        if disable_mem_pattern:
            opts.enable_mem_pattern = False
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"веса T-one не найдены: {model_path} "
                "(скачиваются model-downloader в models/t-one/model.onnx)"
            )
        logger.info(
            "loading_tone_model",
            model=model_path,
            intra_threads=intra_threads,
            graph_opt=graph_opt,
        )
        self._sess = ort.InferenceSession(model_path, sess_options=opts)
        self._normalizer = RussianTextNormalizer()
        logger.info("tone_model_loaded_successfully", model=model_path)

    def load_model(self) -> None:
        return None

    def _wav_to_8k(self, audio_bytes: bytes) -> np.ndarray:
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as w:
                rate, channels, width, n = (
                    w.getframerate(),
                    w.getnchannels(),
                    w.getsampwidth(),
                    w.getnframes(),
                )
                raw = w.readframes(n)
        except Exception as e:
            raise ValueError(f"T-one принимает только PCM WAV: {e}") from e
        if width != 2:
            raise ValueError(f"T-one: только 16-бит PCM, получено {width * 8} бит")
        samples = np.frombuffer(raw, dtype=np.int16)
        if channels > 1:
            samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
        if rate == SAMPLE_RATE:
            return samples.astype(np.int32)
        if rate == 16000:
            # Наивная децимация: телефония и так ограничена ~4 кГц.
            return samples[::2].astype(np.int32)
        raise ValueError(f"T-one: частота {rate} Гц не поддерживается (нужны 8000/16000)")

    def transcribe(
        self,
        audio,
        language: str = "ru",
        beam_size: int = 1,
        initial_prompt=None,
        hotwords=None,
        vad_filter: bool = True,
        vad_parameters: dict | None = None,
        word_timestamps: bool = False,
        **kwargs,
    ) -> tuple[list[dict], dict[str, float | str]]:
        if hasattr(audio, "read"):
            audio_bytes = audio.read()
        elif isinstance(audio, (bytes, bytearray)):
            audio_bytes = bytes(audio_bytes := audio)
        else:
            audio_bytes = np.asarray(audio).tobytes()
        samples = self._wav_to_8k(audio_bytes)
        duration_sec = len(samples) / SAMPLE_RATE

        padded = np.pad(samples, (PADDING_SAMPLES, PADDING_SAMPLES)).astype(np.int32)
        padded = np.pad(padded, (0, -len(padded) % CHUNK_SAMPLES))
        chunks = np.split(padded, len(padded) // CHUNK_SAMPLES)

        model_state: np.ndarray | None = None
        splitter = _SplitterState()
        segments: list[dict] = []
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            if model_state is None:
                model_state = np.zeros((1, STATE_SIZE), dtype=np.float16)
            logprobs, model_state = self._sess.run(
                None, {"signal": chunk[None, :, None], "state": model_state}
            )
            frames = np.asarray(logprobs[0], dtype=np.float32)
            combined = np.concatenate((splitter.past_logprobs, frames), axis=0)
            is_speech = np.exp(combined[..., -2:]).sum(axis=-1) <= SILENCE_THRESHOLD
            last_end = 0
            for start, end in _split_phrases(is_speech, is_last=is_last):
                piece = combined[max(0, start - SPEECH_EXPAND_FRAMES) : end + SPEECH_EXPAND_FRAMES]
                text, conf, silence = _greedy_decode(piece)
                if not text:
                    continue
                seg_start = max(
                    0.0,
                    round(
                        (start + splitter.offset) * FRAME_SIZE_SEC
                        - MEAN_TIME_BIAS_SEC
                        - PADDING_SAMPLES / SAMPLE_RATE,
                        2,
                    ),
                )
                seg_end = max(
                    seg_start,
                    round(
                        (end + splitter.offset) * FRAME_SIZE_SEC
                        - MEAN_TIME_BIAS_SEC
                        - PADDING_SAMPLES / SAMPLE_RATE,
                        2,
                    ),
                )
                segments.append(
                    {
                        "start": seg_start,
                        "end": seg_end,
                        "text": self._normalizer.normalize(text),
                        "confidence": round(min(1.0, max(0.0, conf)), 3),
                        "no_speech_prob": round(min(1.0, max(0.0, silence)), 3),
                        "words": [],
                    }
                )
                last_end = end
            tail = is_speech[last_end:]
            speech_ids = np.nonzero(tail)[0]
            if not len(speech_ids):
                last_end = max(last_end, len(combined) - SPEECH_EXPAND_FRAMES)
            splitter = _SplitterState(
                past_logprobs=combined[last_end:], offset=splitter.offset + last_end
            )

        meta = {
            "language": "ru",
            # Заглушка: CTC-greedy не оценивает язык; быстрый путь всегда русский.
            "language_probability": 1.0,
            "duration": round(duration_sec, 2),
        }
        return segments, meta


_tone_engine_instance: ToneEngine | None = None


def get_tone_engine() -> ToneEngine:
    global _tone_engine_instance
    if _tone_engine_instance is None:
        from app.core.config import get_settings

        settings = get_settings()
        model_path = os.path.join(settings.tone_model_dir, settings.tone_model_file)
        _tone_engine_instance = ToneEngine(
            model_path,
            intra_threads=settings.tone_threads,
            inter_threads=settings.tone_inter_threads,
            graph_opt=settings.tone_graph_opt,
            disable_arena=settings.tone_disable_arena,
            disable_mem_pattern=settings.tone_disable_mem_pattern,
        )
    return _tone_engine_instance
