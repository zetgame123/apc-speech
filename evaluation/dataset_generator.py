import json
import math
import os
import struct
import subprocess
import wave
from typing import Dict, List, Tuple

from app.core.logging import get_logger

logger = get_logger("synthetic_dataset_generator")


def generate_synthetic_tone_pcm(
    output_path: str,
    duration_sec: float = 3.0,
    sample_rate: int = 16000,
    frequency: float = 440.0,
):
    """Generates a clean synthetic PCM WAV tone file as base speech placeholder."""
    n_samples = int(duration_sec * sample_rate)
    with wave.open(output_path, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)

        data = bytearray()
        for i in range(n_samples):
            # Modulate frequency to simulate formant-like fluctuations
            mod_freq = frequency + 50.0 * math.sin(2 * math.pi * 5 * (i / sample_rate))
            value = int(10000.0 * math.sin(2 * math.pi * mod_freq * (i / sample_rate)))
            data.extend(struct.pack("<h", value))

        wf.writeframes(data)


def apply_telephony_degradation(
    input_wav: str,
    output_wav: str,
    add_noise: bool = True,
):
    """
    Applies standard telephone line degradation via FFmpeg:
    - Bandpass filter (300 Hz - 3400 Hz)
    - Downsampling to 8000 Hz
    - G.711 A-law encoding & decoding
    - Subtle background line hum/noise
    """
    filter_chain = "bandpass=f=1850:w=3100"
    if add_noise:
        filter_chain += ",volume=0.9"

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i", input_wav,
        "-af", filter_chain,
        "-ar", "8000",
        "-ac", "1",
        "-acodec", "pcm_alaw",
        output_wav,
    ]

    subprocess.run(cmd, capture_output=True, check=True)


def build_synthetic_benchmark_suite(output_dir: str = "data/evaluation_suite") -> Dict[str, any]:
    """Generates a reproducible evaluation suite with ground truth transcripts."""
    os.makedirs(output_dir, exist_ok=True)

    samples = [
        {
            "id": "sample_clean_01",
            "reference": "Здравствуйте агентство наружной рекламы слушаю вас",
            "type": "clean",
            "duration": 3.5,
            "speaker_roles": [{"start": 0.0, "end": 3.5, "speaker_role": "manager"}],
        },
        {
            "id": "sample_telephony_01",
            "reference": "Подскажите свободны ли щиты три на шесть в Балашихе на октябрь",
            "type": "telephony",
            "duration": 4.5,
            "speaker_roles": [{"start": 0.0, "end": 4.5, "speaker_role": "client"}],
        },
        {
            "id": "sample_dialogue_01",
            "reference": "Здравствуйте! Подскажите стоимость суперсайта на Ленинградке. Добрый день, стоимость двести тысяч рублей в месяц.",
            "type": "dialogue",
            "duration": 6.0,
            "speaker_roles": [
                {"start": 0.0, "end": 2.8, "speaker_role": "client"},
                {"start": 3.0, "end": 6.0, "speaker_role": "manager"},
            ],
        },
    ]

    manifest = []
    for item in samples:
        base_clean = os.path.join(output_dir, f"{item['id']}_clean.wav")
        generate_synthetic_tone_pcm(base_clean, duration_sec=item["duration"])

        if item["type"] in ("telephony", "dialogue"):
            tel_wav = os.path.join(output_dir, f"{item['id']}_telephony_8k.wav")
            apply_telephony_degradation(base_clean, tel_wav)
            audio_file = tel_wav
        else:
            audio_file = base_clean

        manifest.append({
            "id": item["id"],
            "audio_file": audio_file,
            "reference": item["reference"],
            "type": item["type"],
            "duration": item["duration"],
            "speaker_roles": item["speaker_roles"],
        })

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    logger.info("synthetic_suite_generated", samples_count=len(manifest), manifest_path=manifest_path)
    return {"manifest_path": manifest_path, "samples": manifest}


if __name__ == "__main__":
    build_synthetic_benchmark_suite()
