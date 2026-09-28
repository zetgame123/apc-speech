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
