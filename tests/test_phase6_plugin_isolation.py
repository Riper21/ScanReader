# -*- coding: utf-8 -*-
"""
Тесты Фазы 6 — изоляция плагинов (Правило 3 AGENTS.md).

До этой фазы auditor.py перечислял 20 путей к ИНН, 3 к СНИЛС, 5 к ОГРН и 33
пути кросс-модального гейта руками, а metrics_evaluator — 9 идентификаторов
плагинов. Добавление десятого типа документа требовало правки ядра.
"""

import json
import re
import shutil
from pathlib import Path

import pytest

from scan_reader.type_registry import PluginSpec, get_registry
from scan_reader.verifier import VerificationStatus
from scan_reader.verifier.auditor import ZeroTrustAuditor as A
from scan_reader.verifier.spec import VerificationSpec, get_nested_value, resolve_spec

REPO = Path(__file__).resolve().parent.parent
DOC_TYPES = REPO / "src" / "scan_reader" / "doc_types"

#: Имена, которые ядро обязано не знать: конкретные поля и типы документов.
FORBIDDEN_IN_CORE = [
    "debtor", "claimant", "party_one", "party_two", "seller", "buyer",
    "customer", "contractor", "principal", "agent", "sender", "recipient",
    "employee", "employer", "payment_details", "bank_requisites", "fssp",
    "authority", "court",
    "salary_deductions", "executive_documents", "enforcement_orders",
    "invoices_upd", "commercial_contracts", "powers_of_attorney",
    "legal_claims", "hr_orders", "acceptance_certificates",
    "main_debt_rub", "total_deduction_rub", "deduction_percentage",
    "debt_amount_rub", "fee_penalty_rub", "principal_debt_rub",
    "blank_number", "base_doc_number", "ip_number", "reg_number",
    "total_claim_rub", "total_rub_no_vat",
]

#: Файлы ядра, которые по определению не знают о типах документов.
CORE_MODULES = [
    "src/scan_reader/verifier/auditor.py",
    "src/scan_reader/verifier/hallucination_gate.py",
    "src/scan_reader/verifier/status.py",
    "src/scan_reader/verifier/checksums.py",
    "src/scan_reader/verifier/chronology.py",
    "src/scan_reader/verifier/math_verifier.py",
]

#: Лексика банковского блока принадлежит интерпретатору (spec.py), а не документу.
INTERPRETER_VOCABULARY = {"uin", "rosp_code", "oktmo", "bik", "kpp", "account"}


def _quoted_tokens(text):
    return set(re.findall(r"""["']([A-Za-z_][A-Za-z0-9_]*)["']""", text))


# =========================================================================
# 6.1: verification.json обязателен и содержателен
# =========================================================================
def test_every_plugin_has_verification_file():
    registry = get_registry(force_reload=True)
    assert not registry.load_errors, registry.load_errors
    for plugin_id, plugin in registry.enabled().items():
        path = Path(plugin.folder_path) / "verification.json"
        assert path.exists(), f"{plugin_id}: нет verification.json"
        assert not plugin.verification_spec.is_empty(), f"{plugin_id}: спецификация пуста"


def test_verification_file_is_valid_json_with_required_sections():
    for path in sorted(DOC_TYPES.glob("*/verification.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), path
        spec = VerificationSpec(data)
        assert not spec.validate(path.parent.name), spec.validate(path.parent.name)
        assert data.get("_comment"), f"{path}: нужен _comment с назначением типа"


def test_verification_file_is_shipped_in_package_data():
    """Файл обязан попадать в wheel/sdist, иначе плагин молча деградирует."""
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert "doc_types/*/*.json" in text
    manifest = (REPO / "MANIFEST.in").read_text(encoding="utf-8")
    assert "doc_types" in manifest
    assert "verification.json" in manifest or "*.json" in manifest


def _set_in(data, dotted_path, value):
    parts = dotted_path.split(".")
    cursor = data
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


@pytest.mark.parametrize("plugin_id", sorted(get_registry().enabled()))
def test_every_plugin_verifies_its_own_requisites(plugin_id):
    """Контрольная сумма ИНН применяется там, где плагин объявил сторон."""
    plugin = get_registry().get(plugin_id)
    spec = plugin.verification_spec
    if not spec.parties and not spec.identifier_checks:
        pytest.skip(f"{plugin_id} не объявляет идентификаторов")
    data = {}
    for item in spec.identifier_checks:
        _set_in(data, item["path"], "7707083890")  # заведомо неверный разряд
    for party in spec.parties:
        for field in party["ids"]:
            _set_in(data, f"{party['path']}.{field}", "7707083890")

    report = A.audit_document(data, doc_type=plugin_id)
    invalid = [i for i in report.issues if i.code.startswith("INVALID_")]
    assert invalid, f"{plugin_id}: невалидный ИНН не обнаружен; data={data}"
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED


# =========================================================================
# 6.1: ядро не содержит знаний о типах документов
# =========================================================================
@pytest.mark.parametrize("rel_path", CORE_MODULES)
def test_core_modules_contain_no_document_field_names(rel_path):
    text = (REPO / rel_path).read_text(encoding="utf-8")
    # Только исполняемый код: комментарии и докстринги не считаются знанием ядра
    code = "\n".join(
        line.split("#", 1)[0] for line in text.splitlines()
    )
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    tokens = _quoted_tokens(code)
    leaked = (tokens & set(FORBIDDEN_IN_CORE)) - INTERPRETER_VOCABULARY
    assert not leaked, f"{rel_path}: ядро знает о полях документов {sorted(leaked)}"


def test_spec_module_only_uses_interpreter_vocabulary():
    """
    spec.py ЗАКОННО упоминает «debtor» в докстринге-примере и роли банковского
    блока; исполняемый код не должен содержать имён полей документов.
    """
    text = (REPO / "src/scan_reader/verifier/spec.py").read_text(encoding="utf-8")
    code = re.sub(r'""".*?"""', "", text, flags=re.S)
    code = "\n".join(line.split("#", 1)[0] for line in code.splitlines())
    leaked = _quoted_tokens(code) & set(FORBIDDEN_IN_CORE) - INTERPRETER_VOCABULARY
    assert not leaked, f"spec.py: {sorted(leaked)}"


def test_unknown_document_type_yields_empty_spec_not_guesswork():
    assert resolve_spec("brand_new_document_type").is_empty() is True


def test_adding_tenth_type_requires_no_core_change():
    """
    Регрессия Правила 3: плагин копируется целиком, и все функции ядра работают
    с его собственной спецификацией без правок ядра.
    """
    tmp = REPO / "src" / "scan_reader" / "doc_types" / "_tmp_tenth_type"
    source = DOC_TYPES / "commercial_contracts"
    try:
        shutil.copytree(source, tmp)
        manifest_path = tmp / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["id"] = "_tmp_tenth_type"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        spec = PluginSpec(str(tmp))

        assert spec.id == "_tmp_tenth_type"
        assert not spec.verification_spec.is_empty()
        # Проверки ядра работают на новом типе без специальных веток
        report = A.audit_document(
            {"party_one": {"inn": "7707083890"}},
            doc_type="_tmp_tenth_type",
            spec=spec.verification_spec,
        )
        assert any(i.code == "INVALID_INN" for i in report.issues)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# =========================================================================
# 6.1: корректность декларативных денежных правил
# =========================================================================
def test_money_rules_come_from_plugin_not_auditor():
    registry = get_registry()
    for plugin_id, plugin in registry.enabled().items():
        for rule in plugin.verification_spec.money_rules:
            assert rule["code"], f"{plugin_id}: правило без кода"
            assert rule["components"], f"{plugin_id}: правило без слагаемых"
            assert rule["min_components"] <= len(rule["components"])


def test_salary_deduction_rule_from_plugin_file():
    """Сверка удержаний приходит из verification.json плагина."""
    spec = get_registry().get("salary_deductions").verification_spec
    assert spec.deduction_limit is not None
    assert "deduction_percentage" in spec.deduction_limit["path"]
    assert spec.money_rules and spec.money_rules[0]["code"] == "SALARY_DISCREPANCY"


def test_executive_document_four_component_rule_from_plugin_file():
    spec = get_registry().get("executive_documents").verification_spec
    rule = spec.money_rules[0]
    assert rule["code"] == "WRIT_MATH_DISCREPANCY"
    assert len(rule["components"]) == 4
    report = A.audit_document(
        {"finances": {"main_debt_rub": 157611.62, "interest_penalty_rub": 7004.39,
                      "court_fee_rub": 60000.0, "total_rub": 224616.01}},
        doc_type="executive_documents",
    )
    assert report.details["math_verified_ok"] is True

    report_bad = A.audit_document(
        {"finances": {"main_debt_rub": 157611.62, "interest_penalty_rub": 7004.39,
                      "court_fee_rub": 60000.0, "total_rub": 999999.0}},
        doc_type="executive_documents",
    )
    assert "WRIT_MATH_DISCREPANCY" in [i.code for i in report_bad.issues]


# =========================================================================
# 6.1: валидация конфигурации
# =========================================================================
def test_money_rule_with_impossible_min_components_is_rejected():
    spec = VerificationSpec({
        "money_rules": [{"code": "X", "total": ["a"], "components": [["c", ["b"]]],
                         "min_components": 5, "severity": "error"}]
    })
    assert spec.validate("demo"), "min_components больше числа слагаемых должно ловиться"


def test_valid_spec_reports_no_problems():
    spec = VerificationSpec({
        "money_rules": [{"code": "X", "total": ["a"], "components": [["c1", ["b"]], ["c2", ["d"]]],
                         "min_components": 2, "severity": "error"}]
    })
    assert not spec.validate("demo")


def test_spec_validation_rejects_plugin_at_load(tmp_path):
    """Некорректный verification.json не должен молча проходить."""
    plugin_dir = tmp_path / "bad_plugin"
    plugin_dir.mkdir()
    for name in ("manifest.json", "schema.py", "prompt.md", "classifier_rules.json",
                 "flat_columns.json", "benchmark.json", "autonomous.json"):
        source = DOC_TYPES / "commercial_contracts" / name
        shutil.copy(source, plugin_dir / name)
    manifest = json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["id"] = "bad_plugin"
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "verification.json").write_text(
        json.dumps({
            "money_rules": [{"code": "X", "total": ["a"],
                             "components": [["only", ["b"]]],
                             "min_components": 3, "severity": "error"}]
        }),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        PluginSpec(str(plugin_dir))
    assert "verification.json" in str(exc.value)


def test_benchmark_unknown_type_rejected_at_load(tmp_path):
    """C-06 не должен повториться: неизвестный тип поля ловится при загрузке."""
    plugin_dir = tmp_path / "bad_bench"
    plugin_dir.mkdir()
    for name in ("manifest.json", "schema.py", "prompt.md", "classifier_rules.json",
                 "flat_columns.json", "autonomous.json"):
        shutil.copy(DOC_TYPES / "commercial_contracts" / name, plugin_dir / name)
    manifest = json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest["id"] = "bad_bench"
    (plugin_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (plugin_dir / "benchmark.json").write_text(
        json.dumps({"fields": [{"path": "finances.total_rub", "type": "not_a_type", "weight": 1.0}]}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError) as exc:
        PluginSpec(str(plugin_dir))
    assert "not_a_type" in str(exc.value)


# =========================================================================
# Разбор путей с альтернативами
# =========================================================================
def test_alternative_paths_first_present_wins():
    data = {"act_date": "10.03.2021", "court": {"act_date": "01.01.2020"}}
    assert get_nested_value(data, "court.act_date|act_date") == "01.01.2020"
    assert get_nested_value(data, "absent|act_date") == "10.03.2021"
    assert get_nested_value(data, "nope|also_nope") is None


def test_empty_placeholder_is_treated_as_absent():
    data = {"doc_date": "  ", "ip_number": "98765/23/50026-ИП"}
    assert get_nested_value(data, "doc_date") == "  "
    from scan_reader.verifier.auditor import _first_alternative

    value, path = _first_alternative(data, ["doc_date", "ip_number"])
    assert value == "98765/23/50026-ИП"
    assert path == "ip_number"
