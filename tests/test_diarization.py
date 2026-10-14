import os
import tempfile

import numpy as np
import pytest
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
        diarizer = MonoDiarizer(num_speakers=2, min_speaker_distance=0.10)
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


def test_split_segment_into_turns_with_pauses():
    """Verify that a single merged segment is split into dialogue turns based on word timestamps and pauses."""
    from app.diarization.mono_diarizer import split_segment_into_turns

    seg = {
        "start": 0.0,
        "end": 5.0,
        "text": "Здравствуйте, чем могу помочь? Я хочу оформить заказ.",
        "words": [
            {"word": "Здравствуйте,", "start": 0.1, "end": 0.8, "confidence": 0.95},
            {"word": " чем", "start": 0.85, "end": 1.1, "confidence": 0.92},
            {"word": " могу", "start": 1.12, "end": 1.4, "confidence": 0.94},
            {"word": " помочь?", "start": 1.42, "end": 1.9, "confidence": 0.96},
            # 0.5s pause between question and answer
            {"word": " Я", "start": 2.4, "end": 2.6, "confidence": 0.91},
            {"word": " хочу", "start": 2.62, "end": 3.0, "confidence": 0.93},
            {"word": " оформить", "start": 3.05, "end": 3.6, "confidence": 0.95},
            {"word": " заказ.", "start": 3.65, "end": 4.1, "confidence": 0.94},
        ],
    }

    turns = split_segment_into_turns([seg], min_pause_sec=0.35)
    assert len(turns) == 2
    assert "помочь?" in turns[0]["text"]
    assert "заказ." in turns[1]["text"]
    assert turns[0]["end"] == 1.9
    assert turns[1]["start"] == 2.4


def test_mono_diarizer_real_speech_monologue_and_dialogue():
    """Verify that default MonoDiarizer correctly separates real dialogue and unifies monologue."""
    dialogue_wav = "data/evaluation_suite/sample_dialogue_01_telephony_8k.wav"
    monologue_wav = "data/evaluation_suite/voice10_monologue.wav"

    missing = [p for p in (dialogue_wav, monologue_wav) if not os.path.exists(p)]
    if missing:
        pytest.skip(f"нет аудиоданных: {missing}")

    diarizer = MonoDiarizer(num_speakers=2)
    if diarizer.campplus_session is None:
        pytest.skip(
            "нет весов CAM++ (models/campplus.onnx): тест проверяет нейронный путь; "
            "запасной акустический путь покрыт синтетическими тестами выше"
        )

    # 1. Real dialogue should yield 2 speakers
    dialogue_segs = [
        {"start": 0.0, "end": 5.06, "text": "Клиент спрашивает о стоимости"},
        {"start": 5.56, "end": 11.2, "text": "Менеджер отвечает о стоимости"},
    ]
    res_diag = diarizer.diarize(dialogue_wav, dialogue_segs)
    diag_spks = {s["speaker_label"] for s in res_diag}
    assert len(diag_spks) == 2
    assert "SPEAKER_0" in diag_spks and "SPEAKER_1" in diag_spks

    # 2. Real monologue with natural pauses should stay unified under SPEAKER_0
    mono_segs = [
        {"start": 0.0, "end": 3.0, "text": "Фраза 1"},
        {"start": 3.5, "end": 6.5, "text": "Фраза 2"},
        {"start": 7.0, "end": 9.8, "text": "Фраза 3"},
    ]
    res_mono = diarizer.diarize(monologue_wav, mono_segs)
    mono_spks = {s["speaker_label"] for s in res_mono}
    assert len(mono_spks) == 1
    assert "SPEAKER_0" in mono_spks


def test_mono_diarizer_asymmetric_dialogue():
    """Verify that an asymmetric dialogue (one speaker dominates speech) is correctly separated."""
    sr = 16000
    dur = 2.0
    t = np.linspace(0, dur, int(sr * dur), endpoint=False)

    spk1_audio = (
        0.5 * np.sin(2 * np.pi * 120 * t)
        + 0.3 * np.sin(2 * np.pi * 240 * t)
        + 0.2 * np.sin(2 * np.pi * 360 * t)
    )
    spk2_audio = (
        0.5 * np.sin(2 * np.pi * 240 * t)
        + 0.3 * np.sin(2 * np.pi * 480 * t)
        + 0.2 * np.sin(2 * np.pi * 720 * t)
    )

    # Spk1 speaks 5 segments, Spk2 speaks only 1 segment
    total_audio = np.zeros(int(sr * 15.0), dtype=np.float32)
    total_audio[int(sr * 0.0) : int(sr * 2.0)] = spk1_audio
    total_audio[int(sr * 2.5) : int(sr * 4.5)] = spk1_audio
    total_audio[int(sr * 5.0) : int(sr * 7.0)] = spk1_audio
    total_audio[int(sr * 7.5) : int(sr * 9.5)] = spk1_audio
    total_audio[int(sr * 10.0) : int(sr * 12.0)] = spk2_audio  # Only 1 segment of Spk2
    total_audio[int(sr * 12.5) : int(sr * 14.5)] = spk1_audio

    segments = [
        {"start": 0.0, "end": 2.0, "text": "Приветствие компании"},
        {"start": 2.5, "end": 4.5, "text": "Рассказ об условиях программы"},
        {"start": 5.0, "end": 7.0, "text": "Подробности о пассивном доходе"},
        {"start": 7.5, "end": 9.5, "text": "Вопрос клиенту о заинтересованности"},
        {"start": 10.0, "end": 12.0, "text": "Вышлите мне на почту подробности"},
        {"start": 12.5, "end": 14.5, "text": "Хорошо специалист свяжется с вами"},
    ]

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_wav = f.name
        sf.write(temp_wav, total_audio, sr)

    try:
        diarizer = MonoDiarizer(num_speakers=2, min_speaker_distance=0.10)
        diarized = diarizer.diarize(temp_wav, segments)

        spks = {s["speaker_label"] for s in diarized}
        assert len(spks) == 2, f"Expected 2 speakers in asymmetric dialogue, got {spks}"
        # Segment 4 (spk2) should have a different speaker label than segment 0 (spk1)
        assert diarized[4]["speaker_label"] != diarized[0]["speaker_label"]
    finally:
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
