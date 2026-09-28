import librosa
import numpy as np
from sklearn.cluster import AgglomerativeClustering

from app.core.logging import get_logger

logger = get_logger("mono_diarizer")

class MonoDiarizer:
    def __init__(self, num_speakers=2):
        self.num_speakers = num_speakers

    def diarize(self, audio_path: str, segments: list[dict]) -> list[dict]:
        """
        Extracts MFCC features for each segment and clusters them into `num_speakers`.
        Updates the 'speaker_label' field in each segment.
        """
        if not segments:
            return segments

        try:
            # Load audio
            y, sr = librosa.load(audio_path, sr=16000, mono=True)
        except Exception as e:
            logger.error("audio_load_failed", error=str(e))
            # Fallback to simple alternating if audio can't be loaded
            for i, s in enumerate(segments):
                s["speaker_label"] = f"SPEAKER_{i % 2}"
            return segments

        features = []
        valid_indices = []

        for i, s in enumerate(segments):
            start_sample = int(s["start"] * sr)
            end_sample = int(s["end"] * sr)

            # Ensure boundaries are within audio length
            start_sample = max(0, min(start_sample, len(y)-1))
            end_sample = max(0, min(end_sample, len(y)))

            segment_audio = y[start_sample:end_sample]

            # If segment is too short, we pad it or skip
            if len(segment_audio) < 512:
                segment_audio = np.pad(segment_audio, (0, max(0, 512 - len(segment_audio))))

            # Extract MFCCs
            mfcc = librosa.feature.mfcc(y=segment_audio, sr=sr, n_mfcc=20)

            # Use mean and std as features for this segment
            mfcc_mean = np.mean(mfcc, axis=1)
            mfcc_std = np.std(mfcc, axis=1)
            feature_vector = np.concatenate([mfcc_mean, mfcc_std])

            features.append(feature_vector)
            valid_indices.append(i)

        if not features:
            return segments

        X = np.array(features)

        # Normalize features
        X_mean = np.mean(X, axis=0)
        X_std = np.std(X, axis=0) + 1e-8
        X = (X - X_mean) / X_std

        # Cluster
        # If there's only 1 segment, we can't cluster into 2
        n_clusters = min(self.num_speakers, len(X))
        clusterer = AgglomerativeClustering(n_clusters=n_clusters)
        labels = clusterer.fit_predict(X)

        for valid_idx, label in zip(valid_indices, labels):
            segments[valid_idx]["speaker_label"] = f"SPEAKER_{label}"

        # Give remaining segments a default label
        for i, s in enumerate(segments):
            if "speaker_label" not in s:
                s["speaker_label"] = "SPEAKER_0"

        return segments
