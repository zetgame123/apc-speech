import os
from typing import Any

import librosa
import numpy as np
import soundfile as sf
from sklearn.cluster import AgglomerativeClustering, KMeans

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

    def _find_anchor_centroids(
        self,
        X: np.ndarray,
        durations: np.ndarray,
    ) -> np.ndarray | None:
        """
        Identifies high-confidence acoustic anchor turns (duration >= 2.0s or top percentile)
        and computes robust initial centroids for distinct speakers based on maximum cosine separation.
        Returns None if even the most separated anchors do not exceed min_speaker_distance (monologue).
        """
        n_samples = len(X)
        if n_samples < 2:
            return None

        # Filter candidate anchor turns with long duration
        long_mask = durations >= 2.0
        num_long = np.sum(long_mask)
        if num_long >= 2:
            candidate_idxs = np.where(long_mask)[0]
        else:
            # Fallback to top 35% longest turns, minimum 2
            k = max(2, int(np.ceil(n_samples * 0.35)))
            candidate_idxs = np.argsort(durations)[-k:]

        cand_X = X[candidate_idxs]
        # Cosine distance between normalized candidate embeddings
        sim_matrix = np.dot(cand_X, cand_X.T)
        dist_matrix = 1.0 - sim_matrix

        # Avoid self-distance on diagonal
        np.fill_diagonal(dist_matrix, -1.0)
        max_idx_flat = int(np.argmax(dist_matrix))
        row_idx, col_idx = divmod(max_idx_flat, len(candidate_idxs))
        max_dist = float(dist_matrix[row_idx, col_idx])

        # Monologue check on maximum anchor distance
        if max_dist < self.min_speaker_distance:
            logger.debug(
                "anchor_separation_below_threshold",
                max_dist=max_dist,
                threshold=self.min_speaker_distance,
            )
            return None

        anchor_a = cand_X[row_idx]
        anchor_b = cand_X[col_idx]

        # Group all candidate anchors around the two poles
        sim_a = np.dot(cand_X, anchor_a)
        sim_b = np.dot(cand_X, anchor_b)

        group_a = cand_X[sim_a >= sim_b]
        group_b = cand_X[sim_b > sim_a]

        if len(group_a) == 0 or len(group_b) == 0:
            c0 = anchor_a
            c1 = anchor_b
        else:
            c0 = np.mean(group_a, axis=0)
            c1 = np.mean(group_b, axis=0)

        norm0 = np.linalg.norm(c0)
        norm1 = np.linalg.norm(c1)
        c0 = c0 / max(norm0, 1e-6)
        c1 = c1 / max(norm1, 1e-6)

        final_sep = 1.0 - float(np.dot(c0, c1))
        if final_sep < self.min_speaker_distance:
            return None

        return np.array([c0, c1])

    def _viterbi_smoothing(
        self,
        turns: list[dict[str, Any]],
        valid_indices: list[int],
        embeddings: np.ndarray,
        centroids: np.ndarray,
        beta: float = 3.5,
    ) -> np.ndarray:
        """
        Applies Hidden Markov Model (HMM) Viterbi decoding to smooth turn assignments.
        Uses normalized relative margin between centroids so even similar voices produce
        sharp, confident emission probabilities.
        Penalizes unnatural rapid flip-flops across negligible pauses (<0.25s),
        while allowing genuine dialogue turn-taking across natural pauses (>=0.35s).
        """
        n = len(valid_indices)
        if n <= 1:
            return np.zeros(n, dtype=int)

        c0, c1 = centroids[0], centroids[1]
        norm0 = np.linalg.norm(c0)
        norm1 = np.linalg.norm(c1)
        c0 = c0 / max(norm0, 1e-6)
        c1 = c1 / max(norm1, 1e-6)

        norms_e = np.linalg.norm(embeddings, axis=1, keepdims=True)
        e_norm = embeddings / np.maximum(norms_e, 1e-6)

        # Centroid separation
        sep = max(1e-4, 1.0 - float(np.dot(c0, c1)))

        # Cosine similarities to c0 and c1: shape (n, 2)
        sim_c0 = np.dot(e_norm, c0)
        sim_c1 = np.dot(e_norm, c1)

        # Normalized margins: in range roughly [-1.0, 1.0]
        margin_0 = (sim_c0 - sim_c1) / sep
        margin_1 = -margin_0

        # Pre-compute durations and pauses
        durs = np.zeros(n, dtype=np.float32)
        gaps = np.zeros(n, dtype=np.float32)
        for i in range(n):
            idx = valid_indices[i]
            durs[i] = max(0.01, turns[idx].get("end", 0.0) - turns[idx].get("start", 0.0))
            if i > 0:
                prev_idx = valid_indices[i - 1]
                gaps[i] = max(0.0, turns[idx].get("start", 0.0) - turns[prev_idx].get("end", 0.0))

        V = np.zeros((n, 2), dtype=np.float64)
        bp = np.zeros((n, 2), dtype=int)

        w0 = float(np.clip(durs[0] / 1.5, 0.35, 1.0))
        V[0, 0] = np.log(0.5) + beta * w0 * margin_0[0]
        V[0, 1] = np.log(0.5) + beta * w0 * margin_1[0]

        for i in range(1, n):
            dur_i = durs[i]
            gap_i = gaps[i]

            if gap_i < 0.25:
                # Immediate speech continuation (likely same speaker)
                p_switch = 0.15
            elif gap_i < 0.50:
                # Slight pause, small turn transition penalty
                p_switch = 0.35
            else:
                # Distinct conversational pause (>= 0.5s), natural turn-taking
                p_switch = 0.50

            p_stay = 1.0 - p_switch
            log_stay = np.log(p_stay)
            log_switch = np.log(p_switch)

            w_i = float(np.clip(dur_i / 1.5, 0.35, 1.0))
            emit_0 = beta * w_i * margin_0[i]
            emit_1 = beta * w_i * margin_1[i]

            val_00 = V[i - 1, 0] + log_stay
            val_10 = V[i - 1, 1] + log_switch
            if val_00 >= val_10:
                V[i, 0] = val_00 + emit_0
                bp[i, 0] = 0
            else:
                V[i, 0] = val_10 + emit_0
                bp[i, 0] = 1

            val_01 = V[i - 1, 0] + log_switch
            val_11 = V[i - 1, 1] + log_stay
            if val_01 >= val_11:
                V[i, 1] = val_01 + emit_1
                bp[i, 1] = 0
            else:
                V[i, 1] = val_11 + emit_1
                bp[i, 1] = 1

        best = np.zeros(n, dtype=int)
        best[-1] = int(np.argmax(V[-1]))
        for i in range(n - 2, -1, -1):
            best[i] = bp[i + 1, best[i + 1]]

        return best

    def diarize(self, audio_path: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Diarizes mono audio:
        1. Splits merged Whisper segments into dialogue turns using word pause gaps.
        2. Extracts neural CAM++ embeddings (or acoustic features fallback).
        3. Identifies anchor centroids from longest turns.
        4. Smooths turn assignments via Markov / HMM Viterbi decoding.
        5. Assigns 'speaker_label' (SPEAKER_0, SPEAKER_1).
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
        min_dur_samples = int(sr * 0.8)

        for i, t in enumerate(turns):
            start_sample = max(0, min(int(t["start"] * sr), len(y) - 1))
            end_sample = max(0, min(int(t["end"] * sr), len(y)))

            if end_sample <= start_sample:
                continue

            # Context padding for short utterances to ensure robust CAM++ embedding
            dur_samples = end_sample - start_sample
            if dur_samples < min_dur_samples:
                pad_needed = min_dur_samples - dur_samples
                pad_left = pad_needed // 2
                pad_right = pad_needed - pad_left
                chunk_start = max(0, start_sample - pad_left)
                chunk_end = min(len(y), end_sample + pad_right)
                chunk = y[chunk_start:chunk_end]
            else:
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

        # Determine representation: use CAM++ if at least 50% of turns extracted embeddings
        valid_cam_positions = [pos for pos, e in enumerate(cam_embs) if e is not None]
        use_cam = len(valid_cam_positions) >= 2 and (
            len(valid_cam_positions) / len(cam_embs) >= 0.5
        )

        if use_cam:
            X_cam = np.array([cam_embs[pos] for pos in valid_cam_positions])
            durs_cam = np.array(
                [
                    turns[valid_indices[pos]]["end"] - turns[valid_indices[pos]]["start"]
                    for pos in valid_cam_positions
                ]
            )
            n_clus = min(self.num_speakers, len(X_cam))

            if n_clus < 2:
                cam_labels = np.zeros(len(X_cam), dtype=int)
            elif len(X_cam) == 2:
                dist = 1.0 - float(np.dot(X_cam[0], X_cam[1]))
                cam_labels = (
                    np.array([0, 1]) if dist >= self.min_speaker_distance else np.array([0, 0])
                )
            else:
                # 1. Anchor-based centroid discovery
                anchor_centroids = self._find_anchor_centroids(X_cam, durs_cam)

                if anchor_centroids is None:
                    # Monologue detected based on anchor turn separation
                    cam_labels = np.zeros(len(X_cam), dtype=int)
                else:
                    # Initialize KMeans with robust anchor centroids
                    km = KMeans(
                        n_clusters=n_clus,
                        init=anchor_centroids,
                        n_init=1,
                        random_state=42,
                    )
                    km.fit(X_cam)
                    final_centroids = km.cluster_centers_

                    # 2. Markov / Viterbi sequence smoothing across turns
                    cam_labels = self._viterbi_smoothing(
                        turns=[turns[valid_indices[pos]] for pos in valid_cam_positions],
                        valid_indices=list(range(len(valid_cam_positions))),
                        embeddings=X_cam,
                        centroids=final_centroids,
                    )

            # Map cluster labels to all valid turns
            labels = np.zeros(len(valid_indices), dtype=int)
            for pos_in_valid_cam, orig_pos in enumerate(valid_cam_positions):
                labels[orig_pos] = cam_labels[pos_in_valid_cam]

            # Assign any turns with missing CAM++ embeddings to nearest neighbor
            for pos in range(len(valid_indices)):
                if cam_embs[pos] is None:
                    for offset in range(1, len(valid_indices)):
                        if pos - offset >= 0 and cam_embs[pos - offset] is not None:
                            labels[pos] = labels[pos - offset]
                            break
                        if pos + offset < len(valid_indices) and cam_embs[pos + offset] is not None:
                            labels[pos] = labels[pos + offset]
                            break
        else:
            # Fallback to acoustic features (MFCC, pitch, spectral)
            X_raw = np.array(acoustics)
            std = np.std(X_raw, axis=0)
            std[std == 0] = 1.0
            X = (X_raw - np.mean(X_raw, axis=0)) / std

            if len(X) < 2 or self.num_speakers < 2:
                labels = np.zeros(len(X), dtype=int)
            elif len(X) == 2:
                dist = float(np.linalg.norm(X[0] - X[1]))
                labels = np.array([0, 1]) if dist >= 1.2 else np.array([0, 0])
            else:
                clus = AgglomerativeClustering(
                    n_clusters=min(self.num_speakers, len(X)),
                    metric="euclidean",
                    linkage="ward",
                )
                labels = clus.fit_predict(X)

                mask0 = labels == 0
                mask1 = labels == 1
                if np.any(mask0) and np.any(mask1):
                    c0 = np.mean(X[mask0], axis=0)
                    c1 = np.mean(X[mask1], axis=0)
                    sep = float(np.linalg.norm(c0 - c1))
                    if sep < 1.4:
                        labels = np.zeros(len(X), dtype=int)

            # Temporal continuity smoothing for acoustic fallback
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

        # Standardize speaker numbering: the first speaker to talk is always SPEAKER_0
        if len(labels) > 0 and labels[0] == 1 and np.any(labels == 0):
            labels = 1 - labels

        for valid_idx, label in zip(valid_indices, labels):
            turns[valid_idx]["speaker_label"] = f"SPEAKER_{label}"

        for t in turns:
            if "speaker_label" not in t:
                t["speaker_label"] = "SPEAKER_0"

        return turns
