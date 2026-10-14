#!/usr/bin/env python3
"""Генератор DER-набора C10-4: 30 синтетических диалогов 2-5 мин.

Два TTS-голоса (второй — pitch-shift через ffmpeg), паузы и перебивания,
моно и стерео, телефонная деградация (8 кГц, G.711). Эталон — из генератора.
Аудио в git не входит (tmp/der_suite/).
"""

import json
import os
import subprocess

OUT_DIR = os.path.join("tmp", "der_suite")

CLIENT_TURNS = [
    "Здравствуйте, подскажите стоимость суперсайта на Ленинградке",
    "Интересует аренда билбордов в Химках на полгода",
    "Свободны ли щиты три на шесть в Балашихе на октябрь",
    "Нужны щиты шесть на три вдоль МКАД сторона А",
    "Пришлите прайс на суперсайты и скидки за полгода",
    "Свободен ли экран на Тверской на январь",
    "Хотим забронировать медиафасад на декабрь",
    "Какая цена печати и монтажа для брандмауэра",
    "Подскажите требования к макетам для сити-формата",
    "Нужен договор и счёт на оплату за октябрь",
    "Жалоба на выцветший баннер в Люберцах",
    "Мы владельцы щитов в Королёве хотим сдать инвентарь",
    "Интересует программатик на диджитал экранах",
    "Свободны ли поверхности Б на проспекте Мира",
    "Сколько стоит сторона Б щита в Химках",
    "Подскажите занятость суперсайтов в Москве",
    "Нужен подбор мест под запуск в Красногорске",
    "Какая цена аренды пиллара на месяц",
    "Призматрон на Ленинградке ещё свободен",
    "Договор на размещение уже готов",
]

MANAGER_TURNS = [
    "Добрый день, агентство наружной рекламы, слушаю вас",
    "Стоимость двести тысяч рублей в месяц",
    "Да, свободны, могу забронировать на октябрь",
    "Предлагаю варианты вдоль МКАД сторона А и Б",
    "Высылаю прайс и скидку за полугодовое размещение",
    "Экран свободен, готов оформить бронь",
    "Медиафасад доступен, фиксируем декабрь",
    "Печать и монтаж стоят пятьдесят тысяч рублей",
    "Требования вышлю на почту сегодня",
    "Договор и счёт готовы, проверьте почту",
    "Приносим извинения, заменим баннер завтра",
    "Готовы обсудить ваш инвентарь на встрече",
    "Программатик доступен, настроим кампанию",
    "Поверхности свободны, бронируем",
    "Сторона Б стоит сто тысяч рублей",
    "Занятость вышлю таблицей",
    "Подборку мест подготовлю к пятнице",
    "Пиллар стоит восемьдесят тысяч в месяц",
    "Призматрон свободен",
    "Договор готов, жду подпись",
]


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, capture_output=True, check=True)


def tts_turn(text: str, path: str, pitch: float | None = None) -> float:
    """Озвучка одного хода; возвращает длительность (с)."""
    import soundfile as sf
    from gtts import gTTS

    mp3 = path.replace(".wav", ".mp3")
    gTTS(text, lang="ru").save(mp3)
    filt = f"asetrate=16000*{pitch},aresample=16000" if pitch else "anull"
    run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            mp3,
            "-af",
            filt,
            "-ar",
            "16000",
            "-ac",
            "1",
            path,
        ]
    )
    os.remove(mp3)
    return sf.info(path).duration


def build_turn_bank() -> dict[str, list[tuple[str, float]]]:
    import soundfile as sf

    bank_dir = os.path.join(OUT_DIR, "_turns")
    os.makedirs(bank_dir, exist_ok=True)
    bank: dict[str, list[tuple[str, float]]] = {"client": [], "manager": []}
    for i, text in enumerate(CLIENT_TURNS):
        p = os.path.join(bank_dir, f"c{i:02d}.wav")
        if not os.path.exists(p):
            d = tts_turn(text, p)
        else:
            d = sf.info(p).duration
        bank["client"].append((p, d))
    for i, text in enumerate(MANAGER_TURNS):
        p = os.path.join(bank_dir, f"m{i:02d}.wav")
        if not os.path.exists(p):
            d = tts_turn(text, p, pitch=0.82)
        else:
            d = sf.info(p).duration
        bank["manager"].append((p, d))
    return bank


def assemble(idx: int, bank: dict, stereo: bool, seed: int) -> dict:
    import numpy as np
    import soundfile as sf

    rng = __import__("random").Random(seed)
    sr = 16000
    # 14-30 пар ходов -> 2-5 мин
    n_pairs = rng.randint(14, 30)
    order = []
    for k in range(n_pairs):
        order.append(("client", bank["client"][(idx + k) % len(bank["client"])]))
        order.append(("manager", bank["manager"][(idx * 2 + k) % len(bank["manager"])]))
    total = sum(d for _, (_, d) in order) + len(order) * 0.8 + 5.0
    left = np.zeros(int(total * sr), dtype=np.float32)
    right = np.zeros(int(total * sr), dtype=np.float32)
    t = 0.5
    truth = []
    for k, (role, (path, dur)) in enumerate(order):
        data, _ = sf.read(path, dtype="float32")
        # перебивание: каждый ~6-й ход начинается раньше конца предыдущего
        if k > 0 and k % 6 == 0:
            t = max(0.0, t - rng.uniform(0.3, 0.8))
        else:
            t += rng.uniform(0.3, 1.2)
        s0 = int(t * sr)
        s1 = min(len(left), s0 + len(data))
        seg = data[: s1 - s0]
        if stereo:
            if role == "client":
                left[s0:s1] += seg
            else:
                right[s0:s1] += seg
        else:
            left[s0:s1] += seg
            right[s0:s1] += seg * 0.0
        truth.append(
            {"start": round(t, 2), "end": round(t + len(seg) / sr, 2), "speaker_role": role}
        )
        t += len(seg) / sr
    dur_total = t + 0.5
    if stereo:
        mix = np.stack([left[: int(dur_total * sr)], right[: int(dur_total * sr)]], axis=1)
        ch = 2
    else:
        mix = left[: int(dur_total * sr)]
        ch = 1
    raw_path = os.path.join(OUT_DIR, f"d{idx:02d}_{'stereo' if stereo else 'mono'}_16k.wav")
    sf.write(raw_path, mix, sr)
    tel_path = raw_path.replace("_16k.wav", "_8k.wav")
    run(
        [
            "ffmpeg",
            "-nostdin",
            "-y",
            "-v",
            "error",
            "-i",
            raw_path,
            "-af",
            "highpass=f=300,lowpass=f=3400",
            "-ar",
            "8000",
            "-ac",
            str(ch),
            "-c:a",
            "pcm_alaw",
            tel_path,
        ]
    )
    return {
        "id": f"d{idx:02d}",
        "stereo": stereo,
        "duration": round(dur_total, 2),
        "segments": truth,
        "files": {"16k": raw_path, "8k": tel_path},
    }


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    bank = build_turn_bank()
    manifest = []
    for idx in range(30):
        entry = assemble(idx, bank, stereo=(idx % 2 == 1), seed=1000 + idx)
        manifest.append(entry)
        print(
            entry["id"],
            "stereo" if entry["stereo"] else "mono",
            entry["duration"],
            len(entry["segments"]),
            flush=True,
        )
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
