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
                turn_text = " ".join(w["word"].strip() for w in cur_words if w["word"].strip())
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
            turn_text = " ".join(w["word"].strip() for w in cur_words if w["word"].strip())
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


_CAMPPLUS_SESSION_CACHE: dict[str, Any] = {}
_PYANNOTE_SEG_SESSION_CACHE: dict[str, Any] = {}


def _get_cached_campplus_session(model_path: str) -> Any | None:
    """Returns a cached ONNX Runtime InferenceSession for CAM++ with bounded CPU threads."""
    abs_path = os.path.abspath(model_path)
    if abs_path in _CAMPPLUS_SESSION_CACHE:
        return _CAMPPLUS_SESSION_CACHE[abs_path]
    if ort is None:
        return None
    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_opts.intra_op_num_threads = 1
    sess_opts.inter_op_num_threads = 1
    session = ort.InferenceSession(
        abs_path, sess_options=sess_opts, providers=["CPUExecutionProvider"]
    )
    _CAMPPLUS_SESSION_CACHE[abs_path] = session
    return session


def _get_cached_pyannote_seg_session(model_path: str) -> Any | None:
    """Returns a cached ONNX Runtime InferenceSession for pyannote-segmentation-3.0 (MIT, 6 MB)."""
    abs_path = os.path.abspath(model_path)
    if abs_path in _PYANNOTE_SEG_SESSION_CACHE:
        return _PYANNOTE_SEG_SESSION_CACHE[abs_path]
    if ort is None:
        return None
    sess_opts = ort.SessionOptions()
    sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    sess_opts.intra_op_num_threads = 1
    sess_opts.inter_op_num_threads = 1
    session = ort.InferenceSession(
        abs_path, sess_options=sess_opts, providers=["CPUExecutionProvider"]
    )
    _PYANNOTE_SEG_SESSION_CACHE[abs_path] = session
    return session


def _build_powerset_mapping(num_classes: int, num_speakers: int) -> np.ndarray:
    """Maps pyannote 7-class powerset output to multi-label (num_classes, num_speakers) matrix."""
    mapping = np.zeros((num_classes, num_speakers), dtype=np.float32)
    k = 1
    for j in range(num_speakers):
        if k < num_classes:
            mapping[k, j] = 1.0
            k += 1
    for j in range(num_speakers):
        for m in range(j + 1, num_speakers):
            if k < num_classes:
                mapping[k, j] = 1.0
                mapping[k, m] = 1.0
                k += 1
    return mapping


class MonoDiarizer:
    def __init__(
        self,
        num_speakers: int = 2,
        min_speaker_distance: float = 0.18,
        campplus_path: str = "models/campplus.onnx",
        seg_model_path: str = "models/pyannote-segmentation-3.0.onnx",
    ):
        self.num_speakers = num_speakers
        self.min_speaker_distance = min_speaker_distance
        self.campplus_session = None
        self.seg_session = None

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
                        was_cached = os.path.abspath(p) in _CAMPPLUS_SESSION_CACHE
                        self.campplus_session = _get_cached_campplus_session(p)
                        if not was_cached:
                            logger.info("campplus_model_loaded", path=p)
                        break
                    except Exception as e:
                        logger.warning("campplus_load_failed", path=p, error=str(e))

            seg_candidates = [
                seg_model_path,
                os.path.join(
                    os.path.dirname(__file__), "../../models/pyannote-segmentation-3.0.onnx"
                ),
                "/app/models/pyannote-segmentation-3.0.onnx",
            ]
            for p in seg_candidates:
                if os.path.exists(p):
                    try:
                        was_cached = os.path.abspath(p) in _PYANNOTE_SEG_SESSION_CACHE
                        self.seg_session = _get_cached_pyannote_seg_session(p)
                        if not was_cached:
                            logger.info("pyannote_seg_model_loaded", path=p)
                        break
                    except Exception as e:
                        logger.warning("pyannote_seg_load_failed", path=p, error=str(e))

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

        # Group candidate anchors around the two poles with a margin
        # to avoid contaminating one pole with the other speaker's turns in asymmetric dialogues.
        sim_a = np.dot(cand_X, anchor_a)
        sim_b = np.dot(cand_X, anchor_b)

        margin = sim_a - sim_b
        conf_margin = 0.05 * max_dist
        group_a = cand_X[margin >= conf_margin]
        group_b = cand_X[margin <= -conf_margin]

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
            # If centroid averaging reduced separation due to high class imbalance,
            # but the raw anchor separation is strong (>= 0.30 or >= 1.5 * min_speaker_distance),
            # fall back to the raw anchor vectors directly.
            if max_dist >= max(0.30, self.min_speaker_distance * 1.5):
                logger.info(
                    "using_raw_anchor_poles_for_asymmetric_dialogue",
                    max_dist=max_dist,
                    averaged_sep=final_sep,
                )
                return np.array([anchor_a, anchor_b])
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

        # Centroid separation (clamp at 0.18 to prevent margin explosion on close centroids)
        sep = max(0.18, 1.0 - float(np.dot(c0, c1)))

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
                p_switch = 0.08
            elif gap_i < 0.50:
                # Slight pause, small turn transition penalty
                p_switch = 0.22
            else:
                # Distinct conversational pause (>= 0.5s), natural turn-taking
                p_switch = 0.38

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

    def _split_long_turn_on_voice_change(
        self,
        turn: dict[str, Any],
        y: np.ndarray,
        sr: int = 16000,
    ) -> list[dict[str, Any]]:
        """Detects mid-turn speaker changes (interruptions without pause) in long turns (>=6s)
        using a 1.5s sliding CAM++ window and splits at the nearest word boundary."""
        words = turn.get("words") or []
        dur = float(turn.get("end", 0.0)) - float(turn.get("start", 0.0))
        if self.campplus_session is None or dur < 6.0 or len(words) < 6:
            return [turn]

        win_sec = 1.5
        step_sec = 0.75
        t_start = float(turn["start"])
        t_end = float(turn["end"])

        win_centers: list[float] = []
        win_embs: list[np.ndarray] = []
        cur = t_start
        while cur + win_sec <= t_end + 0.05:
            s_idx = max(0, int(cur * sr))
            e_idx = min(len(y), int((cur + win_sec) * sr))
            emb = self._extract_campplus(y[s_idx:e_idx], sr)
            if emb is not None:
                win_centers.append(cur + win_sec * 0.5)
                win_embs.append(emb)
            cur += step_sec

        if len(win_embs) < 3:
            return [turn]

        change_thresh = max(0.35, self.min_speaker_distance * 1.8)
        split_times: list[float] = []
        for idx in range(len(win_embs) - 1):
            cos_dist = 1.0 - float(np.dot(win_embs[idx], win_embs[idx + 1]))
            if cos_dist >= change_thresh:
                cp = 0.5 * (win_centers[idx] + win_centers[idx + 1])
                if not split_times or (cp - split_times[-1]) >= 2.0:
                    split_times.append(cp)

        if not split_times:
            return [turn]

        # Partition words at the split timestamps
        buckets: list[list[dict[str, Any]]] = [[] for _ in range(len(split_times) + 1)]
        for w in words:
            w_mid = 0.5 * (float(w["start"]) + float(w["end"]))
            b_idx = 0
            while b_idx < len(split_times) and w_mid >= split_times[b_idx]:
                b_idx += 1
            buckets[b_idx].append(w)

        sub_turns: list[dict[str, Any]] = []
        for grp in buckets:
            if not grp:
                continue
            sub_text = " ".join(w["word"].strip() for w in grp if w["word"].strip())
            if not sub_text:
                continue
            sub_turns.append(
                {
                    "start": grp[0]["start"],
                    "end": grp[-1]["end"],
                    "text": sub_text,
                    "words": grp,
                    "confidence": float(np.mean([w.get("confidence", 0.9) for w in grp])),
                }
            )
        return sub_turns if len(sub_turns) >= 2 else [turn]

    def _diarize_with_pyannote_seg(
        self,
        y: np.ndarray,
        sr: int,
        segments: list[dict[str, Any]],
    ) -> list[dict[str, Any]] | None:
        """Neural segmentation (pyannote-segmentation-3.0 ONNX, MIT) + CAM++ clustering.

        Handles zero-gap speaker turns and overlapped speech cleanly without PyTorch.
        Returns None on empty/degenerate input so `diarize()` can fall back safely.
        """
        if self.seg_session is None or self.campplus_session is None or len(y) == 0:
            return None

        meta = self.seg_session.get_modelmeta().custom_metadata_map or {}
        window_size = int(meta.get("window_size", 160000))
        rf_shift = int(meta.get("receptive_field_shift", 270))
        num_local_spk = int(meta.get("num_speakers", 3))
        num_classes = int(meta.get("num_classes", 7))
        window_shift = max(1, window_size // 2)  # 5s hop for 10s window

        mapping = _build_powerset_mapping(num_classes, num_local_spk)
        seg_in = self.seg_session.get_inputs()[0].name
        seg_out = self.seg_session.get_outputs()[0].name

        total_samples = len(y)
        if total_samples <= window_size:
            pad_len = window_size
        else:
            n_hops = (total_samples - window_size + window_shift - 1) // window_shift
            pad_len = n_hops * window_shift + window_size
        padded = np.pad(y, (0, pad_len - total_samples)).astype(np.float32, copy=False)

        starts = list(range(0, len(padded) - window_size + 1, window_shift))
        chunks = np.stack([padded[s : s + window_size] for s in starts], axis=0)

        outs = []
        for i in range(0, len(chunks), 4):
            batch = chunks[i : i + 4, np.newaxis, :]
            (out_y,) = self.seg_session.run([seg_out], {seg_in: batch})
            outs.append(out_y)
        y_all = np.concatenate(outs, axis=0)
        num_chunks, num_frames, _ = y_all.shape

        cls = np.argmax(y_all, axis=-1)
        labels = mapping[cls.reshape(-1)].reshape(num_chunks, num_frames, num_local_spk)

        # Extract CAM++ embeddings only on non-overlapping speech frames
        clean_labels = labels * (labels.sum(axis=-1, keepdims=True) < 1.5)
        samples_per_frame = window_size / float(num_frames)
        min_cam_samples = int(0.4 * sr)
        min_pad_samples = int(0.8 * sr)

        embs: list[np.ndarray] = []
        emb_keys: list[tuple[int, int]] = []
        for c_idx in range(num_chunks):
            chunk_wav = chunks[c_idx]
            for s_idx in range(num_local_spk):
                active_frames = np.where(clean_labels[c_idx, :, s_idx] > 0.5)[0]
                if len(active_frames) < 15:
                    continue
                pieces = [
                    chunk_wav[
                        int(f * samples_per_frame) : min(
                            window_size, int((f + 1) * samples_per_frame)
                        )
                    ]
                    for f in active_frames
                ]
                spk_wav = np.concatenate(pieces)
                if len(spk_wav) < min_cam_samples:
                    continue
                if len(spk_wav) < min_pad_samples:
                    spk_wav = np.pad(spk_wav, (0, min_pad_samples - len(spk_wav)))
                emb = self._extract_campplus(spk_wav, sr)
                if emb is not None:
                    norm = float(np.linalg.norm(emb))
                    if norm > 1e-8:
                        embs.append(emb / norm)
                        emb_keys.append((c_idx, s_idx))

        if not embs:
            return None

        if len(embs) == 1:
            cluster_ids = [0]
        else:
            X = np.stack(embs, axis=0)
            n_clust = min(self.num_speakers, len(embs))
            if n_clust < 2:
                cluster_ids = [0] * len(embs)
            else:
                # Spherical KMeans on L2-normalized CAM++ embeddings: unlike average-linkage,
                # a single noisy/outlier chunk cannot steal cluster #2 and collapse both real
                # speakers into cluster #1 on long real-world calls.
                km = KMeans(n_clusters=n_clust, random_state=42, n_init=10)
                raw_ids = km.fit_predict(X)
                c0 = km.cluster_centers_[0]
                c1 = km.cluster_centers_[1]
                n0, n1 = float(np.linalg.norm(c0)), float(np.linalg.norm(c1))
                centroid_dist = (
                    1.0 - float(np.dot(c0 / n0, c1 / n1)) if n0 > 1e-8 and n1 > 1e-8 else 0.0
                )
                if centroid_dist < self.min_speaker_distance:
                    cluster_ids = [0] * len(embs)
                else:
                    cluster_ids = raw_ids.tolist()

        key_to_global = dict(zip(emb_keys, cluster_ids, strict=False))
        n_global = max(cluster_ids) + 1

        total_frames = int(np.ceil(total_samples / float(rf_shift))) + 10
        global_act = np.zeros((total_frames, n_global), dtype=np.float32)
        for c_idx, start_samp in enumerate(starts):
            f0 = int(round(start_samp / float(rf_shift)))
            f1 = min(total_frames, f0 + num_frames)
            valid_len = f1 - f0
            for s_idx in range(num_local_spk):
                g = key_to_global.get((c_idx, s_idx))
                if g is not None:
                    global_act[f0:f1, g] = np.maximum(
                        global_act[f0:f1, g], labels[c_idx, :valid_len, s_idx]
                    )

        frame_dur = rf_shift / float(sr)

        def _speaker_for_span(t_start: float, t_end: float, fallback_spk: int) -> int:
            f0 = max(0, min(total_frames - 1, int(t_start / frame_dur)))
            f1 = max(f0 + 1, min(total_frames, int(np.ceil(t_end / frame_dur))))
            scores = global_act[f0:f1].sum(axis=0)
            if float(np.max(scores)) > 0.0:
                return int(np.argmax(scores))
            # Check small neighborhood (+/-0.4s) if word timestamp fell in brief gap
            pad_f = max(1, int(0.4 / frame_dur))
            nb = global_act[max(0, f0 - pad_f) : min(total_frames, f1 + pad_f)].sum(axis=0)
            if float(np.max(nb)) > 0.0:
                return int(np.argmax(nb))
            return fallback_spk

        # Align word-level ASR timestamps to neural speaker activity for crisp turn boundaries
        all_words: list[dict[str, Any]] = []
        for seg in segments:
            for w in seg.get("words") or []:
                if w.get("word") and "start" in w and "end" in w:
                    all_words.append(w)

        if all_words:
            all_words.sort(key=lambda w: float(w["start"]))
            assigned_words: list[tuple[int, dict[str, Any]]] = []
            prev_spk = 0
            for w in all_words:
                spk = _speaker_for_span(float(w["start"]), float(w["end"]), prev_spk)
                assigned_words.append((spk, w))
                prev_spk = spk

            # Group contiguous words by speaker (also break on long silence >= 0.8s)
            out_turns: list[dict[str, Any]] = []
            cur_spk = assigned_words[0][0]
            cur_grp: list[dict[str, Any]] = [assigned_words[0][1]]
            for spk, w in assigned_words[1:]:
                gap = float(w["start"]) - float(cur_grp[-1]["end"])
                if spk != cur_spk or gap >= 0.8:
                    text = " ".join(x["word"].strip() for x in cur_grp if x.get("word")).strip()
                    if text:
                        out_turns.append(
                            {
                                "start": round(float(cur_grp[0]["start"]), 2),
                                "end": round(float(cur_grp[-1]["end"]), 2),
                                "text": text,
                                "words": cur_grp,
                                "confidence": float(
                                    np.mean([x.get("confidence", 0.9) for x in cur_grp])
                                ),
                                "speaker_label": f"SPEAKER_{cur_spk}",
                            }
                        )
                    cur_spk = spk
                    cur_grp = [w]
                else:
                    cur_grp.append(w)
            if cur_grp:
                text = " ".join(x["word"].strip() for x in cur_grp if x.get("word")).strip()
                if text:
                    out_turns.append(
                        {
                            "start": round(float(cur_grp[0]["start"]), 2),
                            "end": round(float(cur_grp[-1]["end"]), 2),
                            "text": text,
                            "words": cur_grp,
                            "confidence": float(
                                np.mean([x.get("confidence", 0.9) for x in cur_grp])
                            ),
                            "speaker_label": f"SPEAKER_{cur_spk}",
                        }
                    )
            return out_turns if out_turns else None

        # Fallback when segments have no word-level timestamps
        out_segs: list[dict[str, Any]] = []
        prev_spk = 0
        for seg in segments:
            s_copy = dict(seg)
            spk = _speaker_for_span(float(seg["start"]), float(seg["end"]), prev_spk)
            s_copy["speaker_label"] = f"SPEAKER_{spk}"
            prev_spk = spk
            out_segs.append(s_copy)
        return out_segs

    def diarize(self, audio_path: str, segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        Diarizes mono audio:
        0. Primary path: pyannote-segmentation-3.0 ONNX (MIT) + CAM++ clustering (handles
           zero-gap turns and overlaps cleanly).
        1. Fallback path (if segmentation model absent): pause splitting + CAM++ sliding-window
           + anchor centroids + Viterbi smoothing.
        """
        if not segments:
            return segments

        try:
            y = self._load_audio_mono(audio_path, target_sr=16000)
            sr = 16000
        except Exception as e:
            logger.error("audio_load_failed", error=str(e))
            turns = split_segment_into_turns(segments)
            for i, s in enumerate(turns):
                s["speaker_label"] = f"SPEAKER_{i % 2}"
            return turns

        # 0. Primary path: neural pyannote-segmentation-3.0 + CAM++
        if self.seg_session is not None and self.campplus_session is not None:
            try:
                seg_result = self._diarize_with_pyannote_seg(y, sr, segments)
                if seg_result:
                    return seg_result
            except Exception as e:
                logger.warning("pyannote_seg_diarization_fallback", error=str(e))

        # 1. Fallback path: Turn splitting + CAM++
        turns = split_segment_into_turns(segments)

        # 2. Split long uninterrupted turns (>=6s) if voice changes mid-turn
        if self.campplus_session is not None:
            refined_turns: list[dict[str, Any]] = []
            for t in turns:
                refined_turns.extend(self._split_long_turn_on_voice_change(t, y, sr))
            turns = refined_turns

        cam_embs: list[np.ndarray | None] = []
        valid_chunks: list[np.ndarray] = []
        valid_indices: list[int] = []
        min_cam_dur_samples = int(sr * 0.4)
        min_pad_samples = int(sr * 0.8)

        for i, t in enumerate(turns):
            start_sample = max(0, min(int(t["start"] * sr), len(y) - 1))
            end_sample = max(0, min(int(t["end"] * sr), len(y)))

            if end_sample <= start_sample:
                continue

            raw_chunk = y[start_sample:end_sample]
            dur_samples = len(raw_chunk)

            # Avoid bleeding into neighboring speaker's audio on short turns (<0.8s):
            # - turns <0.4s skip CAM++ and inherit label from neighbors/Viterbi;
            # - turns 0.4s..0.8s are zero-padded rather than grabbing neighbor speech.
            if self.campplus_session is not None and dur_samples >= min_cam_dur_samples:
                if dur_samples < min_pad_samples:
                    pad_needed = min_pad_samples - dur_samples
                    pad_left = pad_needed // 2
                    cam_chunk = np.pad(raw_chunk, (pad_left, pad_needed - pad_left))
                else:
                    cam_chunk = raw_chunk
                c_emb = self._extract_campplus(cam_chunk, sr)
            else:
                c_emb = None

            cam_embs.append(c_emb)
            valid_chunks.append(raw_chunk)
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

        # Compute heavy librosa/pitch acoustic features LAZILY only when CAM++ is unavailable
        acoustics = (
            [] if use_cam else [self._extract_segment_features(chunk, sr) for chunk in valid_chunks]
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

                    # 3. Post-clustering validation (guard against false splits of monologues)
                    n_spk0 = int(np.sum(cam_labels == 0))
                    n_spk1 = int(np.sum(cam_labels == 1))
                    if n_spk0 == 0 or n_spk1 == 0:
                        cam_labels = np.zeros(len(X_cam), dtype=int)
                    else:
                        c0_final = np.mean(X_cam[cam_labels == 0], axis=0)
                        c1_final = np.mean(X_cam[cam_labels == 1], axis=0)
                        norm0 = np.linalg.norm(c0_final)
                        norm1 = np.linalg.norm(c1_final)
                        c0_norm = c0_final / max(norm0, 1e-6)
                        c1_norm = c1_final / max(norm1, 1e-6)
                        final_cluster_sep = 1.0 - float(np.dot(c0_norm, c1_norm))

                        min_turns = min(n_spk0, n_spk1)
                        turn_ratio = min_turns / max(1, len(cam_labels))

                        is_monologue = False
                        if final_cluster_sep < self.min_speaker_distance:
                            logger.info(
                                "monologue_detected_low_cluster_separation",
                                sep=final_cluster_sep,
                                threshold=self.min_speaker_distance,
                            )
                            is_monologue = True
                        elif len(cam_labels) >= 4:
                            try:
                                from sklearn.metrics import silhouette_score

                                sil = float(silhouette_score(X_cam, cam_labels, metric="cosine"))
                                if sil < 0.12 or (sil < 0.18 and turn_ratio < 0.05):
                                    logger.info(
                                        "monologue_detected_low_silhouette",
                                        silhouette=sil,
                                        turn_ratio=turn_ratio,
                                    )
                                    is_monologue = True
                            except Exception as e:
                                logger.debug("silhouette_check_failed", error=str(e))

                        if is_monologue:
                            cam_labels = np.zeros(len(X_cam), dtype=int)

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
            X_raw = np.array(acoustics)
            std = np.std(X_raw, axis=0)
            std[std == 0] = 1.0
            X = (X_raw - np.mean(X_raw, axis=0)) / std
            # Fundamental frequency (pitch) is the primary acoustic invariant distinguishing speakers
            X[:, 0] *= 3.5
            X[:, 1] *= 1.5

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
