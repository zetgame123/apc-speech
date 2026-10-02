import os
import tempfile

import numpy as np
import soundfile as sf

from app.asr.preprocessor import AudioPreprocessor


def test_split_stereo_channels():
    preprocessor = AudioPreprocessor()

    sr = 16000
    duration_sec = 1
    # 2 channels: left channel tone, right channel silence
    left = np.sin(2 * np.pi * 440 * np.linspace(0, duration_sec, sr, dtype=np.float32))
    right = np.zeros(sr, dtype=np.float32)
    stereo_data = np.stack([left, right], axis=1)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_input = f.name
        sf.write(temp_input, stereo_data, sr)

    try:
        ch0_path, ch1_path = preprocessor.split_stereo_channels(temp_input)
        assert os.path.exists(ch0_path)
        assert os.path.exists(ch1_path)

        data0, sr0 = sf.read(ch0_path)
        data1, sr1 = sf.read(ch1_path)

        assert sr0 == sr
        assert sr1 == sr
        assert data0.ndim == 1
        assert data1.ndim == 1
        assert len(data0) == sr
        assert len(data1) == sr
    finally:
        for p in (temp_input, ch0_path, ch1_path):
            if os.path.exists(p):
                os.remove(p)


def test_check_channel_isolation_true_telephony():
    preprocessor = AudioPreprocessor()
    sr = 16000
    duration_sec = 4

    # Ch0 speaks in sec 0-2, Ch1 silent. Ch1 speaks in sec 2-4, Ch0 silent.
    ch0 = np.zeros(sr * duration_sec, dtype=np.float32)
    ch1 = np.zeros(sr * duration_sec, dtype=np.float32)

    t2 = np.linspace(0, 2, sr * 2, endpoint=False, dtype=np.float32)
    ch0[: sr * 2] = 0.5 * np.sin(2 * np.pi * 300 * t2)
    ch1[sr * 2 :] = 0.5 * np.sin(2 * np.pi * 500 * t2)

    stereo = np.stack([ch0, ch1], axis=1)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_input = f.name
        sf.write(temp_input, stereo, sr)

    try:
        assert preprocessor.check_channel_isolation(temp_input) is True
    finally:
        if os.path.exists(temp_input):
            os.remove(temp_input)


def test_check_channel_isolation_false_dual_mono():
    preprocessor = AudioPreprocessor()
    sr = 16000
    duration_sec = 4

    # Both channels speak simultaneously with identical signal (dual mono)
    t = np.linspace(0, duration_sec, sr * duration_sec, endpoint=False, dtype=np.float32)
    tone = 0.5 * np.sin(2 * np.pi * 440 * t)
    stereo = np.stack([tone, tone], axis=1)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_input = f.name
        sf.write(temp_input, stereo, sr)

    try:
        assert preprocessor.check_channel_isolation(temp_input) is False
    finally:
        if os.path.exists(temp_input):
            os.remove(temp_input)


def test_check_channel_isolation_mono_file():
    preprocessor = AudioPreprocessor()
    sr = 16000
    duration_sec = 2
    t = np.linspace(0, duration_sec, sr * duration_sec, endpoint=False, dtype=np.float32)
    mono = 0.5 * np.sin(2 * np.pi * 440 * t)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        temp_input = f.name
        sf.write(temp_input, mono, sr)

    try:
        assert preprocessor.check_channel_isolation(temp_input) is False
    finally:
        if os.path.exists(temp_input):
            os.remove(temp_input)
