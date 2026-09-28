# -*- coding: utf-8 -*-
"""
Регрессионные тесты Фазы 5 — честность измерений (C-06).

Каждый тест фиксирует дефект, воспроизведённый ДО исправления:
десятикратная ошибка в сумме оценивалась в 98.32% со статусом "excellent",
почти пустое извлечение давало 100%, а удержание сверх закона проходило.
"""

import inspect

import pytest

from scan_reader.core import metrics_evaluator as me
from scan_reader.core.guardrails import RULE_KINDS, evaluate_rule, run_guardrails
from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.type_registry import get_registry


def _facade():
    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    return facade


# =========================================================================
# 5.1: словарь type из benchmark.json
# =========================================================================
@pytest.mark.parametrize(
    "declared,expected",
    [
        ("number", "numeric"), ("numeric", "numeric"), ("int", "numeric"),
        ("float", "numeric"), ("money", "numeric"), ("amount", "numeric"),
        ("string", "text"), ("text", "text"), ("fuzzy", "text"),
        ("inn", "inn"), ("snils", "inn"), ("ogrn", "inn"),
        ("date", "date"), ("datetime", "date"),
        ("exact", "exact"),
    ],
)
def test_c06_field_type_aliases_resolved(declared, expected):
    assert me.resolve_field_type(declared) == expected


def test_c06_tenfold_money_error_is_not_excellent():
    """Ошибка в 10 раз давала 98.32% и статус 'excellent' через difflib."""
    result = me.evaluate_generic_benchmark(
        {"finances": {"total_rub": 240000.0}},
        {"finances": {"total_rub": 2400000.0}},
        "commercial_contracts",
    )
    assert result["overall_score"] < 50.0
    assert result["validation_passed"] is False


def test_c06_money_error_detected_in_both_benchmark_paths():
    """Оба бенчмарк-пути обязаны давать одинаковый вердикт."""
    facade = _facade()
    doc = {
        "doc_date": "15.01.2023", "doc_number": "П-1", "ip_number": "98765/23/50026-ИП",
        "claimant_name": "ПАО Сбербанк", "debtor_name": "Смирнов Андрей",
        "finances": {"total_deduction_rub": 150000.0, "deduction_percentage": "50%"},
    }
    bad = dict(doc)
    bad["finances"] = {"total_deduction_rub": 1500000.0, "deduction_percentage": "50%"}
    assert facade.benchmark_against_ground_truth(bad, doc, "salary_deductions")["accuracy"] < 90.0
    assert me.evaluate_generic_benchmark(bad, doc, "salary_deductions")["overall_score"] < 90.0


def test_c06_numeric_field_compared_as_number():
    score, kind, state = me.score_field(240000.0, 240000.0, "number")
    assert (score, kind, state) == (100.0, "numeric", "scored")
    score, _k, _s = me.score_field("1 234,56", 1234.56, "number")
    assert score == 100.0


def test_c06_text_field_still_fuzzy():
    score, kind, _s = me.score_field("2-1234/2015", "2-1234/2015", "fuzzy")
    assert (score, kind) == (100.0, "text")
    score, _k, _s = me.score_field("2-1234/2015", "99-9999", "fuzzy")
    assert score < 50.0


def test_c06_inn_field_checks_control_digit():
    ok_score, _k, _s = me.score_field("7707083893", "7707083893", "inn")
    bad_score, _k, _s = me.score_field("7707083890", "7707083893", "inn")
    assert ok_score == 100.0
    assert bad_score == 0.0


def test_c06_date_field_normalises_format():
    same, _k, _s = me.score_field("15.01.2023", "2023-01-15", "date")
    diff, _k, _s = me.score_field("15.01.2023", "15.01.2024", "date")
    assert same == 100.0
    assert diff == 0.0


# =========================================================================
# 5.2: пустые значения больше не дают 100
# =========================================================================
def test_c06_absent_on_both_sides_excluded_from_denominator():
    """Раньше поле, пустое с обеих сторон, давало 100.0 и раздувало оценку."""
    score, _kind, state = me.score_field(None, None, "number")
    assert score is None
    assert state == "absent_both"


def test_c06_absent_field_does_not_inflate_score():
    """Модель не должна наказываться и получать 100 за то, что не выдумала поле."""
    gt = {"doc_number": "Д-1"}
    result = me.evaluate_generic_benchmark({"doc_number": "Д-1"}, gt, "commercial_contracts")
    assert result["overall_score"] == 100.0
    assert "finances.total_rub" in result["fields_absent_on_both_sides"]


def test_c06_missed_field_scores_zero():
    score, _kind, state = me.score_field(None, 100.0, "number")
    assert (score, state) == (0.0, "missed")


def test_c06_hallucinated_field_is_penalised():
    result = me.evaluate_generic_benchmark(
        {"doc_number": "Д-1", "finances": {"total_rub": 999999.0}},
        {"doc_number": "Д-1"},
        "commercial_contracts",
    )
    assert "finances.total_rub" in result["fields_hallucinated"]
    assert result["hallucination_penalty"] > 0
    assert result["validation_passed"] is False


def test_c06_empty_rule_set_is_not_perfect_score():
    """Пустой autonomous.json больше не даёт 100.0 и passed=True."""
    empty = run_guardrails([], {})
    assert empty["score"] == 0.0
    assert empty["passed"] is False
    assert empty["evaluator_degraded"] is True


def test_c06_no_applicable_gt_fields_scores_zero():
    result = me.evaluate_generic_benchmark({}, {}, "commercial_contracts")
    assert result["overall_score"] == 0.0


# =========================================================================
# 5.3: единый исполнитель правил
# =========================================================================
def test_c06_facade_and_metrics_agree_on_every_field():
    """Одно поле не должно получать противоположные вердикты в двух путях."""
    facade = _facade()
    for doc_type in ("hr_orders", "enforcement_orders", "salary_deductions"):
        for doc in (
            {"doc_date": "НЕ-ДАТА-ВОБЩЕ"},
            {"doc_date": "15.01.2023"},
            {"doc_date": ""},
            {},
        ):
            a = facade.validate_document(doc, doc_type)["score"]
            b = me.evaluate_generic_autonomous(doc, doc_type)["overall_score"]
            assert abs(a - b) < 0.01, f"{doc_type}: facade={a} metrics={b} для {doc}"


def test_c06_both_rule_vocabularies_are_supported():
    """valid_date_format и date_format — синонимы одного правила."""
    assert "valid_date_format" in RULE_KINDS
    assert "date_format" in RULE_KINDS
    assert "not_empty" in RULE_KINDS
    assert "required" in RULE_KINDS


def test_c06_garbage_date_rule_is_enforced_in_metrics_path():
    """valid_date_format использовался 5 плагинами, но для метрик был невидим."""
    facade = _facade()
    doc = {"doc_date": "НЕ-ДАТА-ВОБЩЕ"}
    assert facade.validate_document(doc, "hr_orders")["score"] < 100.0
    assert me.evaluate_generic_autonomous(doc, "hr_orders")["overall_score"] < 100.0


def test_c06_unknown_rule_is_reported_not_silently_passed():
    result = run_guardrails([{"field": "x", "rule": "no_such_rule"}], {"x": "value"})
    assert result["rules_unknown"], "неизвестное правило должно быть сообщено"
    assert result["rules_total"] == 0
    assert result["passed"] is False


@pytest.mark.parametrize("rule", sorted(RULE_KINDS))
def test_c06_every_declared_rule_is_implemented(rule):
    ok, note = evaluate_rule(rule, "1")
    assert not note, f"правило '{rule}' объявлено, но не реализовано: {note}"
    assert isinstance(ok, bool)


# =========================================================================
# 5.4: 229-ФЗ — алгоритмическая проверка, а не ключевые слова
# =========================================================================
@pytest.mark.parametrize(
    "rate,expected_ok",
    [
        ("50%", True),
        ("25%", True),
        ("1/4 части заработка", True),
        ("50 процентов", True),
        ("70%", False),
        ("80%", False),
        ("100%", False),
    ],
)
def test_c06_deduction_rate_is_alorithmic(rate, expected_ok):
    ok, score = me.validate_deduction_rate(rate)
    assert ok is expected_ok, f"{rate}: ok={ok} score={score}"


def test_c06_illegal_rate_is_not_awarded_90():
    """Раньше любое значение со словом «доля» получало 90.0, включая 80% и 100%."""
    ok, score = me.validate_deduction_rate("80%")
    assert ok is False
    assert score == 0.0


def test_c06_alimony_basis_allows_70_percent():
    ok, score = me.validate_deduction_rate("70%", "содержание несовершеннолетнего ребенка")
    assert ok is True
    assert score == 100.0


def test_c06_deduction_limit_rule_uses_legal_checker():
    ok, _note = evaluate_rule("deduction_limit", "80%", {"claim_subject": "взыскание"})
    assert ok is False
    ok, _note = evaluate_rule("deduction_limit", "50%", {})
    assert ok is True


# =========================================================================
# 5.6: все 9 типов оцениваются по данным плагина
# =========================================================================
def test_c06_hardcoded_evaluators_removed():
    for name in ("BENCHMARK_EVALUATORS", "AUTONOMOUS_EVALUATORS"):
        assert not hasattr(me, name), f"{name} остался: захардкоженные ID типов документов в core"
    for name in dir(me):
        if name.startswith("evaluate_") and "generic" not in name and name != "evaluate_dataset":
            pytest.fail(f"частный скорер остался в core: {name}")


def test_c06_all_plugin_types_consume_their_own_benchmark_config():
    registry = get_registry()
    for plugin_id in sorted(registry.enabled()):
        evaluator = me.get_benchmark_evaluator(plugin_id)
        result = evaluator({"doc_date": "15.01.2023"}, {"doc_date": "15.01.2023"})
        assert result["evaluator"] == "generic", plugin_id
        assert result["benchmark_config_consumed"] is True, plugin_id


def test_c06_facade_benchmark_uses_plugin_weights_not_hardcoded_fields():
    """Раньше executive_documents игнорировал свой benchmark.json."""
    facade = _facade()
    registry = get_registry()
    plugin = registry.get("salary_deductions")
    declared = {f.get("path") for f in plugin.benchmark_config.get("fields", [])}
    gt = {
        "doc_date": "15.01.2023", "doc_number": "П-1", "ip_number": "98765/23/50026-ИП",
        "claimant_name": "ПАО Сбербанк", "debtor_name": "Смирнов Андрей",
        "finances": {"total_deduction_rub": 150000.0, "deduction_percentage": "50%"},
    }
    result = facade.benchmark_against_ground_truth(gt, gt, "salary_deductions")
    assert set(result["details"].keys()) <= declared
    assert result["total_weight"] > 0


# =========================================================================
# 5.5: measurement_caveats
# =========================================================================
def test_c06_report_marks_ground_truth_name_mismatch():
    """Раньше документ без совпадения имени оценивался автономно, но отчёт
    продолжал называться 'benchmark'."""
    report = me.evaluate_dataset(
        [{"data": {"file_name": "other.pdf", "doc_date": "15.01.2023"}, "file_name": "other.pdf"}],
        [{"file_name": "a.pdf", "doc_date": "15.01.2023"}],
        doc_type="hr_orders",
    )
    doc = report["documents"][0]
    assert doc["mode_used"] == "autonomous_no_ground_truth_match"
    assert report["measurement_caveats"]["benchmark_mode_available"] is True
    assert report["measurement_caveats"]["low_confidence"] is True
    assert report["documents_measured_in_benchmark_mode"] == 0


def test_c06_caveats_flag_low_field_support():
    report = me.evaluate_dataset(
        [{"data": {"file_name": "a.pdf", "doc_date": "15.01.2023"}, "file_name": "a.pdf"}],
        [{"file_name": "a.pdf", "doc_date": "15.01.2023"}],
        doc_type="hr_orders",
    )
    caveats = report["documents"][0]["measurement_caveats"]
    assert caveats["low_field_support"] is True
    assert caveats["ground_truth_match"] is True
    assert caveats["fields_in_ground_truth"] == 1
    assert report["ground_truth_coverage_percent"] == 100.0


def test_c06_report_records_benchmark_and_autonomous_split():
    report = me.evaluate_dataset(
        [
            {"data": {"file_name": "a.pdf", "doc_date": "15.01.2023"}, "file_name": "a.pdf"},
            {"data": {"file_name": "b.pdf", "doc_date": "15.01.2023"}, "file_name": "b.pdf"},
        ],
        [{"file_name": "a.pdf", "doc_date": "15.01.2023"}],
        doc_type="hr_orders",
    )
    assert report["documents_measured_in_benchmark_mode"] == 1
    assert report["ground_truth_coverage_percent"] == 100.0
    assert report["measurement_caveats"]["documents_scored_against_ground_truth"] == 1
    assert report["measurement_caveats"]["documents_scored_autonomously"] == 1


def test_c06_evaluate_dataset_signature_unchanged():
    """Сигнатура используется CLI и MCP — ломать её нельзя."""
    params = list(inspect.signature(me.evaluate_dataset).parameters)
    assert params == ["predicted_docs", "ground_truth_docs", "doc_type", "registry"]
