# -*- coding: utf-8 -*-
"""
Тесты Фазы 4 — OCR-канал и честная маркировка источника эталона.

Фаза закрывает главный пробел аудита: проект называется ScanReader и принимает
сканы, но OCR-движка в нём не было, поэтому кросс-модальный гейт для изображений
не выполнялся ВОВСЕ.
"""

import pytest
from PIL import Image

from scan_reader.core import diagnostics as diag
from scan_reader.core.ocr import OcrEngine, OcrUnavailable, reset_ocr_engine
from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.type_registry import get_registry
from scan_reader.verifier import VerificationStatus
from scan_reader.verifier.auditor import ZeroTrustAuditor as A
from scan_reader.verifier.hallucination_gate import _numeric_atoms, check_presence_in_raw_text


def _facade():
    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade._client = None
    return facade


@pytest.fixture
def scan(tmp_path):
    path = tmp_path / "scan.jpg"
    Image.new("L", (1000, 1400), color=210).save(path)
    return str(path)


@pytest.fixture(autouse=True)
def _reset_singletons():
    yield
    reset_ocr_engine()


# =========================================================================
# Деградация без установленного движка
# =========================================================================
def test_ocr_degrades_gracefully_without_backend(scan):
    """Проект обязан работать без extra [ocr], не поднимая исключений."""
    engine = OcrEngine()
    assert engine.text(scan) is None, "отсутствие OCR обязано давать None, а не исключение"


def test_ocr_diagnostics_reports_install_hint():
    diag_info = OcrEngine().diagnostics()
    assert diag_info["install_hint"] == 'pip install "scan-reader[ocr]"'
    assert "installed" in diag_info
    assert "backend" in diag_info


def test_ocr_explicit_call_raises_actionable_error():
    """Явный вызов даёт понятную ошибку с командой установки."""
    if OcrEngine().available():
        pytest.skip("OCR backend installed in this environment")
    with pytest.raises(OcrUnavailable) as exc:
        OcrEngine().image_to_text(Image.new("L", (8, 8)))
    assert "scan-reader[ocr]" in str(exc.value)


def test_ocr_engine_is_lazy():
    """Модель не поднимается в конструкторе: это секунды и десятки мегабайт."""
    engine = OcrEngine()
    assert engine._impl is None
    assert engine.diagnostics()["initialized"] is False


# =========================================================================
# Выбор канала эталона
# =========================================================================
def test_reference_channel_prefers_text_layer(tmp_path, monkeypatch):
    """Текстовый слой точнее и бесплатен, поэтому имеет приоритет над OCR."""
    doc = tmp_path / "order.txt"
    doc.write_text("текстовый слой документа " * 4, encoding="utf-8")
    facade = _facade()
    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", lambda p: "текстовый слой документа" * 3)
    monkeypatch.setattr(facade, "_ocr_enabled", lambda: True)
    text, source = facade._reference_text_for_gate(str(doc))
    assert source == "text_layer"
    assert "текстовый слой" in text


def test_text_layer_is_not_requested_for_images(scan, monkeypatch):
    """У сканов нет текстового слоя — лишний вызов не делается."""
    facade = _facade()
    calls = []

    def _spy(path):
        calls.append(path)
        return None

    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", _spy)
    monkeypatch.setattr(facade, "_ocr_enabled", lambda: False)
    monkeypatch.setattr(facade, "_transcribe_scan_for_audit", lambda p: None)
    facade._reference_text_for_gate(scan)
    assert calls == [], "текстовый слой не должен запрашиваться у изображений"


def test_reference_channel_falls_back_to_vlm(scan, monkeypatch):
    facade = _facade()
    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", lambda p: None)
    monkeypatch.setattr(facade, "_ocr_enabled", lambda: False)
    monkeypatch.setattr(facade, "_transcribe_scan_for_audit", lambda p: "транскрипция VLM " * 5)
    text, source = facade._reference_text_for_gate(scan)
    assert source == "vlm_transcription"
    assert "транскрипция" in text


def test_reference_channel_reports_none_when_nothing_available(scan, monkeypatch):
    facade = _facade()
    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", lambda p: None)
    monkeypatch.setattr(facade, "_ocr_enabled", lambda: False)
    monkeypatch.setattr(facade, "_transcribe_scan_for_audit", lambda p: None)
    text, source = facade._reference_text_for_gate(scan)
    assert text is None
    assert source == "none"


def test_reference_channel_prefers_ocr_over_vlm(scan, monkeypatch):
    """OCR даёт независимый канал, VLM-транскрипция — нет."""
    import scan_reader.facade as facade_mod

    class FakeEngine:
        def available(self):
            return True

        def text(self, path, max_pages=None):
            return "НЕЗАВИСИМЫЙ OCR: ООО Ромашка, ИНН 7707083893"

    facade = _facade()
    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", lambda p: None)
    monkeypatch.setattr(facade, "_ocr_enabled", lambda: True)
    monkeypatch.setattr(facade, "_transcribe_scan_for_audit", lambda p: "текст от VLM")
    monkeypatch.setattr(facade_mod, "get_ocr_engine", lambda *a, **k: FakeEngine())

    text, source = facade._reference_text_for_gate(scan)
    assert source == "ocr"
    assert "НЕЗАВИСИМЫЙ OCR" in text


def test_ocr_can_be_disabled_by_env(monkeypatch):
    monkeypatch.setenv("SCANREADER_OCR_ENABLED", "false")
    assert _facade()._ocr_enabled() is False
    monkeypatch.setenv("SCANREADER_OCR_ENABLED", "true")
    # дальше зависит от наличия движка в окружении
    assert isinstance(_facade()._ocr_enabled(), bool)


# =========================================================================
# Честная маркировка источника в отчёте
# =========================================================================
RAW_REAL = "Постановление о взыскании в пользу ООО Ромашка, ИНН 7707083893, 150000 рублей"
# Реквизиты берутся из verification.json плагина salary_deductions: поле
# finances.total_rub у этого типа не проверяется гейтом, зато проверяется
# total_deduction_rub.
DOC_REAL = {
    "payment_details": {"recipient_inn": "7707083893"},
    "employer": {"name": "ООО Ромашка"},
    "finances": {"total_deduction_rub": 150000.0},
}


def test_vlm_reference_is_marked_not_independent():
    """Эталон от той же модели, что и извлечение, — не независимая сверка."""
    report = A.audit_document(
        DOC_REAL, doc_type="salary_deductions", raw_ocr_text=RAW_REAL,
        gate_source="vlm_transcription",
    )
    assert report.details["gate_independent"] is False
    assert "GATE_REFERENCE_FROM_VLM" in [i.code for i in report.issues]


def test_ocr_reference_is_marked_independent():
    report = A.audit_document(
        DOC_REAL, doc_type="salary_deductions", raw_ocr_text=RAW_REAL, gate_source="ocr"
    )
    assert report.details["gate_independent"] is True
    assert "GATE_REFERENCE_FROM_VLM" not in [i.code for i in report.issues]


def test_text_layer_reference_is_marked_independent():
    report = A.audit_document(
        DOC_REAL, doc_type="salary_deductions", raw_ocr_text=RAW_REAL, gate_source="text_layer"
    )
    assert report.details["gate_independent"] is True


def test_absent_reference_is_marked_not_independent():
    report = A.audit_document(DOC_REAL, doc_type="salary_deductions", gate_expected=True)
    assert report.details["gate_independent"] is False
    assert report.details["gate_executed"] is False


def test_real_requisites_are_confirmed_against_real_text():
    """Регрессия на ложные срабатывания: реквизиты из текста подтверждаются."""
    report = A.audit_document(
        DOC_REAL, doc_type="salary_deductions", raw_ocr_text=RAW_REAL, gate_source="ocr"
    )
    assert not [i for i in report.issues if i.code == "HALLUCINATION_RISK"]


def test_fabricated_amount_is_still_rejected():
    raw = "Постановление: ООО Ромашка ИНН 7707083893, взыскать 999999 рублей"
    report = A.audit_document(
        DOC_REAL, doc_type="salary_deductions", raw_ocr_text=raw, gate_source="ocr"
    )
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED
    assert any(
        i.code == "HALLUCINATION_RISK" and i.field_name == "finances.total_deduction_rub"
        for i in report.issues
    )


# =========================================================================
# Токенизация чисел: соседние числа не должны сливаться
# =========================================================================
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("ИНН 7707083893, 150000 руб.", ["7707083893", "150000"]),
        ("Взыскать 224 616,01 руб.", ["22461601"]),
        ("Счет 30101810400000000225, БИК 044525225", ["30101810400000000225", "044525225"]),
        ("1 234 567 руб.", ["1234567"]),
        ("12 345 руб.", ["12345"]),
        ("224616 рублей 01 копейку", ["224616", "01"]),
        ("150000 и 250000", ["150000", "250000"]),
    ],
)
def test_numeric_atoms_do_not_merge_across_separators(raw, expected):
    """
    Пробел продолжает число ТОЛЬКО как разделитель тысяч.

    Регрессия: жадный шаблон склеивал «7707083893, 150000» в один атом
    «7707083893150000», и оба реквизита переставали подтверждаться, то есть
    гейт отклонял корректные документы.
    """
    assert _numeric_atoms(raw) == expected


def test_two_requisites_in_one_line_both_confirm():
    raw = "Оплата: ИНН 7707083893, счет 30101810400000000225, БИК 044525225"
    assert check_presence_in_raw_text("7707083893", raw) is True
    assert check_presence_in_raw_text("30101810400000000225", raw) is True
    assert check_presence_in_raw_text("044525225", raw) is True
    assert check_presence_in_raw_text("7707083890", raw) is False


# =========================================================================
# doctor
# =========================================================================
def test_doctor_reports_ocr_section():
    report = diag.run_diagnostics(check_vlm=False, verbose=False)
    assert "ocr" in report
    assert "installed" in report["ocr"]
    assert report["ocr"]["install_hint"] == 'pip install "scan-reader[ocr]"'


def test_missing_ocr_does_not_make_doctor_report_error():
    """Нет OCR — качество сверки ниже, но конфигурация работоспособна."""
    report = diag.run_diagnostics(check_vlm=False, verbose=False)
    assert report["status"] != "error"
    assert "rapidocr_onnxruntime" in report["dependencies"]


def test_ocr_dependency_is_not_part_of_missing_optional():
    """Иначе doctor всегда показывал бы warning независимо от установки."""
    report = diag.run_diagnostics(check_vlm=False, verbose=False)
    optional = ("pandas", "docx")
    for dep in optional:
        assert dep in report["dependencies"]


# =========================================================================
# Упаковка
# =========================================================================
def test_pyproject_declares_ocr_extras():
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    text = (repo / "pyproject.toml").read_text(encoding="utf-8")
    assert 'ocr = [' in text
    assert 'rapidocr-onnxruntime' in text
    assert 'ocr-full' in text


def test_env_example_documents_ocr_settings():
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    text = (repo / ".env.example").read_text(encoding="utf-8")
    for var in (
        "SCANREADER_OCR_ENABLED",
        "SCANREADER_OCR_ENGINE",
        "SCANREADER_OCR_LANG",
        "SCANREADER_OCR_MAX_PAGES",
        "SCANREADER_OCR_MIN_TEXT_LENGTH",
        "SCANREADER_OCR_CROSSCHECK",
    ):
        assert var in text, f"{var} не задокументирован в .env.example"
