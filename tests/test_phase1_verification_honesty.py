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
# Фикстуры проверяют МЕХАНИКУ аудитора, поэтому задают собственную спецификацию:
# после Фазы 6 ядро не знает ни одного имени поля конкретного типа документа.
SALARY_SPEC = {
    "parties": [{"path": "debtor", "label": "должник", "ids": ["inn"]},
                {"path": "claimant", "label": "взыскатель", "ids": ["inn"]}],
    "identifier_checks": [],
    "money_rules": [{
        "code": "SALARY_DISCREPANCY",
        "total": ["finances.total_deduction_rub"],
        "components": [["долг", ["finances.debt_amount_rub"]],
                       ["удержания", ["finances.fee_penalty_rub"]]],
        "min_components": 2, "tolerance": 0.05, "severity": "error",
    }],
    "deduction_limit": {"path": "finances.deduction_percentage", "basis_fields": ["claim_subject"]},
    "gate_fields": [
        {"path": "debtor.inn", "min_length": 10},
        {"path": "claimant.inn", "min_length": 10},
        {"path": "court.case_number", "min_length": 5},
        {"path": "finances.total_deduction_rub", "min_length": 3},
        {"path": "finances.total_rub", "min_length": 3},
    ],
    "gate_names": ["debtor.name", "claimant.name"],
    "gate_authorities": ["court.name"],
}

def _spec():
    """Спецификация для тестов механики аудитора (не привязана к реестру)."""
    from scan_reader.verifier.spec import VerificationSpec

    return VerificationSpec(SALARY_SPEC)


def _exec_spec():
    """Спецификация типа «исполнительный лист» для проверок денежных сверок."""
    from scan_reader.verifier.spec import VerificationSpec

    return VerificationSpec({
        "money_rules": [{
            "code": "WRIT_MATH_DISCREPANCY",
            "doc_hint": "исполнительный лист",
            "total": ["finances.total_rub"],
            "components": [["основной долг", ["finances.main_debt_rub"]],
                           ["проценты и неустойка", ["finances.interest_penalty_rub"]],
                           ["судебные расходы", ["finances.court_fee_rub"]],
                           ["прочее", ["finances.other_rub"]]],
            "min_components": 2, "tolerance": 0.05, "severity": "error",
        }],
    })


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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data=data, doc_type="salary_deductions", raw_ocr_text=RAW_UNRELATED
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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data=data, doc_type="salary_deductions", raw_ocr_text=raw
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


# Эталонный текст содержит ВСЕ денежные значения документа: с Фазы 3 гейт
# проверяет и суммы, поэтому текст без них честно возвращает расхождение.
RAW_CONSISTENT = (
    "Судебный приказ. Должник Иванов Иван Иванович, ИНН 7707083893. "
    "Основной долг 50000 руб., госпошлина 3500 руб., всего удержанию 53500 рублей, "
    "в размере 50% дохода."
)


def test_c03_single_number_is_not_zero_trust_verified():
    """Одно число не должно давать статус полной верификации.

    До исправления: zero_trust_verified (проверка фактически не выполнялась).
    """
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={"finances": {"total_rub": 1000.0}},
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.status != VerificationStatus.ZERO_TRUST_VERIFIED
    # Сумма 1000 отсутствует в эталонном тексте, поэтому гейт отклоняет документ.
    # Раньше такая сумма вообще не проверялась.
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED
    assert any(i.code == "HALLUCINATION_RISK" and i.field_name == "finances.total_rub"
               for i in report.issues)
    assert report.details.get("math_verified_ok") is None


def test_c03_single_number_present_in_text_is_verified_math_wise():
    """Число, реально присутствующее в тексте, проходит гейт, но не даёт verified."""
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={"finances": {"total_rub": 1000.0}},
        doc_type="salary_deductions",
        raw_ocr_text="Судебный приказ о взыскании 1000 рублей в пользу взыскателя.",
    )
    assert not [i for i in report.issues if i.code == "HALLUCINATION_RISK"]
    assert report.status != VerificationStatus.ZERO_TRUST_VERIFIED
    assert report.details.get("math_verified_ok") is None


def test_c03_full_reconciliation_yields_zero_trust_verified():
    """Полная сверка + успешные контрольные суммы + выполненный гейт."""
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data=_fully_consistent_doc(),
        doc_type="salary_deductions",
        raw_ocr_text=RAW_CONSISTENT,
        gate_source="text_layer",
    )
    assert report.status == VerificationStatus.ZERO_TRUST_VERIFIED
    assert report.details["checksums_verified_ok"] >= 1
    assert report.details["math_verified_ok"] is True
    assert report.details["gate_executed"] is True


def test_c02_gate_not_executed_when_reference_unavailable():
    """Документ передан, эталон недоступен — статус обязан отличаться от verified."""
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data=_fully_consistent_doc(),
        doc_type="salary_deductions",
        raw_ocr_text=None,
        gate_expected=True,
    )
    assert report.status == VerificationStatus.GATE_NOT_EXECUTED
    assert report.details["gate_executed"] is False
    assert any(i.code == "GATE_NOT_EXECUTED" for i in report.issues)


def test_c02_short_reference_is_not_a_passed_gate():
    """P2.8: непустой, но короткий эталон — гейт НЕ выполнен: раньше
    gate_executed=True молча ставился поверх пустого списка находок."""
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data=_fully_consistent_doc(),
        doc_type="salary_deductions",
        raw_ocr_text="шапка",
        gate_source="text_layer",
        gate_expected=True,
    )
    assert report.details["gate_executed"] is False
    assert report.details["gate_source"] == "text_layer"
    assert any(i.code == "GATE_REFERENCE_TOO_SHORT" for i in report.issues)
    assert report.status == VerificationStatus.GATE_NOT_EXECUTED


def test_c02_standalone_verify_without_document_is_normal():
    """Аудит готового JSON без документа — штатный случай, не отказ."""
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data=_fully_consistent_doc(), doc_type="salary_deductions"
    )
    assert report.status == VerificationStatus.ZERO_TRUST_VERIFIED
    assert not [i for i in report.issues if i.code == "GATE_NOT_EXECUTED"]

def test_c03_executive_documents_no_longer_reports_bogus_verification():
    """Исполнительные листы: 4 слагаемых сверяются (Фаза 3.1), и подмена итога
    обязана обнаруживаться."""
    report = ZeroTrustAuditor.audit_document(
        spec=_exec_spec(),
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
    assert "WRIT_MATH_DISCREPANCY" in [i.code for i in report.issues if i.severity == "error"]


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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={
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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={
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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={"finances": {"total_deduction_rub": 0.0}},
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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={
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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={
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
    report = ZeroTrustAuditor.audit_document(spec=_spec(), data={
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


def test_p17_declared_error_severity_breaks_validity():
    """P1.7: limit["severity"]="error" из verification.json применяется честно.

    Раньше строка `severity = severity` была no-op: удержание 60% без
    основания проходило предупреждением, хотя плагин заявил ошибку.
    """
    from scan_reader.verifier.spec import VerificationSpec

    spec = VerificationSpec({
        "deduction_limit": {
            "path": "finances.deduction_percentage",
            "basis_fields": ["claim_subject"],
            "severity": "error",
        }
    })
    report = ZeroTrustAuditor.audit_document(
        spec=spec,
        data={
            "finances": {
                "debt_amount_rub": 100.0,
                "fee_penalty_rub": 0.0,
                "total_deduction_rub": 100.0,
                "deduction_percentage": "60%",
            },
            "claim_subject": "взыскание задолженности",
        },
        doc_type="salary_deductions",
        raw_ocr_text=RAW_UNRELATED,
    )
    issue = next(i for i in report.issues if i.code == "STATUTORY_LIMIT_ALERT")
    assert issue.severity == "error"
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED


def test_p18_money_rule_with_single_number_is_not_a_check():
    """P1.8: одно число — не сверка. Правило 2: сравнивать обязаны
    два и более числа, иначе статус остаётся vlm_unverified.

    legal_claims раньше заявлял min_components=1: слагаемое отсутствовало,
    «проверка» молча не выполнялась, а претензия с одной суммой выглядела
    свереной.
    """
    from scan_reader.verifier.spec import VerificationSpec

    spec = VerificationSpec({
        "money_rules": [{
            "code": "CLAIM_MATH_DISCREPANCY",
            "total": ["finances.total_claim_rub"],
            "components": [
                ["основной долг", ["finances.principal_debt_rub"]],
                ["неустойка", ["finances.penalty_rub"]],
            ],
            "min_components": 2, "tolerance": 0.05, "severity": "error",
        }]
    })
    report = ZeroTrustAuditor.audit_document(
        spec=spec,
        data={"finances": {"total_claim_rub": 1000.0}},
        doc_type="legal_claims",
        raw_ocr_text=RAW_UNRELATED,
    )
    assert report.details.get("math_checked") is not True
    assert report.status == VerificationStatus.VLM_UNVERIFIED


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
        target = saved.get("Registry_Full.json")
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


# =========================================================================
# P1.4: пути verification.json обязаны существовать в схеме плагина
# =========================================================================
def test_p14_dead_paths_are_reported_against_schema():
    """Мёртвый путь (несуществующее поле) даёт предупреждение, а не тишину."""
    from pydantic import BaseModel

    from scan_reader.verifier.spec import VerificationSpec

    class Party(BaseModel):
        inn: str = ""

    class Doc(BaseModel):
        claimant: Party

    spec = VerificationSpec({"parties": [{"path": "claimant", "ids": ["inn", "ogrn"]}]})
    warnings = spec.validate_paths(Doc, "test_plugin")
    # claimant.ogrn в Party нет — путь мёртвый
    assert any("claimant.ogrn" in w for w in warnings)
    # claimant.inn существует — предупреждения быть не должно
    assert not any("claimant.inn" in w for w in warnings)


def test_p14_alternative_path_valid_when_one_side_exists():
    """Путь с |-альтернативами валиден, если резолвится хотя бы одна."""
    from pydantic import BaseModel

    from scan_reader.verifier.spec import VerificationSpec

    class Court(BaseModel):
        act_date: str = ""

    class Doc(BaseModel):
        court: Court

    spec = VerificationSpec({"doc_ref_fields": ["act_date|court.act_date", "doc_date"]})
    warnings = spec.validate_paths(Doc, "test_plugin")
    # «act_date|court.act_date» валиден (вторая альтернатива живая),
    # «doc_date» мёртвый (нет ни в корне, ни в court).
    assert not any("court.act_date" in w for w in warnings)
    assert any("'doc_date'" in w for w in warnings)


def test_p14_all_plugins_without_dead_verification_paths():
    """Все 9 плагинов грузятся без мёртвых путей в verification.json."""
    from scan_reader.type_registry import get_registry

    reg = get_registry(force_reload=True)
    assert reg.load_errors == {}
    dead = {
        pid: p.verification_path_warnings
        for pid, p in reg.plugins.items()
        if p.verification_path_warnings
    }
    assert dead == {}
