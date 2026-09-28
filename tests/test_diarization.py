import os
import tempfile

import numpy as np
import soundfile as sf

from app.diarization.mono_diarizer import MonoDiarizer


def test_mono_diarizer_separates_distinct_speakers():
    """Verify that two speakers with distinct acoustic profiles (pitches and harmonics) are correctly separated."""
    sr = 16000
    dur = 2.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)

    # Speaker 1: lower fundamental pitch ~120 Hz with harmonics
    spk1_audio = (
        0.5 * np.sin(2 * np.pi * 120 * t)
        + 0.3 * np.sin(2 * np.pi * 240 * t)
        + 0.2 * np.sin(2 * np.pi * 360 * t)
    )

    # Speaker 2: higher fundamental pitch ~240 Hz with harmonics
    spk2_audio = (
        0.5 * np.sin(2 * np.pi * 240 * t)
        + 0.3 * np.sin(2 * np.pi * 480 * t)
        + 0.2 * np.sin(2 * np.pi * 720 * t)
    )

    # Construct dialogue: Spk1 (0-2s), Spk2 (2.5-4.5s), Spk1 (5-7s), Spk2 (7.5-9.5s)
    total_audio = np.zeros(int(sr * 10.0), dtype=np.float32)
    total_audio[int(sr * 0.0) : int(sr * 2.0)] = spk1_audio
    total_audio[int(sr * 2.5) : int(sr * 4.5)] = spk2_audio
    total_audio[int(sr * 5.0) : int(sr * 7.0)] = spk1_audio
    total_audio[int(sr * 7.5) : int(sr * 9.5)] = spk2_audio

    segments = [
        {"start": 0.0, "end": 2.0, "text": "Добрый день компания аутдор"},
        {"start": 2.5, "end": 4.5, "text": "Здравствуйте сколько стоит щит"},
        {"start": 5.0, "end": 7.0, "text": "Щит стоит сто тысяч рублей"},
        {"start": 7.5, "end": 9.5, "text": "Понятно спасибо большое"},
    ]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, total_audio, sr)

    try:
        diarizer = MonoDiarizer(num_speakers=2)
        diarized = diarizer.diarize(temp_wav, segments)

        assert len(diarized) == 4
        # Segments 0 and 2 should share the same speaker label
        assert diarized[0]["speaker_label"] == diarized[2]["speaker_label"]
        # Segments 1 and 3 should share the same speaker label
        assert diarized[1]["speaker_label"] == diarized[3]["speaker_label"]
        # The two speakers must be distinct
        assert diarized[0]["speaker_label"] != diarized[1]["speaker_label"]
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)


def test_mono_diarizer_single_speaker_monologue():
    """Verify that a single speaker monologue is not artificially split into multiple speakers."""
    sr = 16000
    dur = 2.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)
    single_spk = 0.5 * np.sin(2 * np.pi * 150 * t) + 0.2 * np.sin(2 * np.pi * 300 * t)

    total_audio = np.zeros(int(sr * 8.0), dtype=np.float32)
    total_audio[int(sr * 0.0) : int(sr * 2.0)] = single_spk
    total_audio[int(sr * 3.0) : int(sr * 5.0)] = single_spk
    total_audio[int(sr * 5.5) : int(sr * 7.5)] = single_spk

    segments = [
        {"start": 0.0, "end": 2.0, "text": "Вас приветствует автоответчик"},
        {"start": 3.0, "end": 5.0, "text": "Оставьте сообщение после сигнала"},
        {"start": 5.5, "end": 7.5, "text": "Всего доброго"},
    ]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, total_audio, sr)

    try:
        diarizer = MonoDiarizer(num_speakers=2, min_speaker_distance=0.10)
        diarized = diarizer.diarize(temp_wav, segments)

        # All segments should be unified under SPEAKER_0
        labels = {s["speaker_label"] for s in diarized}
        assert len(labels) == 1
        assert "SPEAKER_0" in labels
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)


def test_mono_diarizer_edge_cases():
    """Verify handling of empty segments and invalid audio files."""
    diarizer = MonoDiarizer(num_speakers=2)
    assert diarizer.diarize("non_existent.wav", []) == []

    # Non-existent file with segments should fallback gracefully without crash
    segs = [{"start": 0.0, "end": 1.0, "text": "Тест"}]
    res = diarizer.diarize("/path/to/missing/file.wav", segs)
    assert len(res) == 1
    assert "speaker_label" in res[0]
