import pytest

from app.pii.masker import PiiMasker


def test_mask_phone_numbers():
    masker = PiiMasker()
    text = "Позвоните мне по номеру +7 (926) 123-45-67 или 89165554433"
    masked = masker.mask_text(text)

    assert "+7 (926) 123-45-67" not in masked
    assert "89165554433" not in masked
    assert "[ТЕЛЕФОН_1]" in masked
    assert "[ТЕЛЕФОН_2]" in masked


def test_mask_email():
    masker = PiiMasker()
    text = "Отправьте КП на почту manager@outdoor-agency.ru пожалуйста"
    masked = masker.mask_text(text)

    assert "manager@outdoor-agency.ru" not in masked
    assert "[EMAIL]" in masked


def test_mask_bank_card_and_passport():
    masker = PiiMasker()
    text = "Карта для оплаты 4276 3800 1234 5678, паспорт 4510 123456"
    masked = masker.mask_text(text)

    assert "4276 3800 1234 5678" not in masked
    assert "4510 123456" not in masked
    assert "[КАРТА]" in masked
    assert "[ПАСПОРТ]" in masked


def test_assert_no_leaked_pii():
    masker = PiiMasker()
    safe_text = "Клиент просил щит 3×6 [ТЕЛЕФОН_1] [EMAIL]"
    assert masker.assert_no_leaked_pii(safe_text) is True

    unsafe_text = "Клиент оставил карту 4276 3800 1234 5678 для списания"
    with pytest.raises(ValueError, match="PII Leak"):
        masker.assert_no_leaked_pii(unsafe_text)

    unsafe_text_email = "Почта test@example.com"
    with pytest.raises(ValueError, match="PII Leak"):
        masker.assert_no_leaked_pii(unsafe_text_email)

    unsafe_text_phone = "Мой телефон +7 926 123 45 67"
    with pytest.raises(ValueError, match="PII Leak"):
        masker.assert_no_leaked_pii(unsafe_text_phone)

    unsafe_text_passport = "Вот паспорт 4510 123456"
    with pytest.raises(ValueError, match="PII Leak"):
        masker.assert_no_leaked_pii(unsafe_text_passport)


def test_mask_person_names():
    masker = PiiMasker()
    text = "Звонил Иван Петров, уточнял смету для Анна Смирнова"
    masked = masker.mask_text(text)

    assert "Иван Петров" not in masked
    assert "Анна Смирнова" not in masked
    assert "[ИМЯ_1]" in masked
    assert "[ИМЯ_2]" in masked


def test_mask_person_names_consistent_numbering():
    masker = PiiMasker()
    text = "Иван Петров позвонил, потом снова Иван Петров уточнил детали"
    masked = masker.mask_text(text)

    # Same name mentioned twice must reuse the same placeholder number
    assert masked.count("[ИМЯ_1]") == 2
    assert "[ИМЯ_2]" not in masked


def test_assert_no_leaked_pii_detects_unmasked_name():
    masker = PiiMasker()
    unsafe_text_name = "Меня зовут Анна Смирнова, хочу записаться на завтра"
    with pytest.raises(ValueError, match="PII Leak"):
        masker.assert_no_leaked_pii(unsafe_text_name)

    # A text already masked by mask_text() must pass the safety check
    safe_text = masker.mask_text(unsafe_text_name)
    assert masker.assert_no_leaked_pii(safe_text) is True


def test_mask_person_names_does_not_mask_domain_terms():
    """
    Regression test: Natasha's NewsNERTagger (trained on a news corpus) tags
    capitalized domain terms / toponyms from the outdoor-advertising vocabulary
    (config/vocabulary_hints.yaml) as PER, e.g. "Ситиборды" at the start of a
    sentence. These must be preserved, not replaced with [ИМЯ_N].
    """
    masker = PiiMasker()
    text = "Ситиборды на павелетском вокзале до 500 000 руб."
    masked = masker.mask_text(text)

    assert masked == text
    assert "[ИМЯ_" not in masked

    masker2 = PiiMasker()
    text2 = "Билборды в Балашихе свободны на январь"
    masked2 = masker2.mask_text(text2)
    assert "[ИМЯ_" not in masked2

    # Domain terms must not trigger assert_no_leaked_pii() either
    assert masker.assert_no_leaked_pii(text) is True
