import os
import tempfile

import numpy as np
import soundfile as sf

from app.workers.chunking import chunk_audio, chunk_audio_adaptive, merge_and_deduplicate


def test_chunk_audio_adaptive_short_audio_single_chunk():
    """Verify that audio shorter than max_chunk_sec is not split and returned as a single chunk."""
    sr = 16000
    duration_sec = 5.0
    audio = np.random.randn(int(sr * duration_sec)).astype(np.float32) * 0.1

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, audio, sr)

    try:
        chunks = chunk_audio_adaptive(temp_wav, max_chunk_sec=10)
        assert len(chunks) == 1
        assert chunks[0]["start_sec"] == 0.0
        assert round(chunks[0]["end_sec"], 1) == 5.0
        assert chunks[0]["is_silence_split"] is True
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)


def test_chunk_audio_adaptive_splits_at_natural_silence():
    """Verify that long audio is cleanly split inside the silence pause rather than cutting speech."""
    sr = 16000
    # 25 seconds total: speech (0-11s), silence (11-13s), speech (13-25s)
    audio = np.random.randn(int(sr * 25.0)).astype(np.float32) * 0.1
    audio[int(sr * 11.0) : int(sr * 13.0)] = 0.0  # 2 seconds of silence

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, audio, sr)

    try:
        # Request split with min_chunk=8s, max_chunk=15s
        chunks = chunk_audio_adaptive(
            temp_wav,
            target_chunk_sec=12,
            min_chunk_sec=8,
            max_chunk_sec=15,
            min_silence_sec=0.5,
        )
        assert len(chunks) == 2
        # First chunk should end inside the silence interval [11.0, 13.0]
        split_time = chunks[0]["end_sec"]
        assert 11.0 <= split_time <= 13.0
        # Second chunk should start exactly where the first ended
        assert chunks[1]["start_sec"] == split_time
        assert round(chunks[1]["end_sec"], 1) == 25.0
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)


def test_chunk_audio_adaptive_preserves_requested_overlap():
    sr = 16000
    # 25 seconds total: speech (0-11s), silence (11-13s), speech (13-25s)
    audio = np.random.randn(int(sr * 25.0)).astype(np.float32) * 0.1
    audio[int(sr * 11.0) : int(sr * 13.0)] = 0.0

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, audio, sr)

    try:
        chunks = chunk_audio_adaptive(
            temp_wav,
            target_chunk_sec=12,
            min_chunk_sec=8,
            max_chunk_sec=15,
            min_silence_sec=0.5,
            overlap_sec=2,
        )
        assert len(chunks) == 2
        assert chunks[1]["start_sec"] < chunks[0]["end_sec"]
        assert round(chunks[1]["overlap_sec"], 1) == 2.0
        assert round(chunks[1]["start_sec"], 2) == round(chunks[0]["end_sec"] - 2.0, 2)
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)


def test_merge_and_deduplicate_silence_split_chunks():
    """Verify lossless timestamp offset and preservation of words across silence-split chunks."""
    chunks_info = [
        {"start_sec": 0.0, "end_sec": 12.0, "overlap_sec": 0.0},
        {"start_sec": 12.0, "end_sec": 25.0, "overlap_sec": 0.0},
    ]

    all_segments = [
        [
            {
                "start": 1.0,
                "end": 3.0,
                "text": "Привет",
                "words": [{"word": "Привет", "start": 1.0, "end": 3.0}],
            }
        ],
        [
            {
                "start": 2.0,
                "end": 5.0,
                "text": "Мир",
                "words": [{"word": "Мир", "start": 2.0, "end": 5.0}],
            }
        ],
    ]

    merged = merge_and_deduplicate(all_segments, chunks_info)

    assert len(merged) == 2
    # Segment 1 unchanged (starts at 1.0)
    assert merged[0]["start"] == 1.0
    assert merged[0]["end"] == 3.0
    assert merged[0]["words"][0]["start"] == 1.0

    # Segment 2 shifted by 12.0s
    assert merged[1]["start"] == 14.0
    assert merged[1]["end"] == 17.0
    assert merged[1]["words"][0]["start"] == 14.0
    assert merged[1]["words"][0]["end"] == 17.0


def test_chunk_audio_default_wrapper():
    """Verify default chunk_audio wrapper properly executes adaptive chunking."""
    sr = 16000
    audio = np.random.randn(int(sr * 4.0)).astype(np.float32) * 0.05
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, audio, sr)

    try:
        chunks = chunk_audio(temp_wav, chunk_length_sec=10)
        assert len(chunks) == 1
        assert chunks[0]["end_sec"] == 4.0
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
