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
