from app.asr.normalizer import RussianTextNormalizer


def test_billboard_dimensions_normalization():
    normalizer = RussianTextNormalizer()

    assert (
        normalizer.normalize("нам нужен щит три на шесть в Балашихе")
        == "нам нужен щит 3×6 в Балашихе"
    )
    assert normalizer.normalize("суперсайт шесть на три свободен") == "суперсайт 6×3 свободен"
    assert normalizer.normalize("конструкция 3х6 или 3*6") == "конструкция 3×6 или 3×6"
    assert normalizer.normalize("суперсайт пять на двенадцать") == "суперсайт 5×12"
    assert normalizer.normalize("формат 1,2х1,8") == "формат 1.2×1.8"


def test_time_normalization():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("ролик на пятнадцать секунд") == "ролик на 15 с"
    assert normalizer.normalize("хронометраж двадцать секунд") == "хронометраж 20 с"
    assert normalizer.normalize("размещение на два месяца") == "размещение на 2 мес."


def test_currency_normalization():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("бюджет сто тысяч рублей") == "бюджет 100 000 руб."
    assert normalizer.normalize("стоимость 50000 рублей") == "стоимость 50 000 руб."
    assert (
        normalizer.normalize("до 50 000 руб на автозаводской") == "до 50 000 руб. на Автозаводской"
    )
    assert normalizer.normalize("до пятидесяти тысяч рублей") == "до 50 000 руб."
    assert normalizer.normalize("от 30 до 80 тысяч рублей") == "от 30 000 до 80 000 руб."
    assert normalizer.normalize("до 50 тыс") == "до 50 000 руб."
    assert normalizer.normalize("до 100к") == "до 100 000 руб."


def test_metro_stations_capitalization():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("щиты на автозаводской") == "щиты на Автозаводской"
    assert normalizer.normalize("около таганской и на соколе") == "около Таганской и на Соколе"
    assert (
        normalizer.normalize("метро бауманская и китай-город") == "метро Бауманская и Китай-город"
    )
    assert normalizer.normalize("экраны на вднх") == "экраны на ВДНХ"
    assert normalizer.normalize("щиты у парка культуры") == "щиты у Парка культуры"


def test_highways_and_rings():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("щит на ленинградке") == "щит на Ленинградке"
    assert normalizer.normalize("экраны на мкад и ттк") == "экраны на МКАД и ТТК"
    assert (
        normalizer.normalize("размещение на шоссе энтузиастов") == "размещение на Шоссе Энтузиастов"
    )
    assert normalizer.normalize("суперсайт на новой риге") == "суперсайт на Новой Риге"
    assert normalizer.normalize("баннер на варшавке") == "баннер на Варшавке"


def test_cities_and_homophones():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("щиты в балашихе и химках") == "щиты в Балашихе и Химках"
    assert normalizer.normalize("аренда счетов 3х6 около мытищ") == "аренда щитов 3×6 около Мытищ"
    assert normalizer.normalize("счета на ленинградке") == "щиты на Ленинградке"


def test_terms_and_formats():
    normalizer = RussianTextNormalizer()

    assert normalizer.normalize("сити формат сторона а") == "сити-формат сторона А"
    assert normalizer.normalize("супер сайт сторона б") == "суперсайт сторона Б"
    assert normalizer.normalize("диджитал экраны с высоким grp") == "digital-экраны с высоким GRP"
