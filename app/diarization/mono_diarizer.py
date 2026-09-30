import os
from typing import Any

import librosa
import numpy as np
import soundfile as sf
from sklearn.cluster import AgglomerativeClustering

try:
    import onnxruntime as ort
except ImportError:
    ort = None

from app.core.logging import get_logger

logger = get_logger("mono_diarizer")


def split_segment_into_turns(
    segments: list[dict[str, Any]],
    min_pause_sec: float = 0.35,
) -> list[dict[str, Any]]:
    """
    Splits merged Whisper segments into dialogue turns when word timestamps
    indicate a pause or conversational turn transition (question/answer boundary).
    Preserves words, start/end timestamps, and confidence.
    """
    out: list[dict[str, Any]] = []

    for seg in segments:
        words = seg.get("words", [])
        if not words or len(words) <= 1:
            out.append(dict(seg))
            continue

        cur_words = [words[0]]
        for i in range(len(words) - 1):
            w_curr = words[i]
            w_next = words[i + 1]

            pause = max(0.0, w_next["start"] - w_curr["end"])
            text_curr = w_curr["word"].strip()
            has_sentence_end = any(text_curr.endswith(p) for p in [".", "?", "!", "..."])

            # Split on noticeable inter-word pause OR sentence punctuation with a small pause
            if pause >= min_pause_sec or (has_sentence_end and pause >= 0.20):
                turn_text = "".join(w["word"] for w in cur_words).strip()
                out.append(
                    {
                        "start": cur_words[0]["start"],
                        "end": cur_words[-1]["end"],
                        "text": turn_text,
                        "words": list(cur_words),
                        "confidence": float(np.mean([w.get("confidence", 0.9) for w in cur_words])),
                    }
                )
                cur_words = [w_next]
            else:
                cur_words.append(w_next)

        if cur_words:
            turn_text = "".join(w["word"] for w in cur_words).strip()
            out.append(
                {
                    "start": cur_words[0]["start"],
                    "end": cur_words[-1]["end"],
                    "text": turn_text,
                    "words": list(cur_words),
                    "confidence": float(np.mean([w.get("confidence", 0.9) for w in cur_words])),
                }
            )

    return out


class MonoDiarizer:
    def __init__(
        self,
        num_speakers: int = 2,
        min_speaker_distance: float = 0.08,
        campplus_path: str = "models/campplus.onnx",
    ):
        self.num_speakers = num_speakers
        self.min_speaker_distance = min_speaker_distance
        self.campplus_session = None

        # Check candidate locations for CAM++ ONNX model
        candidates = [
            campplus_path,
            os.path.join(os.path.dirname(__file__), "../../models/campplus.onnx"),
            "/app/models/campplus.onnx",
        ]
        if ort:
            for p in candidates:
                if os.path.exists(p):
                    try:
                        self.campplus_session = ort.InferenceSession(
                            p, providers=["CPUExecutionProvider"]
                        )
                        logger.info("campplus_model_loaded", path=p)
                        break
                    except Exception as e:
                        logger.warning("campplus_load_failed", path=p, error=str(e))

    def _load_audio_mono(self, audio_path: str, target_sr: int = 16000) -> np.ndarray:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

        data, sr = sf.read(audio_path, dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        if sr != target_sr:
            data = librosa.resample(data, orig_sr=sr, target_sr=target_sr)

        return data

    def _extract_campplus(self, audio: np.ndarray, sr: int = 16000) -> np.ndarray | None:
        """Extracts 192-dimensional CAM++ neural d-vector embedding."""
        if self.campplus_session is None or len(audio) < 400:
            return None
        try:
            mel = librosa.feature.melspectrogram(
                y=audio,
                sr=sr,
                n_fft=512,
                win_length=400,
                hop_length=160,
                n_mels=80,
                fmin=20.0,
                fmax=7600.0,
                window="hamming",
                center=False,
            )
            fbank = np.log(np.maximum(mel, 1e-5)).T
            fbank = fbank - np.mean(fbank, axis=0, keepdims=True)
            feats = np.expand_dims(fbank.astype(np.float32), axis=0)
            emb = self.campplus_session.run(None, {"feats": feats})[0][0]
            norm = np.linalg.norm(emb)
            return emb / max(norm, 1e-6)
        except Exception as e:
            logger.debug("campplus_inference_error", error=str(e))
            return None

    def _extract_segment_features(self, segment_audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        """Acoustic feature extractor fallback: pitch (F0), RMS, spectral centroid/rolloff, and MFCCs."""
        if len(segment_audio) < 512:
            segment_audio = np.pad(segment_audio, (0, max(0, 512 - len(segment_audio))))

        # 1. Pitch F0 autocorrelation
        def get_pitch(frame: np.ndarray) -> float:
            n = len(frame)
            n_fft = 1 << (2 * n - 1).bit_length()
            fy = np.fft.rfft(frame, n=n_fft)
            corr = np.fft.irfft(fy * np.conj(fy))[:n]
            min_lag = int(sr / 350.0)
            max_lag = int(sr / 65.0)
            if max_lag >= len(corr) or min_lag >= max_lag:
                return 0.0
            peak = min_lag + int(np.argmax(corr[min_lag:max_lag]))
            return float(sr / peak) if corr[peak] > 0.25 * corr[0] else 0.0

        pitches = []
        frame_len = int(sr * 0.06)
        hop = int(sr * 0.03)
        if len(segment_audio) >= frame_len:
            for st in range(0, len(segment_audio) - frame_len, hop):
                p = get_pitch(segment_audio[st : st + frame_len])
                if p > 0:
                    pitches.append(p)

        pitch_med = float(np.median(pitches)) if pitches else 0.0
        pitch_std = float(np.std(pitches)) if len(pitches) > 1 else 0.0
        rms = float(np.sqrt(np.mean(segment_audio**2)))

        mfcc = librosa.feature.mfcc(y=segment_audio, sr=sr, n_mfcc=13)
        mfcc_mean = np.mean(mfcc, axis=1)
        mfcc_std = np.std(mfcc, axis=1)

        centroid = float(np.mean(librosa.feature.spectral_centroid(y=segment_audio, sr=sr)))
        rolloff = float(np.mean(librosa.feature.spectral_rolloff(y=segment_audio, sr=sr)))

        return np.concatenate(
            [
                [pitch_med, pitch_std, rms, centroid, rolloff],
                mfcc_mean,
                mfcc_std,
            ]
        )

    def diarize(self, audio_path: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Diarizes mono audio:
        1. Splits merged Whisper segments into dialogue turns using word pause gaps.
        2. Extracts neural CAM++ embeddings (or acoustic features fallback).
        3. Clusters turns with Agglomerative Clustering.
        4. Smooths isolated short turns and assigns 'speaker_label' (SPEAKER_0, SPEAKER_1).
        """
        if not segments:
            return segments

        # 1. Turn splitting
        turns = split_segment_into_turns(segments)

        try:
            y = self._load_audio_mono(audio_path, target_sr=16000)
            sr = 16000
        except Exception as e:
            logger.error("audio_load_failed", error=str(e))
            for i, s in enumerate(turns):
                s["speaker_label"] = f"SPEAKER_{i % 2}"
            return turns

        cam_embs = []
        acoustics = []
        valid_indices = []

        for i, t in enumerate(turns):
            start_sample = max(0, min(int(t["start"] * sr), len(y) - 1))
            end_sample = max(0, min(int(t["end"] * sr), len(y)))

            if end_sample <= start_sample:
                continue

            chunk = y[start_sample:end_sample]
            c_emb = self._extract_campplus(chunk, sr)
            a_feat = self._extract_segment_features(chunk, sr)

            cam_embs.append(c_emb)
            acoustics.append(a_feat)
            valid_indices.append(i)

        if not valid_indices:
            for t in turns:
                t.setdefault("speaker_label", "SPEAKER_0")
            return turns

        # Determine representation: CAM++ if available for all turns, else standardized acoustic features
        has_all_cam = all(e is not None for e in cam_embs)
        if has_all_cam and len(cam_embs) >= 2:
            X = np.array(cam_embs)
            use_cosine = True
        else:
            X_raw = np.array(acoustics)
            std = np.std(X_raw, axis=0)
            std[std == 0] = 1.0
            X = (X_raw - np.mean(X_raw, axis=0)) / std
            use_cosine = False

        if len(X) < 2 or self.num_speakers < 2:
            labels = np.zeros(len(X), dtype=int)
        elif len(X) == 2:
            # For 2-turn conversations, check distance
            if use_cosine:
                dist = 1.0 - float(np.dot(X[0], X[1]))
                thresh = 0.12
            else:
                dist = float(np.linalg.norm(X[0] - X[1]))
                thresh = 1.2
            labels = np.array([0, 1]) if dist >= thresh else np.array([0, 0])
        else:
            metric = "cosine" if use_cosine else "euclidean"
            linkage = "average" if use_cosine else "ward"
            clus = AgglomerativeClustering(
                n_clusters=min(self.num_speakers, len(X)),
                metric=metric,
                linkage=linkage,
            )
            labels = clus.fit_predict(X)

            # Monologue validation on standardized cluster centroids
            mask0 = labels == 0
            mask1 = labels == 1
            if np.any(mask0) and np.any(mask1):
                c0 = np.mean(X[mask0], axis=0)
                c1 = np.mean(X[mask1], axis=0)
                if use_cosine:
                    norm0, norm1 = np.linalg.norm(c0), np.linalg.norm(c1)
                    sep = (
                        1.0 - float(np.dot(c0, c1) / (norm0 * norm1))
                        if norm0 > 0 and norm1 > 0
                        else 0.0
                    )
                    thresh = self.min_speaker_distance
                else:
                    sep = float(np.linalg.norm(c0 - c1))
                    thresh = 1.4
                if sep < thresh:
                    labels = np.zeros(len(X), dtype=int)

            # Temporal continuity smoothing
            for i in range(1, len(valid_indices) - 1):
                prev_idx = valid_indices[i - 1]
                curr_idx = valid_indices[i]
                dur = turns[curr_idx].get("end", 0.0) - turns[curr_idx].get("start", 0.0)
                gap_prev = turns[curr_idx].get("start", 0.0) - turns[prev_idx].get("end", 0.0)

                if (
                    dur < 0.8
                    and gap_prev < 0.5
                    and labels[i - 1] == labels[i + 1]
                    and labels[i] != labels[i - 1]
                ):
                    labels[i] = labels[i - 1]

        for valid_idx, label in zip(valid_indices, labels):
            turns[valid_idx]["speaker_label"] = f"SPEAKER_{label}"

        for t in turns:
            if "speaker_label" not in t:
                t["speaker_label"] = "SPEAKER_0"

        return turns
