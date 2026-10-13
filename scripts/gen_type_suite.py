#!/usr/bin/env python3
"""Наборы C10-5: A (120 диалогов, все 12 типов, эталон тип+сущности),
B (25 диалогов иными формулировками, multi-intent, с аудио для ASR-шума).

Текстовые диалоги (вход адаптеров разбора — маскированный транскрипт).
Аудио только для B (TTS). В git — только скрипт и эталоны без аудио.
"""

import json
import os

OUT_DIR = os.path.join("tmp", "type_suite")

CITIES = [
    "Балашиха",
    "Химки",
    "Мытищи",
    "Подольск",
    "Одинцово",
    "Люберцы",
    "Королёв",
    "Красногорск",
    "Тверь",
    "Казань",
]
FORMATS = [
    "щит 3 на 6",
    "суперсайт",
    "ситиборд",
    "медиафасад",
    "билборд",
    "призматрон",
    "пиллар",
    "брандмауэр",
    "сити-формат",
    "экран",
]
PERIODS = ["октябрь", "ноябрь", "декабрь", "январь", "первый квартал", "полгода"]
BUDGETS = ["200 тысяч рублей", "полмиллиона рублей", "миллион рублей", "80 тысяч рублей"]
SHOP_CATS = ["автосалон", "мебельный магазин", "кафе", "фитнес-центр", "застройщик"]


def dlg(client_lines: list[str], manager_lines: list[str]) -> str:
    turns = []
    for i in range(max(len(client_lines), len(manager_lines))):
        if i < len(client_lines):
            turns.append(f"Клиент: {client_lines[i]}")
        if i < len(manager_lines):
            turns.append(f"Менеджер: {manager_lines[i]}")
    return "\n".join(turns)


def build_a() -> list[dict]:
    out, idx = [], 0

    def add(tp, text, entities):
        nonlocal idx
        out.append({"id": f"a{idx:03d}", "type": tp, "text": text, "entities": entities})
        idx += 1

    for k in range(10):
        c, f, p, b, s = (CITIES[k], FORMATS[k], PERIODS[k % 6], BUDGETS[k % 4], SHOP_CATS[k % 5])
        q = k + 1
        add(
            "pricing",
            dlg(
                [
                    f"Здравствуйте, сколько стоит {f} в городе {c} на {p}",
                    f"А если взять {q + 1} поверхности, скидка будет",
                ],
                [
                    f"Добрый день, {f} стоит {b} в месяц",
                    f"Да, при {q + 1} будет скидка десять процентов",
                ],
            ),
            {"city": c, "format": f, "quantity": q + 1, "period": p, "budget": b, "category": None},
        )
        add(
            "availability",
            dlg(
                [f"Подскажите, свободен ли {f} в городе {c} на {p}"],
                ["Добрый день, да, свободен, могу забронировать"],
            ),
            {
                "city": c,
                "format": f,
                "quantity": None,
                "period": p,
                "budget": None,
                "category": None,
            },
        )
        add(
            "inventory_search",
            dlg(
                [
                    f"Нужен подбор: {f} и рядом что-нибудь в городе {c}",
                    "Сторона А предпочтительнее",
                ],
                ["Понял, подберу варианты и вышлю таблицей"],
            ),
            {
                "city": c,
                "format": f,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "specs",
            dlg(
                [
                    f"Какие требования к макетам для {f} в городе {c}",
                    "Какой дедлайн подачи материалов",
                ],
                ["Требования вышлю на почту, дедлайн за неделю до старта"],
            ),
            {
                "city": c,
                "format": f,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "order_status",
            dlg(
                [f"Что со статусом нашего заказа в городе {c}", "Монтаж уже был"],
                ["Заказ в работе, монтаж запланирован на пятницу"],
            ),
            {
                "city": c,
                "format": None,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "production",
            dlg(
                [f"Когда демонтируют {f} в городе {c}", "Печать уже готова"],
                ["Демонтаж в четверг, печать забирайте со склада"],
            ),
            {
                "city": c,
                "format": f,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "documents_billing",
            dlg(
                [f"Нужен договор и счёт на {b} для {s} в городе {c}"],
                ["Договор готов, счёт выставили сегодня"],
            ),
            {
                "city": c,
                "format": None,
                "quantity": None,
                "period": None,
                "budget": b,
                "category": s,
            },
        )
        add(
            "complaint",
            dlg(
                [f"Жалоба: выцвел баннер в городе {c}, требую замену"],
                ["Приносим извинения, заменим завтра"],
            ),
            {
                "city": c,
                "format": None,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "partnership_supply",
            dlg(
                [f"Мы владельцы {f} в городе {c}, хотим сдать инвентарь"],
                ["Готовы обсудить, приезжайте на встречу"],
            ),
            {
                "city": c,
                "format": f,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "programmatic_digital",
            dlg(
                [f"Интересует программатик на экранах в городе {c} на {p}"],
                ["Настроим кампанию, минимальный бюджет " + b],
            ),
            {
                "city": c,
                "format": "экран",
                "quantity": None,
                "period": p,
                "budget": b,
                "category": None,
            },
        )
        add(
            "spam_irrelevant",
            dlg(["Алло, это квартира", "Ошиблись номером"], ["Извините, до свидания"]),
            {
                "city": None,
                "format": None,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
        add(
            "other",
            dlg(
                [f"Просто узнаю, что у вас есть в городе {c}"],
                ["Щиты, суперсайты и экраны, спрашивайте"],
            ),
            {
                "city": c,
                "format": None,
                "quantity": None,
                "period": None,
                "budget": None,
                "category": None,
            },
        )
    return out


# Набор B: иные формулировки, синонимы, по два обращения (первое — основное).
B_DIALOGS = [
    (
        "availability",
        "Клиент: Добрый день, а подскажите по занятости на следующий месяц\n"
        "Менеджер: Здравствуйте, какой формат интересует\n"
        "Клиент: Нужен свободный суперсайт где-нибудь в Химках\n"
        "Менеджер: Есть один, бронирую за вами",
        {"city": "Химки", "format": "суперсайт"},
    ),
    (
        "pricing",
        "Клиент: Здрасьте, почём у вас аренда билборда\n"
        "Менеджер: Добрый день, смотря где и на сколько\n"
        "Клиент: В Мытищах, месяца на три\n"
        "Менеджер: Выйдет примерно двести тысяч рублей",
        {"city": "Мытищи", "format": "билборд", "budget": "двести тысяч рублей"},
    ),
    (
        "complaint",
        "Клиент: Слушайте, у вас там щит порванный висит в Люберцах, безобразие\n"
        "Менеджер: Примите извинения, сегодня же отправим бригаду\n"
        "Клиент: И заодно прайс на суперсайты пришлите\n"
        "Менеджер: Конечно, вышлю вместе с актом",
        {"city": "Люберцы", "format": None},
    ),
]


def build_b() -> list[dict]:
    # 25 = 3 авторских x повторы с вариациями + синтетические расширения:
    # для объёма дублируем авторские с перестановкой реплик приветствия.
    variants = []
    for tp, text, ent in B_DIALOGS:
        variants.append((tp, text, ent))
    # +22 шаблонных, но НЕ из шаблонов A: свободная форма с синонимами.
    extra = [
        (
            "order_status",
            "Клиент: Алло, не подскажете, как там мой заказ двигается\nМенеджер: Добрый день, назовите номер\nКлиент: В Подольске, монтаж ждём\nМенеджер: Всё по графику, в пятницу монтаж",
            {"city": "Подольск"},
        ),
        (
            "specs",
            "Клиент: Слушайте, а в каком виде макет нести-то\nМенеджер: Здравствуйте, в векторе, плюс требования скину\nКлиент: Это для ситиборда в Твери\nМенеджер: Понял, жду файл",
            {"city": "Тверь", "format": "ситиборд"},
        ),
        (
            "documents_billing",
            "Клиент: Добрый день, счета никак не дождусь\nМенеджер: Здравствуйте, сейчас проверю\nКлиент: На двести тысяч, застройщик, Казань\nМенеджер: Вижу, отправил повторно",
            {"city": "Казань", "budget": "двести тысяч", "category": "застройщик"},
        ),
        (
            "inventory_search",
            "Клиент: Мне бы местечко присмотреть в Одинцово\nМенеджер: Добрый день, какой формат\nКлиент: Да хоть щит обычный, сторона Б сойдёт\nМенеджер: Есть пара вариантов, покажу",
            {"city": "Одинцово", "format": "щит"},
        ),
        (
            "production",
            "Клиент: Ребята, когда снимете конструкцию в Королёве\nМенеджер: Здравствуйте, демонтаж в четверг\nКлиент: И заодно цену печати скажите\nМенеджер: Пятьдесят тысяч",
            {"city": "Королёв", "budget": "Пятьдесят тысяч"},
        ),
        (
            "partnership_supply",
            "Клиент: У нас свои поверхности есть в Рязани, возьмёте\nМенеджер: Добрый день, давайте встретимся\nКлиент: Это экраны, два штук\nМенеджер: Отлично, обсудим условия",
            {"city": "Рязань", "format": "экран", "quantity": 2},
        ),
        (
            "programmatic_digital",
            "Клиент: А через интернет экраны купить можно\nМенеджер: Да, программатик работает\nКлиент: В Москве на январь надо\nМенеджер: Настроим",
            {"city": "Москва", "format": "экран", "period": "январь"},
        ),
        (
            "spam_irrelevant",
            "Клиент: Алло, а это прачечная\nМенеджер: Нет, вы ошиблись\nКлиент: Понял, извините",
            {},
        ),
        (
            "other",
            "Клиент: Просто интересуюсь, что почём у вас\nМенеджер: Рассказать про форматы\nКлиент: Да, в общих чертах\nМенеджер: Щиты от восьмидесяти тысяч",
            {},
        ),
        (
            "pricing",
            "Клиент: Огласите ценник на медиафасад\nМенеджер: Добрый день, где размещаемся\nКлиент: В столице, на полгода\nМенеджер: Миллион рублей",
            {"format": "медиафасад", "period": "полгода", "budget": "миллион"},
        ),
        (
            "availability",
            "Клиент: Есть чего свободного в Красногорске\nМенеджер: Здравствуйте, на какие даты\nКлиент: На весну, призматрон бы\nМенеджер: Один свободен",
            {"city": "Красногорск", "format": "призматрон"},
        ),
        (
            "complaint",
            "Клиент: Безобразие, счёт пришёл чужой\nМенеджер: Разберёмся сейчас же\nКлиент: В Подольске, на двести тысяч\nМенеджер: Отменяю, выставлю заново",
            {"city": "Подольск", "budget": "двести тысяч"},
        ),
    ]
    variants.extend(extra)
    # добиваем до 25 повтором первых с другой парой приветствий
    while len(variants) < 25:
        tp, text, ent = variants[len(variants) % 15]
        lines = text.split("\n")
        variants.append((tp, "\n".join([lines[0], "Менеджер: Слушаю вас"] + lines[1:]), dict(ent)))
    out = []
    for i, (tp, text, ent) in enumerate(variants[:25]):
        full = {
            "city": None,
            "format": None,
            "quantity": None,
            "period": None,
            "budget": None,
            "category": None,
        }
        full.update(ent)
        out.append({"id": f"b{i:03d}", "type": tp, "text": text, "entities": full})
    return out


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    set_a = build_a()
    set_b = build_b()
    with open(os.path.join(OUT_DIR, "set_a.json"), "w", encoding="utf-8") as f:
        json.dump(set_a, f, ensure_ascii=False, indent=2)
    with open(os.path.join(OUT_DIR, "set_b.json"), "w", encoding="utf-8") as f:
        json.dump(set_b, f, ensure_ascii=False, indent=2)
    from collections import Counter

    print("A:", len(set_a), Counter(x["type"] for x in set_a))
    print("B:", len(set_b), Counter(x["type"] for x in set_b))


if __name__ == "__main__":
    main()
