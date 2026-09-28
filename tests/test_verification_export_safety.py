# -*- coding: utf-8 -*-
"""
Регрессионные тесты горящих дефектов экспорта (найдены при оценке рисков).

Три из них относятся к безопасности данных, а не к удобству:

1. normalize_doc_data переносила только file_name/path/doc_type/status/
   processed_at/quality_*, поэтому zero_trust_status и zero_trust в реестры
   НЕ попадали. Проверено на живой функции: 1С-запись содержала
   ZeroTrustStatus = None, то есть бухгалтер не видел следа проверки.

2. Документ со статусом gate_not_executed (кросс-модальная сверка не
   выполнялась) выгружался со status: COMPLETED и попадал в 1С-реестр.
   Код возврата 3 защищал только одиночный вызов CLI.

3. В двух местах экспорта ZeroTrustValid подставлялся дефолтом True, то есть
   непроверенный документ получал ложное подтверждение.

4. Регрессия производительности, внесённая слиянием реестров: каждый
   документ переписывал весь реестр, то есть O(N^2) за прогон.
"""

import json
import os

import pytest

from scan_reader.core.json_exporter import (
    convert_salary_to_target_1c,
    convert_to_flat_1c,
    export_consolidated_registries,
)
from scan_reader.core.verification_export import (
    REVIEW_REQUIRED_STATUSES,
    apply_verification_to_flat,
    normalize_doc_data as normalize_with_verification,
    requires_human_review,
    verification_status_of,
)
from scan_reader.verifier import VerificationStatus


def _result(status, *, zt_is_valid=True, file_name="doc.pdf", **extra):
    base = {
        "file_name": file_name,
        "file_path": f"/inbox/{file_name}",
        "doc_type": "salary_deductions",
        "status": "COMPLETED",
        "data": {"debtor_name": "Иванов", "finances": {"total_deduction_rub": 1000.0}},
        "zero_trust_status": status,
        "zero_trust": {"status": status, "is_valid": zt_is_valid, "issues": [], "details": {}},
    }
    base.update(extra)
    return base


# =========================================================================
# 1. Статус верификации доходит до реестров
# =========================================================================
def test_normalize_carries_verification_status():
    rec = _result(VerificationStatus.ZERO_TRUST_VERIFIED.value)
    out = normalize_with_verification(rec)
    assert out["zero_trust_status"] == "zero_trust_verified"
    assert out["requires_human_review"] is False
    assert out["status"] == "COMPLETED"


@pytest.mark.parametrize("status", sorted(REVIEW_REQUIRED_STATUSES))
def test_review_required_statuses_are_flagged(status):
    assert requires_human_review(_result(status)) is True
    out = normalize_with_verification(_result(status))
    assert out["requires_human_review"] is True
    assert out["zero_trust_status"] == status


def test_verification_survives_registry_roundtrip(tmp_path):
    """Статус должен быть в файле реестра, а не только в отчёте одиночного документа."""
    export_consolidated_registries([_result("discrepancy_detected")], str(tmp_path), merge=True)

    with open(tmp_path / "salary_deductions_registry.json", encoding="utf-8") as fh:
        records = json.load(fh)
    assert len(records) == 1
    assert records[0]["zero_trust_status"] == "discrepancy_detected"
    assert records[0]["requires_human_review"] is True


def test_verification_survives_1c_registry(tmp_path):
    export_consolidated_registries([_result("gate_not_executed")], str(tmp_path), merge=True)

    with open(tmp_path / "salary_deductions_registry_1c.json", encoding="utf-8") as fh:
        records = json.load(fh)
    assert records[0]["ZeroTrustStatus"] == "gate_not_executed"
    assert records[0]["RequiresHumanReview"] is True


def test_1c_record_never_claims_false_validity():
    """
    Раньше ZeroTrustValid подставлялся дефолтом True для документа без отчёта.
    Ложное подтверждение в учётной системе опаснее отсутствия подтверждения.
    """
    without_report = {"file_name": "x.pdf", "doc_type": "salary_deductions", "data": {}}
    rec = convert_salary_to_target_1c(without_report)
    assert rec["ZeroTrustValid"] is None
    assert rec["ZeroTrustStatus"] == ""

    flat = convert_to_flat_1c(without_report)
    assert flat["ZeroTrustValid"] is None
    assert flat["ZeroTrustStatus"] == ""


def test_flat_1c_record_has_verification_fields():
    rec = _result(VerificationStatus.DISCREPANCY_DETECTED.value, zt_is_valid=False)
    flat = convert_to_flat_1c(rec)
    assert flat["ZeroTrustStatus"] == "discrepancy_detected"
    assert flat["ZeroTrustValid"] is False
    assert flat["RequiresHumanReview"] is True


def test_apply_verification_is_idempotent():
    """Повторный прогон экспортера по готовой плоской записи не стирает статус."""
    rec = convert_salary_to_target_1c(_result("partially_verified"))
    again = apply_verification_to_flat(rec, rec)
    assert again["ZeroTrustStatus"] == "partially_verified"
    # partially_verified — совещательный, а не блокирующий статус
    assert again["RequiresHumanReview"] is False
    assert again["AdvisoryReview"] is True


def test_advisory_statuses_do_not_block_but_are_flagged():
    """partially_verified и vlm_unverified не блокируют импорт, но видны оператору."""
    from scan_reader.core.verification_export import requires_advisory_review

    for status in ("partially_verified", "vlm_unverified", "ocr_low_confidence"):
        rec = _result(status)
        assert requires_human_review(rec) is False, f"{status} не должен блокировать"
        assert requires_advisory_review(rec) is True, f"{status} должен быть виден оператору"
        out = normalize_with_verification(rec)
        assert out["requires_human_review"] is False
        assert out["requires_advisory_review"] is True


def test_blocking_and_advisory_sets_are_disjoint():
    from scan_reader.core.verification_export import ADVISORY_STATUSES

    assert not (REVIEW_REQUIRED_STATUSES & ADVISORY_STATUSES)


def test_status_derived_from_report_when_field_absent():
    """Запись может прийти из Registry_Full.json, где статуса нет, но отчёт есть."""
    rec = {
        "file_name": "x.pdf", "doc_type": "hr_orders", "data": {},
        "zero_trust": {"status": "discrepancy_detected", "is_valid": False, "details": {}},
    }
    assert verification_status_of(rec) == "discrepancy_detected"
    out = normalize_with_verification(rec)
    assert out["zero_trust_status"] == "discrepancy_detected"
    assert out["requires_human_review"] is True


def test_empty_status_is_not_treated_as_failure():
    """Бытовая запись без верификации — не повод требовать проверки."""
    assert requires_human_review({"file_name": "x.pdf", "data": {}}) is False
    out = normalize_with_verification({"file_name": "x.pdf", "data": {}})
    assert out["requires_human_review"] is False
    assert "zero_trust_status" not in out


# =========================================================================
# 2. Непроверенный документ не помечается COMPLETED
# =========================================================================
def test_unverified_document_not_marked_completed(tmp_path):
    """
    Ключевая проверка: gate_not_executed означает, что реквизиты НИ РАЗУ не
    сопоставлялись с исходным текстом. Такая запись не должна выглядеть
    обработанной и подтверждённой.
    """
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()

    for zt_status in ("gate_not_executed", "discrepancy_detected", "heuristic_fallback",
                      "partially_verified", "vlm_unverified"):
        result = {
            "status": "COMPLETED",
            "zero_trust_status": zt_status,
            "zero_trust": {"status": zt_status, "is_valid": zt_status != "discrepancy_detected",
                           "issues": [], "details": {}},
        }
        review = requires_human_review(result)
        expected_review = zt_status in REVIEW_REQUIRED_STATUSES
        assert review is expected_review, f"{zt_status}: requires_human_review={review}"

    # Надёжные статусы статус COMPLETED сохраняют
    for zt_status in ("zero_trust_verified", "ocr_low_confidence"):
        assert requires_human_review(_result(zt_status)) is False


# =========================================================================
# 3. Производительность: реестры не переписываются на каждом документе
# =========================================================================
def test_batch_disables_per_document_registry_update():
    """Пакетный режим обязан писать реестры один раз, а не на каждый документ."""
    import inspect

    from scan_reader.facade import LegalDocPlatformFacade

    source = inspect.getsource(LegalDocPlatformFacade._batch_stage4_extract)
    assert "update_registries=False" in source, (
        "пакетная обработка не отключает перезапись реестров на каждом документе"
    )
    assert "update_registries=True" not in source


def test_single_document_still_updates_registries_by_default():
    import inspect

    from scan_reader.facade import LegalDocPlatformFacade

    params = inspect.signature(LegalDocPlatformFacade.process_single_document).parameters
    assert params["update_registries"].default is True, (
        "одиночный запуск из CLI обязан обновлять реестр по умолчанию"
    )


def test_registry_merge_still_prevents_truncation(tmp_path):
    """Отключение перезаписи в пакете не должно вернуть усечение реестра."""

    out = str(tmp_path)
    for i in (1, 2, 3):
        export_consolidated_registries(
            [_result("zero_trust_verified", file_name=f"d{i}.pdf")], out, merge=True
        )
    with open(os.path.join(out, "salary_deductions_registry.json"), encoding="utf-8") as fh:
        assert len(json.load(fh)) == 3
