#!/usr/bin/env python3
"""Генерация большой выборки C4: >=50 синтетических коротких запросов (5-15 с).

TTS (gTTS, ru) -> 16 кГц clean + 8 кГц с телефонной деградацией.
Сами аудио в git НЕ коммитятся (вывод в tmp/c4suite/), коммитится только скрипт
и manifest.json с эталонами (текст = вход TTS).
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from evaluation.dataset_generator import apply_telephony_degradation

OUT_DIR = os.path.join("tmp", "c4suite")

# Фразы из ТЗ + таксономия обращений + термины/города из словаря.
TEXTS = [
    "Подскажите, свободны ли щиты три на шесть в Балашихе на октябрь",
    "Здравствуйте, агентство наружной рекламы, слушаю вас",
    "Сколько стоит суперсайт на Ленинградском шоссе на ноябрь",
    "Интересует аренда билбордов в Химках на полгода",
    "Подскажите стоимость размещения на ситибордах в Мытищах",
    "Нужны щиты шесть на три вдоль МКАД, сторона А",
    "Какая цена печати и монтажа для брандмауэра",
    "Хотим забронировать медиафасад на декабрь, сколько стоит",
    "Пришлите прайс на суперсайты и скидки за полугодовое размещение",
    "Свободен ли экран на Тверской на январь",
    "Статус нашего заказа на монтаж в Подольске",
    "Когда демонтируют щит на шоссе Энтузиастов",
    "Хочу разместить рекламу пива на щитах в Одинцово",
    "Подскажите требования к макетам для сити-формата",
    "Нужен договор и счёт на оплату за октябрь",
    "Жалоба на выцветший баннер в Люберцах",
    "Мы владельцы щитов в Королёве, хотим сдать инвентарь",
    "Интересует программатик на диджитал экранах",
    "Свободны ли поверхности Б на проспекте Мира на весну",
    "Сколько стоит сторона Б щита три на шесть в Химках",
    "Подскажите занятость суперсайтов пять на двенадцать в Москве",
    "Нужен подбор мест под запуск в Красногорске",
    "Какая цена аренды пиллара на месяц",
    "Призматрон на Ленинградке ещё свободен",
    "Договор на размещение уже готов",
]
# Удлинители для диапазона 5-15 с: вторая реплика через паузу склейкой диалога.
FOLLOWUPS = [
    "Алло, вы меня слышите",
    "Повторите, пожалуйста, цену",
    "А скидка за три месяца будет",
    "Хорошо, тогда бронируйте",
    "Спасибо, жду договор на почту",
]


def build_suite() -> dict:
    from evaluation.dataset_generator import generate_dialogue_wav

    os.makedirs(OUT_DIR, exist_ok=True)
    manifest = []
    idx = 0
    for i, text in enumerate(TEXTS):
        follow = FOLLOWUPS[i % len(FOLLOWUPS)]
        base = os.path.join(OUT_DIR, f"q{idx:02d}_16k.wav")
        tel = os.path.join(OUT_DIR, f"q{idx:02d}_8k.wav")
        if not (os.path.exists(base) and os.path.exists(tel)):
            for attempt in range(3):
                try:
                    generate_dialogue_wav(text, follow, base)
                    apply_telephony_degradation(base, tel)
                    break
                except Exception as e:
                    print(f"retry {idx} ({e})")
                    time.sleep(5)
            else:
                print(f"SKIP q{idx:02d}: TTS failed")
                idx += 1
                continue
        import soundfile as sf

        d16, sr16 = sf.read(base)
        d8, sr8 = sf.read(tel)
        manifest.append(
            {
                "id": f"q{idx:02d}",
                "reference": f"{text}. {follow}.",
                "files": {
                    "16k": {"path": base, "duration": round(len(d16) / sr16, 2)},
                    "8k": {"path": tel, "duration": round(len(d8) / sr8, 2)},
                },
            }
        )
        idx += 1
    manifest_path = os.path.join(OUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    durs = [
        v for m in manifest for v in (m["files"]["16k"]["duration"], m["files"]["8k"]["duration"])
    ]
    print(f"сэмплов: {len(manifest)} запросов x 2 частоты = {len(durs)} файлов")
    print(f"длительность: мин {min(durs):.1f} с, макс {max(durs):.1f} с")
    return {"manifest": manifest_path, "count": len(manifest)}


if __name__ == "__main__":
    build_suite()
