# -*- coding: utf-8 -*-
"""
Тесты модуля core/utils.py (безопасный парсер JSON, санитизация, UTF-8).
"""

from scan_reader.core.utils import _safe_parse_json, sanitize_filename


def test_safe_parse_json_pure_json():
    raw = '{"doc_type": "Исполнительный лист", "case_number": "2-1234/2015"}'
    parsed = _safe_parse_json(raw)
    assert parsed["doc_type"] == "Исполнительный лист"
    assert parsed["case_number"] == "2-1234/2015"


def test_safe_parse_json_with_think_tags():
    raw = '<think>Модель размышляет о том, что это исполнительный лист...</think>{"doc_type": "Исполнительный лист", "total_rub": 15000.50}'
    parsed = _safe_parse_json(raw)
    assert parsed["doc_type"] == "Исполнительный лист"
    assert parsed["total_rub"] == 15000.50


def test_safe_parse_json_with_markdown_fence():
    raw = 'Вот извлеченные данные:\n```json\n{"doc_date": "10.03.2015", "debtor": "ООО Ромашка"}\n```\nНадеюсь помог.'
    parsed = _safe_parse_json(raw)
    assert parsed["doc_date"] == "10.03.2015"
    assert parsed["debtor"] == "ООО Ромашка"


def test_safe_parse_json_fallback_braces():
    raw = 'Преамбула перед ответом {"authority": "ОСП по г. Химки", "is_valid": true} суффикс после ответа'
    parsed = _safe_parse_json(raw)
    assert parsed["authority"] == "ОСП по г. Химки"
    assert parsed["is_valid"] is True


def test_safe_parse_json_invalid_input():
    assert _safe_parse_json(None) == {}
    assert _safe_parse_json("") == {}
    assert _safe_parse_json("Невалидный текст без json") == {}


def test_safe_parse_json_multiple_markdown_blocks():
    """P2.6: первый ```-блок может быть пояснением, JSON — во втором."""
    raw = (
        "Пояснение:\n```\nздесь модель размышляет текстом\n```\n"
        "Ответ:\n```json\n{\"doc_type\": \"Приказ\"}\n```"
    )
    parsed = _safe_parse_json(raw)
    assert parsed == {"doc_type": "Приказ"}


def test_safe_parse_json_array_of_objects_first_wins():
    """P2.6: массив объектов -> первый словарь."""
    raw = '[{"item": 1}, {"item": 2}]'
    parsed = _safe_parse_json(raw)
    assert parsed == {"item": 1}


def test_safe_parse_json_nan_and_infinity_become_none():
    """P2.6: NaN/Infinity не проходят в пайплайн как числа."""
    raw = '{"total_rub": NaN, "amount": Infinity, "valid": true}'
    parsed = _safe_parse_json(raw)
    assert parsed["total_rub"] is None
    assert parsed["amount"] is None
    assert parsed["valid"] is True


def test_safe_parse_json_multiple_bare_objects_first_wins():
    """P2.6: несколько объектов в тексте -> первый валидный (раньше {})."""
    raw = 'Заметка {"ref": 1}. Ответ: {"doc_type": "УПД"}'
    parsed = _safe_parse_json(raw)
    assert parsed == {"ref": 1}


def test_sanitize_filename():
    assert sanitize_filename("Приказ / О возбуждении: ИП <123>?*") == "Приказ _ О возбуждении_ ИП _123___"
    assert sanitize_filename("") == "document"
    assert len(sanitize_filename("Очень длинное название документа " * 5, max_len=30)) <= 30
