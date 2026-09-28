# -*- coding: utf-8 -*-
"""
Регрессионные тесты Фазы 1 — честность статуса верификации.

Каждый тест фиксирует дефект, воспроизведённый ДО исправления.
Идентификаторы дефектов соответствуют плану корректировки.
"""

import json
import os

import pytest

from scan_reader.core.json_exporter import export_consolidated_registries
from scan_reader.verifier import VerificationStatus
from scan_reader.verifier.auditor import ZeroTrustAuditor, _first_present

# Эталонный текст, НЕ содержащий проверяемых реквизитов
RAW_UNRELATED = (
    "Постановление о взыскании задолженности в пользу взыскателя "
    "от 01.01.2024 номер 99-АБ"
)


# =========================================================================
# C-01: неподтверждённые реквизиты — ошибка, а не предупреждение
# =========================================================================
def test_c01_fabricated_requisites_are_discrepancy_not_verified():
    """Документ с тремя выдуманными сущностями обязан быть отклонён.

    До исправления: zero_trust_verified + is_valid=True + 3x warning.
    """
    data = {
        "debtor": {"inn": "7707083893", "name": "Иванов Иван"},
        "court": {"case_number": "А40-99999/2099"},
    }
    report = ZeroTrustAuditor.audit_document(
        data=data, doc_type="salary_deductions", raw_ocr_text=RAW_UNRELATED
    )

    assert report.is_valid is False
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED
    hallucinations = [i for i in report.issues if i.code == "HALLUCINATION_RISK"]
    assert len(hallucinations) == 3
    assert all(i.severity == "error" for i in hallucinations)


def test_c01_corroborated_requisites_pass_the_gate():
    """Если реквизиты действительно есть в тексте — ошибок быть не должно."""
    raw = (
        "Судебный приказ. Взыскать с Иванова Ивана Ивановича, ИНН 7707083893, "
        "дело А40-12345/2019, сумму 100 рублей."
    )
    data = {
        "debtor": {"inn": "7707083893", "name": "Иванов Иван"},
        "court": {"case_number": "А40-12345/2019"},
    }
    report = ZeroTrustAuditor.audit_document(
        data=data, doc_type="salary_deductions", raw_ocr_text=raw
    )
    assert report.is_valid is True
    assert not [i for i in report.issues if i.code == "HALLUCINATION_RISK"]


# =========================================================================
# C-02/C-03: zero_trust_verified требует реально выполненных проверок
# =========================================================================
def _fully_consistent_doc():
    return {
        "debtor": {"name": "Иванов Иван Иванович", "inn": "7707083893"},
        "finances": {
            "debt_amount_rub": 50000.0,
            "fee_penalty_rub": 3500.0,
            "total_deduction_rub": 53500.0,
            "deduction_percentage": "50%",
        },
    }


def test_c03_single_number_is_not_zero_trust_verified():
    """Одно число не должно давать статус полной верификации.

    До исправления: zero_trust_verified (проверка фактически не выполнялась).
    """
    report = ZeroTrustAuditor.audit_document(
        data={"finances": {"total_rub": 1000.0}},
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.status != VerificationStatus.ZERO_TRUST_VERIFIED
    assert report.status == VerificationStatus.VLM_UNVERIFIED
    assert report.details.get("math_verified_ok") is None


def test_c03_full_reconciliation_yields_zero_trust_verified():
    """Полная сверка + успешные контрольные суммы + выполненный гейт."""
    raw = (
        "Судебный приказ. Должник Иванов Иван Иванович, ИНН 7707083893, "
        "взыскано 53500 рублей."
    )
    report = ZeroTrustAuditor.audit_document(
        data=_fully_consistent_doc(),
        doc_type="salary_deductions",
        raw_ocr_text=raw,
        gate_source="text_layer",
    )
    assert report.status == VerificationStatus.ZERO_TRUST_VERIFIED
    assert report.details["checksums_verified_ok"] >= 1
    assert report.details["math_verified_ok"] is True
    assert report.details["gate_executed"] is True


def test_c02_gate_not_executed_when_reference_unavailable():
    """Документ передан, эталон недоступен — статус обязан отличаться от verified."""
    report = ZeroTrustAuditor.audit_document(
        data=_fully_consistent_doc(),
        doc_type="salary_deductions",
        raw_ocr_text=None,
        gate_expected=True,
    )
    assert report.status == VerificationStatus.GATE_NOT_EXECUTED
    assert report.details["gate_executed"] is False
    assert any(i.code == "GATE_NOT_EXECUTED" for i in report.issues)


def test_c02_standalone_verify_without_document_is_normal():
    """Аудит готового JSON без документа — штатный случай, не отказ."""
    report = ZeroTrustAuditor.audit_document(
        data=_fully_consistent_doc(), doc_type="salary_deductions"
    )
    assert report.status == VerificationStatus.ZERO_TRUST_VERIFIED
    assert not [i for i in report.issues if i.code == "GATE_NOT_EXECUTED"]


def test_c03_executive_documents_no_longer_reports_bogus_verification():
    """Исполнительные листы: 4 слагаемых не сверяются (C-04), но verified быть не может."""
    report = ZeroTrustAuditor.audit_document(
        data={
            "finances": {
                "main_debt_rub": 157611.62,
                "court_fee_rub": 60000.0,
                "total_rub": 999999.0,
            }
        },
        doc_type="executive_documents",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.status != VerificationStatus.ZERO_TRUST_VERIFIED


# =========================================================================
# C-05: нули — значимые значения
# =========================================================================
def test_c05_first_present_keeps_legitimate_zero():
    assert _first_present({"a": 0.0, "b": 5}, "a", "b") == 0.0
    assert _first_present({"a": 0, "b": 5}, "a", "b") == 0
    assert _first_present({"a": "0", "b": "5"}, "a", "b") == "0"
    assert _first_present({"a": None, "b": 5}, "a", "b") == 5
    assert _first_present({"a": "  ", "b": 5}, "a", "b") == 5
    assert _first_present({"a": "None", "b": 5}, "a", "b") == 5
    assert _first_present({"b": 5}, "a") is None


def test_c05_all_zero_amounts_still_reconciled():
    """Раньше сверка не выполнялась и отчёт молчал об этом."""
    report = ZeroTrustAuditor.audit_document(
        data={
            "finances": {
                "debt_amount_rub": 0.0,
                "fee_penalty_rub": 0.0,
                "total_deduction_rub": 0.0,
            }
        },
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.details["math_checked"] is True
    assert report.details["math_verified_ok"] is True


def test_c05_zero_state_fee_is_reconciled():
    report = ZeroTrustAuditor.audit_document(
        data={
            "finances": {
                "debt_amount_rub": 50000.0,
                "fee_penalty_rub": 0.0,
                "total_deduction_rub": 50000.0,
            }
        },
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.details["math_verified_ok"] is True


def test_c05_lone_total_is_reported_as_incomplete():
    """Нет компонентов — сверка невозможна, и это должно быть видно."""
    report = ZeroTrustAuditor.audit_document(
        data={"finances": {"total_deduction_rub": 0.0}},
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.details.get("math_verified_ok") is None
    assert report.status == VerificationStatus.VLM_UNVERIFIED


# =========================================================================
# Основание удержания по ст. 99 229-ФЗ не должно зависеть от одной подстроки
# =========================================================================
@pytest.mark.parametrize(
    "subject",
    [
        "алименты на несовершеннолетних детей",
        "содержание несовершеннолетнего ребенка в размере 70% дохода",
        "возмещение вреда здоровью",
    ],
)
def test_alimony_basis_recognised_by_wording(subject):
    report = ZeroTrustAuditor.audit_document(
        data={
            "finances": {
                "debt_amount_rub": 100.0,
                "fee_penalty_rub": 0.0,
                "total_deduction_rub": 100.0,
                "deduction_percentage": "70%",
            },
            "claim_subject": subject,
        },
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert not [i for i in report.issues if i.code == "STATUTORY_LIMIT_ALERT"]


def test_deduction_above_70_percent_is_error():
    report = ZeroTrustAuditor.audit_document(
        data={
            "finances": {
                "debt_amount_rub": 100.0,
                "fee_penalty_rub": 0.0,
                "total_deduction_rub": 100.0,
                "deduction_percentage": "80%",
            },
            "claim_subject": "взыскание задолженности",
        },
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED
    assert any(i.code == "STATUTORY_LIMIT_ALERT" and i.severity == "error" for i in report.issues)


def test_deduction_70_without_basis_is_flagged_for_review():
    report = ZeroTrustAuditor.audit_document(
        data={
            "finances": {
                "debt_amount_rub": 100.0,
                "fee_penalty_rub": 0.0,
                "total_deduction_rub": 100.0,
                "deduction_percentage": "70%",
            },
            "claim_subject": "взыскание задолженности",
        },
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert any(i.code == "STATUTORY_LIMIT_ALERT" for i in report.issues)


# =========================================================================
# C-08: отказ экстракции обязан быть отражён во внешнем статусе
# =========================================================================
def test_c08_failed_registry_filter_excludes_failed_records():
    """Записи со статусом FAILED не должны попадать в реестры 1С/Excel.

    До исправления фильтр имел вид `status == "FAILED" and "data" not in item`,
    что делало его холостым для любой записи с ключом "data".
    """
    results_dir = os.path.join(os.environ.get("SCANREADER_OUTPUT_DIR", "."), "regtest")
    failed = {
        "file_name": "broken.pdf",
        "doc_type": "salary_deductions",
        "status": "FAILED",
        "errors": ["LLM client unavailable"],
        "data": {"file_name": "broken.pdf", "status": "FAILED", "_extraction_failed": True},
    }
    marker = {
        "file_name": "inner_failed.pdf",
        "doc_type": "salary_deductions",
        "status": "COMPLETED",
        "data": {"file_name": "inner_failed.pdf", "_extraction_failed": True},
    }
    good = {
        "file_name": "good.pdf",
        "doc_type": "salary_deductions",
        "status": "COMPLETED",
        "data": {"file_name": "good.pdf", "finances": {"total_rub": 100.0}},
    }

    saved = export_consolidated_registries([failed, marker, good], results_dir)
    try:
        target = saved.get("salary_deductions_registry.json")
        if target is None:
            for name, path in saved.items():
                if name.endswith("salary_deductions_registry.json"):
                    target = path
                    break
        assert target is not None, f"registry not produced: {list(saved)}"
        with open(target, "r", encoding="utf-8") as fh:
            records = json.load(fh)
        names = [str(r.get("file_name") or r.get("name") or "") for r in records]
        assert "broken.pdf" not in names
        assert "inner_failed.pdf" not in names
        assert any("good" in n for n in names)
    finally:
        for path in saved.values():
            if os.path.isfile(path):
                os.remove(path)


# =========================================================================
# C-08: сквозной путь через фасад — отказ VLM не должен маскироваться
# =========================================================================
def test_c08_facade_reports_failed_status_when_vlm_unavailable(tmp_path, monkeypatch):
    """process_single_document обязан вернуть status=FAILED, а не COMPLETED."""
    from PIL import Image

    from scan_reader.facade import LegalDocPlatformFacade

    scan = tmp_path / "broken_scan.jpg"
    Image.new("L", (600, 800), color=200).save(scan)

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(facade, "_get_client", lambda: None)
    monkeypatch.setattr(
        facade, "classify_document", lambda *a, **k: ("salary_deductions", 1.0, "user_specified")
    )
    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", lambda *a, **k: None)
    facade.results_dir = str(tmp_path / "results")
    os.makedirs(facade.results_dir, exist_ok=True)

    result = facade.process_single_document(str(scan), doc_type="salary_deductions")

    assert result["status"] == "FAILED"
    assert result["errors"], "Rule 1: отказ обязан быть отражён в errors"
    assert result["quality_score_percent"] == 0.0
    assert result["data"]["_extraction_failed"] is True
    assert any(i["code"] == "EXTRACTION_FAILED" for i in result["zero_trust"]["issues"])


def test_c08_facade_survives_vlm_exception(tmp_path, monkeypatch):
    """Сетевой сбой/таймаут VLM не должен подниматься наружу из process_single_document."""
    from PIL import Image

    from scan_reader.facade import LegalDocPlatformFacade

    scan = tmp_path / "scan2.jpg"
    Image.new("L", (600, 800), color=180).save(scan)

    class _Boom:
        class chat:  # noqa: N801
            class completions:  # noqa: N801
                @staticmethod
                def create(**kwargs):
                    raise TimeoutError("VLM request timed out after 600s")

    facade = LegalDocPlatformFacade()
    monkeypatch.setattr(facade, "_get_client", lambda: _Boom())
    monkeypatch.setattr(
        facade, "classify_document", lambda *a, **k: ("salary_deductions", 1.0, "user_specified")
    )
    monkeypatch.setattr(facade, "_extract_raw_text_for_audit", lambda *a, **k: None)
    facade.results_dir = str(tmp_path / "results2")
    os.makedirs(facade.results_dir, exist_ok=True)

    result = facade.process_single_document(str(scan), doc_type="salary_deductions")

    assert result["status"] == "FAILED"
    assert result["data"]["_extraction_failed"] is True
    assert "timed out" in result["errors"][0]
