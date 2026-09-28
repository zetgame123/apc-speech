import os
from typing import Any

import librosa
import numpy as np
import soundfile as sf
from sklearn.cluster import AgglomerativeClustering

from app.core.logging import get_logger

logger = get_logger("mono_diarizer")


class MonoDiarizer:
    def __init__(self, num_speakers: int = 2, min_speaker_distance: float = 0.005):
        self.num_speakers = num_speakers
        self.min_speaker_distance = min_speaker_distance

    def _load_audio_mono(self, audio_path: str, target_sr: int = 16000) -> np.ndarray:
        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"Audio file does not exist: {audio_path}")

        data, sr = sf.read(audio_path, dtype="float32")
        if data.ndim > 1:
            data = np.mean(data, axis=1)

        if sr != target_sr:
            data = librosa.resample(data, orig_sr=sr, target_sr=target_sr)

        return data

    def _extract_segment_features(self, segment_audio: np.ndarray, sr: int = 16000) -> np.ndarray:
        if len(segment_audio) < 512:
            segment_audio = np.pad(segment_audio, (0, max(0, 512 - len(segment_audio))))

        # 1. MFCC + Delta + Delta-Delta (timbre and speech dynamics)
        mfcc = librosa.feature.mfcc(y=segment_audio, sr=sr, n_mfcc=20)
        mfcc_delta = librosa.feature.delta(mfcc)
        mfcc_delta2 = librosa.feature.delta(mfcc, order=2)

        mfcc_stats = np.concatenate([
            np.mean(mfcc, axis=1),
            np.std(mfcc, axis=1),
            np.mean(mfcc_delta, axis=1),
            np.mean(mfcc_delta2, axis=1),
        ])

        # 2. Spectral features (brightness, bandwidth, energy distribution)
        centroid = librosa.feature.spectral_centroid(y=segment_audio, sr=sr)
        rolloff = librosa.feature.spectral_rolloff(y=segment_audio, sr=sr)
        zcr = librosa.feature.zero_crossing_rate(y=segment_audio)

        spectral_stats = np.array([
            np.mean(centroid),
            np.std(centroid),
            np.mean(rolloff),
            np.std(rolloff),
            np.mean(zcr),
        ])

        # 3. Fundamental frequency (F0/pitch) via fast FFT autocorrelation
        def estimate_pitch_fft(frame: np.ndarray) -> float:
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

        frame_len = int(sr * 0.06)
        hop = int(sr * 0.03)
        pitches = []
        if len(segment_audio) >= frame_len:
            for st in range(0, len(segment_audio) - frame_len, hop):
                p = estimate_pitch_fft(segment_audio[st : st + frame_len])
                if p > 0:
                    pitches.append(p)

        pitch_med = float(np.median(pitches)) if pitches else 0.0
        pitch_std = float(np.std(pitches)) if len(pitches) > 1 else 0.0
        num_frames = max(1, (len(segment_audio) - frame_len) // hop)
        voiced_ratio = len(pitches) / num_frames if len(segment_audio) >= frame_len else 0.0

        pitch_stats = np.array([pitch_med, pitch_std, voiced_ratio])

        return np.concatenate([mfcc_stats, spectral_stats, pitch_stats])

    def diarize(self, audio_path: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Extracts multi-feature acoustic embeddings (MFCCs, spectral shape, pitch F0)
        and clusters segments with cosine distance Agglomerative Clustering.
        Applies single-speaker validation and temporal continuity smoothing.
        Updates 'speaker_label' in each segment.
        """
        if not segments:
            return segments

        try:
            y = self._load_audio_mono(audio_path, target_sr=16000)
            sr = 16000
        except Exception as e:
            logger.error("audio_load_failed", error=str(e))
            for i, s in enumerate(segments):
                s["speaker_label"] = f"SPEAKER_{i % 2}"
            return segments

        features = []
        valid_indices = []

        for i, s in enumerate(segments):
            start_sample = max(0, min(int(s["start"] * sr), len(y) - 1))
            end_sample = max(0, min(int(s["end"] * sr), len(y)))

            if end_sample <= start_sample:
                continue

            segment_audio = y[start_sample:end_sample]
            feat_vec = self._extract_segment_features(segment_audio, sr)
            features.append(feat_vec)
            valid_indices.append(i)

        if not features:
            for s in segments:
                s.setdefault("speaker_label", "SPEAKER_0")
            return segments

        X = np.array(features)

        # Handle identical or nearly identical segments (e.g. single speaker monologue)
        if len(X) < 2 or self.num_speakers < 2 or np.allclose(X, X[0], atol=1e-3):
            labels = np.zeros(len(X), dtype=int)
        else:
            std = np.std(X, axis=0)
            std[std == 0] = 1.0
            X_z = (X - np.mean(X, axis=0)) / std

            n_clusters = min(self.num_speakers, len(X_z))
            clusterer = AgglomerativeClustering(
                n_clusters=n_clusters,
                linkage="ward",
            )
            labels = clusterer.fit_predict(X_z)

            # Single-speaker validation: check centroid distance on raw normalized features
            mask0 = labels == 0
            mask1 = labels == 1
            if np.any(mask0) and np.any(mask1):
                norms = np.linalg.norm(X, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                X_unit = X / norms
                c0 = np.mean(X_unit[mask0], axis=0)
                c1 = np.mean(X_unit[mask1], axis=0)
                norm0 = np.linalg.norm(c0)
                norm1 = np.linalg.norm(c1)
                if norm0 > 1e-6 and norm1 > 1e-6:
                    cos_sim = float(np.dot(c0, c1) / (norm0 * norm1))
                    cos_dist = 1.0 - cos_sim
                    if cos_dist < self.min_speaker_distance:
                        logger.info("monologue_detected_single_speaker", cos_dist=round(cos_dist, 4))
                        labels = np.zeros(len(X), dtype=int)

            # Temporal continuity smoothing (eliminates isolated 1-frame toggles)
            for i in range(1, len(valid_indices) - 1):
                prev_idx = valid_indices[i - 1]
                curr_idx = valid_indices[i]

                dur = segments[curr_idx].get("end", 0.0) - segments[curr_idx].get("start", 0.0)
                gap_prev = segments[curr_idx].get("start", 0.0) - segments[prev_idx].get("end", 0.0)

                # Isolated short segment (<0.8s) surrounded by the same speaker
                if (
                    dur < 0.8
                    and gap_prev < 0.5
                    and labels[i - 1] == labels[i + 1]
                    and labels[i] != labels[i - 1]
                ):
                    labels[i] = labels[i - 1]

        for valid_idx, label in zip(valid_indices, labels):
            segments[valid_idx]["speaker_label"] = f"SPEAKER_{label}"

        for s in segments:
            if "speaker_label" not in s:
                s["speaker_label"] = "SPEAKER_0"

        return segments
