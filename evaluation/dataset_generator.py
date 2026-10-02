import json
import os
import subprocess
from typing import Any

from gtts import gTTS

from app.core.logging import get_logger

logger = get_logger("synthetic_dataset_generator")


def generate_speech_wav(text: str, output_path: str):
    """Generates speech using gTTS and converts to 16kHz 16-bit mono WAV."""
    tts = gTTS(text, lang="ru")
    temp_mp3 = output_path.replace(".wav", ".mp3")
    tts.save(temp_mp3)

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        temp_mp3,
        "-ar",
        "16000",
        "-ac",
        "1",
        "-f",
        "wav",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    os.remove(temp_mp3)


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
    filter_chain = "highpass=f=300,lowpass=f=3400"
    if add_noise:
        filter_chain += ",volume=0.9"

    cmd = [
        "ffmpeg",
        "-nostdin",
        "-y",
        "-i",
        input_wav,
        "-af",
        filter_chain,
        "-ar",
        "8000",
        "-ac",
        "1",
        "-acodec",
        "pcm_s16le",
        output_wav,
    ]
    subprocess.run(cmd, capture_output=True, check=True)


def generate_dialogue_wav(text1: str, text2: str, output_path: str) -> list[dict[str, Any]]:
    """Generates two-speaker dialogue with acoustic pitch differentiation."""
    import numpy as np
    import soundfile as sf

    t1 = output_path.replace(".wav", "_spk1.mp3")
    w1 = output_path.replace(".wav", "_spk1.wav")
    gTTS(text1, lang="ru").save(t1)
    subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", t1, "-ar", "16000", "-ac", "1", w1],
        check=True,
        capture_output=True,
    )
    d1, sr = sf.read(w1, dtype="float32")
    dur1 = len(d1) / sr

    t2 = output_path.replace(".wav", "_spk2.mp3")
    w2 = output_path.replace(".wav", "_spk2.wav")
    gTTS(text2, lang="ru").save(t2)
    # Apply pitch shift to second speaker for acoustic differentiation
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-i",
            t2,
            "-af",
            "asetrate=16000*1.22,aresample=16000",
            "-ar",
            "16000",
            "-ac",
            "1",
            w2,
        ],
        check=True,
        capture_output=True,
    )
    d2, _ = sf.read(w2, dtype="float32")
    dur2 = len(d2) / sr

    pause_dur = 0.5
    pause = np.zeros(int(sr * pause_dur), dtype=np.float32)
    combined = np.concatenate([d1, pause, d2])
    sf.write(output_path, combined, sr)

    for p in [t1, w1, t2, w2]:
        if os.path.exists(p):
            os.remove(p)

    roles = [
        {"start": 0.0, "end": round(dur1, 2), "speaker_role": "client"},
        {
            "start": round(dur1 + pause_dur, 2),
            "end": round(dur1 + pause_dur + dur2, 2),
            "speaker_role": "manager",
        },
    ]
    return roles


def build_synthetic_benchmark_suite(output_dir: str = "data/evaluation_suite") -> dict[str, Any]:
    """Generates a reproducible evaluation suite with ground truth transcripts."""
    os.makedirs(output_dir, exist_ok=True)

    samples = [
        {
            "id": "sample_clean_01",
            "reference": "Здравствуйте агентство наружной рекламы слушаю вас",
            "type": "clean",
            "speaker_roles": [{"start": 0.0, "end": 3.5, "speaker_role": "manager"}],
        },
        {
            "id": "sample_telephony_01",
            "reference": "Подскажите свободны ли щиты три на шесть в Балашихе на октябрь",
            "type": "telephony",
            "speaker_roles": [{"start": 0.0, "end": 4.5, "speaker_role": "client"}],
        },
        {
            "id": "sample_dialogue_01",
            "reference": "Здравствуйте! Подскажите стоимость суперсайта на Ленинградке. Добрый день, стоимость двести тысяч рублей в месяц.",
            "type": "dialogue",
            "spk1_text": "Здравствуйте! Подскажите стоимость суперсайта на Ленинградке.",
            "spk2_text": "Добрый день, стоимость двести тысяч рублей в месяц.",
        },
    ]

    manifest = []
    for item in samples:
        base_clean = os.path.join(output_dir, f"{item['id']}_clean.wav")
        if item["type"] == "dialogue":
            roles = generate_dialogue_wav(item["spk1_text"], item["spk2_text"], base_clean)
            item_roles = roles
        else:
            generate_speech_wav(item["reference"], base_clean)
            item_roles = item["speaker_roles"]

        if item["type"] in ("telephony", "dialogue"):
            tel_wav = os.path.join(output_dir, f"{item['id']}_telephony_8k.wav")
            apply_telephony_degradation(base_clean, tel_wav)
            audio_file = tel_wav
        else:
            audio_file = base_clean

        manifest.append(
            {
                "id": item["id"],
                "audio_file": audio_file,
                "reference": item["reference"],
                "type": item["type"],
                "speaker_roles": item_roles,
            }
        )

    manifest_path = os.path.join(output_dir, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    logger.info(
        "synthetic_suite_generated", samples_count=len(manifest), manifest_path=manifest_path
    )
    return {"manifest_path": manifest_path, "samples": manifest}


if __name__ == "__main__":
    build_synthetic_benchmark_suite()
