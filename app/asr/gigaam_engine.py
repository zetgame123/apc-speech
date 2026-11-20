"""Локальный ASR-движок на GigaAM-v3 E2E CTC (SaluteDevices, MIT) через onnx-asr.

Особенности:
- Conformer-CTC (~220-240M параметров, INT8 ONNX ~240 МБ) без авторегрессионного декодера:
  в 5-10x быстрее Whisper на CPU, не зацикливается на телефонии 8 кГц.
- Вариант `gigaam-v3-e2e-ctc` включает встроенную пунктуацию, регистр и нормализацию.
- Для длинных записей (>25 с) аудио разбивается на речевые фрагменты по паузам (RMS VAD),
  чтобы не превышать окно внимания Conformer и держать низкий пик RAM.
- Для каждого фрагмента восстанавливаются пословные таймкоды из SentencePiece-токенов.
"""

from __future__ import annotations

import io
import math
import os
import wave
from typing import Any

import numpy as np
import onnxruntime as ort
import soundfile as sf

from app.asr.normalizer import get_normalizer
from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger("gigaam_engine")

TARGET_SAMPLE_RATE = 16000
MAX_UTTERANCE_SEC = 22.0
MIN_SILENCE_SEC = 0.35
SPEECH_PAD_SEC = 0.15


def _load_audio_16k_mono(audio: Any) -> tuple[np.ndarray, float]:
    """Loads audio from path, bytes, BytesIO, or numpy float32 array into 16kHz mono float32."""
    if isinstance(audio, np.ndarray):
        arr = audio.astype(np.float32, copy=False)
        if arr.ndim > 1:
            arr = arr.mean(axis=-1).astype(np.float32)
        return arr, len(arr) / TARGET_SAMPLE_RATE

    raw_bytes: bytes | None = None
    if isinstance(audio, (bytes, bytearray)):
        raw_bytes = bytes(audio)
    elif isinstance(audio, io.BytesIO):
        pos = audio.tell()
        raw_bytes = audio.read()
        audio.seek(pos)
    elif isinstance(audio, (str, os.PathLike)):
        data, sr = sf.read(str(audio), dtype="float32", always_2d=False)
        return _normalize_waveform(data, sr)
    else:
        raise TypeError(f"Unsupported audio input type: {type(audio).__name__}")

    # Fast path for 16kHz mono PCM16 WAV (avoids ffmpeg subprocess)
    if len(raw_bytes) >= 44 and raw_bytes[:4] == b"RIFF" and raw_bytes[8:12] == b"WAVE":
        try:
            with wave.open(io.BytesIO(raw_bytes), "rb") as wf:
                if wf.getsampwidth() == 2 and wf.getnchannels() == 1:
                    sr = wf.getframerate()
                    pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
                    arr = pcm.astype(np.float32) / 32768.0
                    return _normalize_waveform(arr, sr)
        except Exception:
            pass

    # Try soundfile directly from memory (WAV/FLAC/OGG)
    try:
        data, sr = sf.read(io.BytesIO(raw_bytes), dtype="float32", always_2d=False)
        return _normalize_waveform(data, sr)
    except Exception:
        # Fallback to ffmpeg preprocessor for MP3/M4A/WebM/GSM/G.711
        from app.asr.preprocessor import AudioPreprocessor

        clean_wav = AudioPreprocessor().preprocess_memory(raw_bytes)
        data, sr = sf.read(io.BytesIO(clean_wav), dtype="float32", always_2d=False)
        return _normalize_waveform(data, sr)


def _normalize_waveform(data: np.ndarray, sr: int) -> tuple[np.ndarray, float]:
    arr = np.asarray(data, dtype=np.float32)
    if arr.ndim > 1:
        arr = arr.mean(axis=-1).astype(np.float32)
    if sr != TARGET_SAMPLE_RATE and len(arr) > 0:
        # Linear resampling is lightweight and dependency-free for 8kHz -> 16kHz telephony
        new_len = max(1, int(round(len(arr) * TARGET_SAMPLE_RATE / sr)))
        x_old = np.linspace(0.0, 1.0, len(arr), endpoint=False)
        x_new = np.linspace(0.0, 1.0, new_len, endpoint=False)
        arr = np.interp(x_new, x_old, arr).astype(np.float32)
    return arr, len(arr) / TARGET_SAMPLE_RATE


def _split_long_waveform(
    waveform: np.ndarray,
    sr: int = TARGET_SAMPLE_RATE,
    max_sec: float = MAX_UTTERANCE_SEC,
) -> list[tuple[float, float, np.ndarray]]:
    """Splits long audio into <=max_sec speech chunks along low-energy silence valleys."""
    total_sec = len(waveform) / sr
    if total_sec <= max_sec:
        return [(0.0, total_sec, waveform)]

    frame_len = int(0.02 * sr)  # 20ms frames
    n_frames = len(waveform) // frame_len
    if n_frames == 0:
        return [(0.0, total_sec, waveform)]

    frames = waveform[: n_frames * frame_len].reshape(n_frames, frame_len)
    rms = np.sqrt(np.mean(frames * frames, axis=1) + 1e-12)
    # Adaptive silence threshold relative to signal energy
    noise_floor = float(np.percentile(rms, 15))
    peak_level = float(np.percentile(rms, 95))
    thresh = max(noise_floor * 2.5, peak_level * 0.06, 1e-4)

    is_speech = rms > thresh
    min_sil_frames = max(1, int(MIN_SILENCE_SEC / 0.02))
    pad_frames = max(1, int(SPEECH_PAD_SEC / 0.02))
    max_frames = int(max_sec / 0.02)

    # Group contiguous speech regions separated by >= min_sil_frames
    raw_spans: list[tuple[int, int]] = []
    start: int | None = None
    sil_run = 0
    for i, sp in enumerate(is_speech):
        if sp:
            if start is None:
                start = i
            sil_run = 0
        else:
            if start is not None:
                sil_run += 1
                if sil_run >= min_sil_frames:
                    raw_spans.append((start, i - sil_run + 1))
                    start = None
                    sil_run = 0
    if start is not None:
        raw_spans.append((start, n_frames))

    if not raw_spans:
        # Pure silence or constant low energy -> split evenly
        chunks = []
        step = int(max_sec * sr)
        for pos in range(0, len(waveform), step):
            end_pos = min(len(waveform), pos + step)
            chunks.append((pos / sr, end_pos / sr, waveform[pos:end_pos]))
        return chunks

    # Merge adjacent short spans up to max_frames and split overly long spans at lowest RMS
    chunks: list[tuple[float, float, np.ndarray]] = []
    cur_s, cur_e = raw_spans[0]
    merged: list[tuple[int, int]] = []
    for s, e in raw_spans[1:]:
        if e - cur_s <= max_frames:
            cur_e = e
        else:
            merged.append((cur_s, cur_e))
            cur_s, cur_e = s, e
    merged.append((cur_s, cur_e))

    for s, e in merged:
        s_pad = max(0, s - pad_frames)
        e_pad = min(n_frames, e + pad_frames)
        while e_pad - s_pad > max_frames:
            # Find quietest frame in [s_pad + 0.6*max_frames, s_pad + max_frames]
            search_lo = s_pad + int(max_frames * 0.6)
            search_hi = s_pad + max_frames
            cut = search_lo + int(np.argmin(rms[search_lo:search_hi]))
            chunks.append(
                (
                    s_pad * 0.02,
                    cut * 0.02,
                    waveform[s_pad * frame_len : cut * frame_len],
                )
            )
            s_pad = cut
        if e_pad > s_pad:
            chunks.append(
                (
                    s_pad * 0.02,
                    e_pad * 0.02,
                    waveform[s_pad * frame_len : e_pad * frame_len],
                )
            )
    return chunks


def _tokens_to_words(
    tokens: list[str] | None,
    timestamps: list[float] | None,
    logprobs: list[float] | None,
    offset_sec: float,
    chunk_end_sec: float,
) -> tuple[list[dict[str, Any]], float]:
    """Reconstructs word-level timestamps and average confidence from SentencePiece tokens."""
    if not tokens or not timestamps:
        return [], 0.95

    words: list[dict[str, Any]] = []
    cur_chars: list[str] = []
    cur_start: float | None = None
    cur_end: float = offset_sec
    cur_lps: list[float] = []
    all_lps: list[float] = []

    n = min(len(tokens), len(timestamps))
    for i in range(n):
        tok = tokens[i]
        ts = offset_sec + float(timestamps[i])
        lp = float(logprobs[i]) if (logprobs and i < len(logprobs)) else -0.05
        all_lps.append(lp)

        starts_word = tok.startswith("▁") or tok.startswith(" ")
        clean_tok = tok.lstrip("▁ ")
        if starts_word and cur_chars:
            word_str = "".join(cur_chars).strip()
            if word_str:
                avg_lp = sum(cur_lps) / len(cur_lps) if cur_lps else -0.05
                words.append(
                    {
                        "word": word_str,
                        "start": round(cur_start if cur_start is not None else ts, 2),
                        "end": round(max(cur_end, ts), 2),
                        "confidence": round(min(1.0, max(0.0, math.exp(avg_lp))), 3),
                    }
                )
            cur_chars = []
            cur_start = None
            cur_lps = []

        if clean_tok:
            if cur_start is None:
                cur_start = ts
            cur_chars.append(clean_tok)
            cur_end = ts + 0.08
            cur_lps.append(lp)

    if cur_chars:
        word_str = "".join(cur_chars).strip()
        if word_str:
            avg_lp = sum(cur_lps) / len(cur_lps) if cur_lps else -0.05
            words.append(
                {
                    "word": word_str,
                    "start": round(cur_start if cur_start is not None else offset_sec, 2),
                    "end": round(
                        min(chunk_end_sec, max(cur_end, (cur_start or offset_sec) + 0.1)), 2
                    ),
                    "confidence": round(min(1.0, max(0.0, math.exp(avg_lp))), 3),
                }
            )

    mean_lp = sum(all_lps) / len(all_lps) if all_lps else -0.05
    overall_conf = round(min(1.0, max(0.0, math.exp(mean_lp))), 3)
    return words, overall_conf


class GigaamEngine:
    """Fast local Russian ASR engine powered by GigaAM-v3 E2E CTC INT8 ONNX."""

    def __init__(
        self,
        model_dir: str | None = None,
        quantization: str | None = None,
        threads: int | None = None,
    ):
        settings = get_settings()
        self.model_dir = model_dir or settings.gigaam_model_dir
        self.quantization = (
            quantization if quantization is not None else settings.gigaam_quantization
        )
        self.threads = threads if threads is not None else settings.gigaam_threads
        self._model = None

    def _resolve_model_dir(self) -> str:
        if os.path.isdir(self.model_dir):
            return self.model_dir
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        candidate = os.path.join(base_dir, self.model_dir.lstrip("./"))
        if os.path.isdir(candidate):
            return candidate
        raise FileNotFoundError(
            f"GigaAM ONNX model directory not found: {self.model_dir} (run scripts/download_models.py)"
        )

    def load_model(self) -> None:
        if self._model is not None:
            return
        import onnx_asr

        resolved_dir = self._resolve_model_dir()
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        if self.threads > 0:
            sess_opts.intra_op_num_threads = self.threads
            sess_opts.inter_op_num_threads = 1

        logger.info(
            "loading_gigaam_model",
            model_dir=resolved_dir,
            quantization=self.quantization,
            threads=self.threads,
        )
        quant = self.quantization if self.quantization else None
        self._model = onnx_asr.load_model(
            "gigaam-v3-e2e-ctc",
            path=resolved_dir,
            quantization=quant,
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        ).with_timestamps()
        logger.info("gigaam_model_loaded_successfully", quantization=self.quantization)

    @property
    def model(self):
        if self._model is None:
            self.load_model()
        return self._model

    def transcribe(
        self,
        audio: Any,
        language: str = "ru",
        word_timestamps: bool = True,
        **kwargs: Any,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Transcribes audio and returns (segments, metadata) matching AsrEngine contract."""
        waveform, total_duration = _load_audio_16k_mono(audio)
        if len(waveform) == 0:
            return [], {"language": "ru", "language_probability": 1.0, "duration": 0.0}

        chunks = _split_long_waveform(waveform, TARGET_SAMPLE_RATE, MAX_UTTERANCE_SEC)
        normalizer = get_normalizer()
        segments: list[dict[str, Any]] = []

        for chunk_start, chunk_end, chunk_wav in chunks:
            if len(chunk_wav) < int(0.08 * TARGET_SAMPLE_RATE):
                continue
            res = self.model.recognize(chunk_wav, sample_rate=TARGET_SAMPLE_RATE)
            raw_text = (res.text or "").strip()
            if not raw_text:
                continue

            clean_text = normalizer.normalize(raw_text)
            words, conf = _tokens_to_words(
                res.tokens,
                res.timestamps,
                res.logprobs,
                offset_sec=chunk_start,
                chunk_end_sec=chunk_end,
            )
            seg_start = words[0]["start"] if words else round(chunk_start, 2)
            seg_end = words[-1]["end"] if words else round(chunk_end, 2)

            segments.append(
                {
                    "start": seg_start,
                    "end": seg_end,
                    "text": clean_text,
                    "confidence": conf,
                    "no_speech_prob": 0.0,
                    "words": words if word_timestamps else [],
                }
            )

        meta_info = {
            "language": "ru",
            "language_probability": 0.99,
            "duration": round(total_duration, 2),
        }
        return segments, meta_info


_gigaam_engine_instance: GigaamEngine | None = None


def get_gigaam_engine() -> GigaamEngine:
    global _gigaam_engine_instance
    if _gigaam_engine_instance is None:
        _gigaam_engine_instance = GigaamEngine()
    return _gigaam_engine_instance
