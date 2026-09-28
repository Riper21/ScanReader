# -*- coding: utf-8 -*-
"""
Тесты Шага 3.5 — усиление кросс-модального гейта.

Гейт перестал быть подстрочным поиском по «склеенному» тексту и покрывает
денежные суммы, счета и наименования всех сторон.
"""

import pytest

from scan_reader.verifier.hallucination_gate import (
    MIN_REFERENCE_LENGTH,
    _numeric_atoms,
    _numeric_candidates,
    audit_cross_modal_consistency,
    check_presence_in_raw_text,
    normalize_token,
)

RAW = (
    "Судебный приказ. Взыскать с Иванова Ивана Ивановича, ИНН 7707083893, "
    "дело А40-12345/2019, ИП 98765/23/50026-ИП от 12.04.2023, "
    "счет 30101810400000000225, БИК 044525225, сумму 100 рублей."
)


def _fields(raw=RAW, **kwargs):
    return {d["field"] for d in audit_cross_modal_consistency(kwargs, raw)}


# =========================================================================
# Нормализация и атомы
# =========================================================================
def test_normalize_token_strips_separators():
    assert normalize_token("«Иванов И.И.»") == "ивановии"
    assert normalize_token("7701-234-567") == "7701234567"


def test_numeric_atoms_respect_group_boundaries():
    """Группы цифр НЕ склеиваются в один атом."""
    atoms = _numeric_atoms("224 616,01 руб. и 7707083893")
    assert "22461601" in atoms
    assert "7707083893" in atoms
    assert "2246160117707083893" not in atoms


def test_numeric_candidates_avoid_wrong_scale():
    """100.0 не должен превращаться в «1000» — это другое число."""
    assert _numeric_candidates("100.0") == ["100"]
    assert _numeric_candidates("1000.0") == ["1000"]
    assert "22461601" in _numeric_candidates("224616.01")


# =========================================================================
# Числовые идентификаторы
# =========================================================================
def test_full_inn_is_confirmed():
    assert check_presence_in_raw_text("7707083893", RAW) is True


def test_truncated_identifier_is_rejected():
    """Раньше «770708389» подтверждался внутри «7707083893»."""
    assert check_presence_in_raw_text("770708389", RAW) is False


def test_identifier_inside_longer_number_is_rejected():
    assert check_presence_in_raw_text("7707083893", "номер 177070838930456") is False


def test_alphanumeric_case_number_is_confirmed():
    """Смешанный идентификатор с разделителями подтверждается как целый токен."""
    assert check_presence_in_raw_text("А40-12345/2019", RAW) is True
    assert check_presence_in_raw_text("98765/23/50026-ИП", RAW) is True


def test_fabricated_case_number_is_rejected():
    assert check_presence_in_raw_text("А40-99999/2099", RAW) is False


def test_bank_account_and_bik_are_confirmed():
    assert check_presence_in_raw_text("30101810400000000225", RAW) is True
    assert check_presence_in_raw_text("044525225", RAW) is True


# =========================================================================
# Денежные суммы
# =========================================================================
@pytest.mark.parametrize("value", [100, 100.0, "100"])
def test_money_matches_document_amount(value):
    assert check_presence_in_raw_text(value, RAW) is True


def test_money_matches_grouped_document_format():
    raw = "Взыскать 224 616,01 руб. в пользу взыскателя."
    assert check_presence_in_raw_text(224616.01, raw) is True


def test_fabricated_amount_is_rejected():
    assert check_presence_in_raw_text(999999.0, RAW) is False
    assert check_presence_in_raw_text(15000.0, RAW) is False


def test_tenfold_error_is_detected():
    assert check_presence_in_raw_text(1000.0, RAW) is False


# =========================================================================
# Текстовые значения: границы слов
# =========================================================================
def test_short_token_not_confirmed_inside_longer_word():
    """«ИВАН» больше не подтверждается внутри «ИВАНОВ»."""
    assert check_presence_in_raw_text("ИВАН", "СИДОРОВ ИВАНОВ ИВАНОВИЧ") is False
    assert check_presence_in_raw_text("Иванов", "СИДОРОВ ИВАНОВ ИВАНОВИЧ") is True


def test_very_short_value_is_not_flagged():
    assert check_presence_in_raw_text("12", RAW) is True


# =========================================================================
# Покрытие полей
# =========================================================================
def test_gate_covers_money_fields():
    """Денежные суммы не проверялись вовсе — главный вектор выдумывания."""
    doc = {"finances": {"total_rub": 999999.0}}
    assert "finances.total_rub" in _fields(**doc)


def test_gate_covers_all_party_inns():
    for party in ("party_one", "party_two", "seller", "buyer", "customer",
                  "contractor", "principal", "agent", "sender", "recipient",
                  "employee"):
        doc = {party: {"inn": "7707083890"}}  # заведомо неверный ИНН
        assert f"{party}.inn" in _fields(**doc), f"{party}.inn не проверяется"


def test_gate_covers_party_names():
    doc = {"seller": {"name": "ООО Ромашка-Лютик"}}
    assert "seller.name" in _fields(**doc)


def test_gate_covers_bank_account():
    doc = {"payment_details": {"payment_account": "40702810000000012345"}}
    assert "payment_details.payment_account" in _fields(**doc)


def test_gate_covers_snils():
    doc = {"debtor": {"snils": "11223344556"}}
    assert "debtor.snils" in _fields(**doc)


def test_corroborated_document_produces_no_findings():
    doc = {
        "debtor": {"inn": "7707083893", "name": "Иванов Иван Иванович"},
        "court": {"case_number": "А40-12345/2019"},
        "payment_details": {"bik": "044525225", "payment_account": "30101810400000000225"},
        "finances": {"total_rub": 100.0},
    }
    assert audit_cross_modal_consistency(doc, RAW) == []


def test_ocr_damage_single_letter_is_tolerated():
    """Одна ошибка распознавания не должна превращаться в расхождение."""
    doc = {"debtor": {"name": "Иванова Иван Иванович"}}
    assert "debtor.name" not in _fields(**doc)


# =========================================================================
# Короткий эталон
# =========================================================================
def test_short_reference_returns_nothing():
    short = "текст"
    assert len(short) < MIN_REFERENCE_LENGTH
    assert audit_cross_modal_consistency({"debtor": {"inn": "7707083890"}}, short) == []


def test_empty_reference_returns_nothing():
    assert audit_cross_modal_consistency({"debtor": {"inn": "7707083890"}}, "") == []


def test_non_dict_payload_is_handled():
    assert audit_cross_modal_consistency("строка", RAW) == []
    assert audit_cross_modal_consistency(None, RAW) == []
