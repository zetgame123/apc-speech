#!/usr/bin/env python3
"""C10-3: доля узнанных терминов с подсказками и без (один набор, TTS из шаблонов).

Генерирует ~20 фраз, насыщенных терминами config/vocabulary_hints.yaml (TTS gTTS),
прогоняет whisper-small движково с hotwords и без. Ориентир: >= 90% с подсказками.
Аудио в git не входит (tmp/eval_terms/).
"""

import io
import json
import os
import subprocess

import yaml

OUT_DIR = os.path.join("tmp", "eval_terms")

TEMPLATES = [
    "Подскажите, свободен ли {t1} на {t2} в октябре",
    "Сколько стоит {t1} в {t2} на ноябрь",
    "Нужен {t1} рядом с {t2}",
    "Интересует аренда {t1} в {t2}",
    "Какая цена {t1} на {t2}",
    "Свободен ли {t1} в {t2} на декабрь",
    "Хотим забронировать {t1} в {t2}",
    "Подскажите занятость {t1} в {t2}",
    "Нужен прайс на {t1} в {t2}",
    "Когда демонтируют {t1} в {t2}",
]

FORMATS = [
    "суперсайт",
    "ситиборд",
    "медиафасад",
    "билборд",
    "призматрон",
    "пиллар",
    "брандмауэр",
    "сити-формат",
    "щит 3 на 6",
    "суперсайт 5 на 12",
]
CITIES = [
    "Балашихе",
    "Химках",
    "Мытищах",
    "Подольске",
    "Одинцово",
    "Люберцах",
    "Королёве",
    "Красногорске",
    "Люблино",
    "Митино",
]


def build_set() -> list[dict]:
    from gtts import gTTS

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(os.path.join("config", "vocabulary_hints.yaml"), encoding="utf-8") as f:
        hints = yaml.safe_load(f)
    terms = [str(t) for v in hints.values() if isinstance(v, list) for t in v]
    items = []
    for i, (tpl, fmt, city) in enumerate(zip(TEMPLATES * 2, FORMATS * 2, CITIES * 2)):
        text = tpl.format(t1=fmt, t2=city)
        mp3 = os.path.join(OUT_DIR, f"term_{i:02d}.mp3")
        wav = os.path.join(OUT_DIR, f"term_{i:02d}.wav")
        if not os.path.exists(wav):
            gTTS(text, lang="ru").save(mp3)
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-y",
                    "-v",
                    "error",
                    "-i",
                    mp3,
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    wav,
                ],
                capture_output=True,
                check=True,
            )
            os.remove(mp3)
        items.append({"id": f"term_{i:02d}", "reference": text, "wav": wav})
    with open(os.path.join(OUT_DIR, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(
            [{"id": x["id"], "reference": x["reference"]} for x in items],
            f,
            ensure_ascii=False,
            indent=2,
        )
    return items, terms


def run_suite() -> dict:
    from app.asr.engine import AsrEngine
    from evaluation.wer_cer import normalize_eval_text

    items, terms = build_set()

    def term_norm(t: str) -> str:
        try:
            from evaluation.wer_cer import normalize_eval_text as n

            return n(t)
        except Exception:
            import re

            return re.sub(r"[^\\w\\s×]", " ", t.lower().replace("ё", "е"))

    eng = AsrEngine(model_name="./models/whisper-small")
    hotwords = " ".join(sorted({t for t in terms if " " not in t}))
    result = {}
    for label, hw in (("без подсказок", None), ("с подсказками", hotwords)):
        hit = tot = 0
        wers = []
        for item in items:
            with open(item["wav"], "rb") as f:
                segs, _ = eng.transcribe(
                    io.BytesIO(f.read()),
                    language="ru",
                    beam_size=2,
                    temperature=0.0,
                    hotwords=hw,
                    vad_filter=True,
                    word_timestamps=False,
                )
            hyp = " ".join(s["text"] for s in segs)
            ref_n = normalize_eval_text(item["reference"])
            hyp_n = normalize_eval_text(hyp)
            expected = [t for t in terms if term_norm(t).strip() and term_norm(t).strip() in ref_n]
            hit += sum(1 for t in expected if term_norm(t).strip() in hyp_n)
            tot += len(expected)
            from evaluation.wer_cer import calculate_wer_cer

            wer, _ = calculate_wer_cer(item["reference"], hyp)
            wers.append(wer)

        result[label] = {
            "terms": f"{hit}/{tot}={round(hit / tot, 3) if tot else 0}",
            "mean_wer": round(sum(wers) / len(wers), 3),
        }
        print(label, result[label], flush=True)
    with open("reports/terms_hints.json", "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


if __name__ == "__main__":
    run_suite()
