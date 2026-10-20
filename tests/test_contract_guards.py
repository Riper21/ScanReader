# -*- coding: utf-8 -*-
"""
Стражи публичных контрактов 1.0.0 (план 04, этап 4).

STABILITY.md / STABILITY_RU.md объявляют поверхностность, замороженную
до 2.0.0. Эти тесты — enforcement: недокументированное изменение
публичного API падает здесь, в CI, а не у потребителя PyPI.

Правило правой руки: любое изменение эталонов ниже обязано сопровождаться
записью в STABILITY-документах и мажорным/минорным бампом версии по SemVer.
"""

import inspect
import json
import re
from pathlib import Path

from scan_reader.cli import (
    EXIT_DISCREPANCY,
    EXIT_ERROR,
    EXIT_FALLBACK,
    EXIT_OK,
    EXIT_USAGE,
    build_parser,
)
from scan_reader.mcp.server import ScanReaderMCPServer
from scan_reader.type_registry import get_registry
from scan_reader.verifier.status import VerificationStatus

REPO = Path(__file__).resolve().parent.parent
PKG = REPO / "src" / "scan_reader"


# =========================================================================
# CLI: коды выхода, подкоманды, поверхность флагов
# =========================================================================
def test_cli_exit_codes_frozen():
    assert (EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_DISCREPANCY, EXIT_FALLBACK) == (0, 1, 2, 3, 4)


def _cli_surface():
    parser = build_parser()
    subs = parser._subparsers._group_actions[0].choices
    surface = {}
    for name, sub in subs.items():
        flags = sorted({opt for act in sub._optionals._group_actions for opt in act.option_strings})
        fmt = next(
            (sorted(act.choices) for act in sub._optionals._group_actions
             if act.dest == "format" and act.choices),
            None,
        )
        positional = [act.dest for act in sub._positionals._group_actions if act.dest != "help"]
        surface[name] = {"flags": flags, "format": fmt, "positional": positional}
    return surface


def test_cli_subcommands_frozen():
    assert set(_cli_surface()) == {
        "run", "classify", "verify", "export", "benchmark", "doctor", "mcp",
    }


def test_cli_flags_and_format_choices_frozen():
    H = {"-h", "--help"}
    assert _cli_surface() == {
        "run": {
            "flags": sorted({"-f", "--format", "-t", "--type", "-o", "--output-dir", "--json"} | H),
            "format": ["1c", "both", "default", "flat", "full", "raw", "stdout"],
            "positional": ["scan_path"],
        },
        "classify": {"flags": sorted({"--json"} | H), "format": None, "positional": ["scan_path"]},
        "verify": {
            "flags": sorted({"-t", "--type", "--json"} | H),
            "format": None,
            "positional": ["json_path"],
        },
        "export": {
            "flags": sorted({"-f", "--format", "--json"} | H),
            "format": ["1c", "both", "excel", "flat", "full"],
            "positional": ["results_dir"],
        },
        "benchmark": {
            "flags": sorted({"-r", "--results-dir", "-g", "--ground-truth", "--json"} | H),
            "format": None,
            "positional": [],
        },
        "doctor": {"flags": sorted({"--json"} | H), "format": None, "positional": []},
        "mcp": {"flags": sorted(H), "format": None, "positional": []},
    }


# =========================================================================
# JSON-RPC / MCP: пять инструментов и их входные контракты
# =========================================================================
def test_mcp_tools_frozen():
    tools = {t["name"]: t["inputSchema"] for t in ScanReaderMCPServer().list_tools()}
    assert set(tools) == {
        "scan_document", "classify_document", "verify_legal_data", "run_benchmark", "export_results",
    }
    assert tools["scan_document"]["required"] == ["file_path"]
    assert sorted(tools["scan_document"]["properties"]) == ["doc_type", "file_path"]
    assert tools["classify_document"]["required"] == ["file_path"]
    assert tools["verify_legal_data"]["required"] == ["data", "doc_type"]
    assert sorted(tools["verify_legal_data"]["properties"]) == ["data", "doc_type", "raw_ocr_text"]
    assert sorted(tools["run_benchmark"]["properties"]) == ["ground_truth_path"]
    assert sorted(tools["export_results"]["properties"]) == ["results_dir", "target_format"]
    assert tools["export_results"]["properties"]["target_format"]["enum"] == ["excel", "1c", "both"]


def test_mcp_doc_type_enum_follows_registry():
    """Enum типов строится из реестра: новый плагин расширяет список additive."""
    tools = {t["name"]: t["inputSchema"] for t in ScanReaderMCPServer().list_tools()}
    enum = tools["scan_document"]["properties"]["doc_type"]["enum"]
    assert enum[0] == "auto"
    assert set(get_registry().ids()) <= set(enum)


# =========================================================================
# Словари статусов
# =========================================================================
FROZEN_VERIFICATION_STATUSES = {
    "zero_trust_verified", "partially_verified", "gate_not_executed",
    "vlm_unverified", "heuristic_fallback", "discrepancy_detected",
    "ocr_low_confidence", "rejected_unsupported",
}


def test_verification_status_vocabulary_frozen():
    assert {s.value for s in VerificationStatus} == FROZEN_VERIFICATION_STATUSES


def test_document_status_vocabulary_frozen():
    src = (PKG / "facade.py").read_text(encoding="utf-8")
    for status in ("COMPLETED", "FAILED", "NEEDS_REVIEW"):
        assert f'"{status}"' in src, f"Статус документа {status} исчез из facade.py"


# =========================================================================
# Запись реестра: верхний уровень Full-карточки
# =========================================================================
FROZEN_FULL_RECORD_KEYS = {
    "file_name", "file_path", "doc_type", "doc_type_title",
    "classification_confidence", "classification_method", "data", "validation",
    "extraction_model", "escalated", "escalation_reason",
    "zero_trust", "zero_trust_status", "quality_score_percent", "quality_status",
    "metrics", "measurement_caveats", "recovered_by_regex",
    "requires_human_review", "status", "errors", "processed_at",
}


def test_full_record_keys_frozen():
    src = (PKG / "facade.py").read_text(encoding="utf-8")
    match = re.search(r"result = \{(.*?)\n        \}", src, re.DOTALL)
    assert match, "Блок сборки result-записи не найден в facade.py"
    keys = set(re.findall(r'"([a-z_0-9]+)":', match.group(1)))
    assert keys == FROZEN_FULL_RECORD_KEYS


# =========================================================================
# Отчёт верификации: ключи публичного словаря
# =========================================================================
def test_verification_report_keys_frozen():
    from scan_reader.verifier.status import (
        VerificationIssue,
        VerificationReport,
        VerificationStatus,
    )

    report = VerificationReport(
        status=VerificationStatus.ZERO_TRUST_VERIFIED,
        is_valid=True,
        issues=[VerificationIssue("warning", "W01", "пример", "поле")],
        details={"gate_source": "text_layer"},
    )
    payload = report.to_dict()
    assert set(payload) == {
        "status", "is_valid", "has_errors", "has_warnings", "issues", "details",
    }
    assert payload["status"] == "zero_trust_verified"
    assert set(payload["issues"][0]) == {"severity", "code", "message", "field_name"}


# =========================================================================
# Переменные окружения: замороженный словарь
# =========================================================================
FROZEN_ENV_VARS = {
    "AI_MODEL_NAME", "ENABLE_CACHE", "ENABLE_RATE_LIMITER", "LOG_LEVEL", "NO_COLOR",
    "OPENAI_API_KEY", "OPENAI_BASE_URL",
    "SCANREADER_ALLOWED_DIRS", "SCANREADER_CACHE_DIR", "SCANREADER_CACHE_FLUSH_EVERY",
    "SCANREADER_CACHE_FLUSH_SECONDS", "SCANREADER_GROUND_TRUTH_DIR", "SCANREADER_HOME",
    "SCANREADER_INCOMING_DIR", "SCANREADER_MAX_CACHE_ENTRIES", "SCANREADER_MAX_PAGES",
    "SCANREADER_OCR_CROSSCHECK", "SCANREADER_OCR_ENABLED", "SCANREADER_OCR_ENGINE",
    "SCANREADER_OCR_LANG", "SCANREADER_OCR_MAX_PAGES", "SCANREADER_OCR_MIN_TEXT_LENGTH",
    "SCANREADER_OUTPUT_DIR", "SCANREADER_VLM_ATTEMPTS",
    "VLM_FAST_MODEL_NAME", "VLM_HTTP_TIMEOUT", "VLM_MAX_CONCURRENCY", "VLM_ROUTER_CONFIDENCE_MIN",
    "VLM_RPM_LIMIT", "VLM_SLOT_TIMEOUT", "VLM_TIMEOUT",
}


def test_env_vars_frozen():
    text = (REPO / ".env.example").read_text(encoding="utf-8")
    declared = set(re.findall(r"^#?\s*([A-Z][A-Z0-9_]+)=", text, re.MULTILINE))
    assert declared == FROZEN_ENV_VARS


# =========================================================================
# 1C-импорт: набор колонок flat_columns.json каждого плагина
# =========================================================================
FROZEN_FLAT_COLUMNS = {
    "acceptance_certificates": [
        "claim_subject", "contract_number", "contractor.inn", "contractor.name",
        "customer.inn", "customer.name", "doc_date", "doc_number",
        "finances.amount_no_vat_rub", "finances.total_rub", "finances.vat_amount_rub",
    ],
    "commercial_contracts": [
        "claim_subject", "contract_type", "doc_date", "doc_number",
        "finances.total_rub", "finances.total_rub_no_vat", "finances.vat_rate",
        "party_one.inn", "party_one.name", "party_two.inn", "party_two.name", "term_end",
    ],
    "enforcement_orders": [
        "claim_subject", "claimant.name", "court.case_number", "court.name",
        "debtor.name", "doc_date", "finances.total_rub", "fssp.name",
        "has_signature_stamp", "is_anonymized", "reg_number",
    ],
    "executive_documents": [
        "act_date", "blank_number", "blank_series", "case_number", "claim_subject",
        "claimant.name", "court.name", "debtor.name", "finances.total_rub",
        "has_signature_stamp", "is_anonymized",
    ],
    "hr_orders": [
        "director_signatory", "doc_date", "doc_number", "effective_date_from",
        "effective_date_to", "employee.full_name", "employee.personnel_number",
        "employee.position", "employee.salary_rub", "employee.structural_unit",
        "employee_signature_date", "employment_contract_date", "employment_contract_number",
        "form_code", "has_signatures", "order_type", "organization_inn", "organization_name",
    ],
    "invoices_upd": [
        "buyer.inn", "buyer.name", "contract_base", "doc_date", "doc_number",
        "finances.total_rub", "finances.total_rub_no_vat", "finances.total_vat_rub",
        "seller.inn", "seller.name", "status",
    ],
    "legal_claims": [
        "bank_requisites.bik", "bank_requisites.payment_account", "city",
        "claim_subject", "contract_basis_date", "contract_basis_number", "doc_date",
        "doc_number", "finances.interest_rub", "finances.penalty_rub",
        "finances.principal_debt_rub", "finances.total_claim_rub", "has_signature_stamp",
        "recipient.inn", "recipient.name", "response_deadline_date",
        "response_deadline_days", "sender.inn", "sender.name",
    ],
    "powers_of_attorney": [
        "agent.full_name", "agent.passport_number", "agent.passport_series", "agent.snils",
        "can_subdelegate", "city", "doc_number", "has_signature_stamp", "is_notarized",
        "issue_date", "powers_summary", "principal.inn", "principal.name", "principal.ogrn",
        "principal.signatory_fio", "valid_until",
    ],
    "salary_deductions": [
        "authority.name", "base_doc_date", "base_doc_number", "claimant_name",
        "debtor_name", "doc_date", "doc_number", "employer.name",
        "finances.deduction_percentage", "finances.total_deduction_rub",
        "has_signature_stamp", "ip_number", "payment_details.bik",
        "payment_details.payment_account", "payment_details.rosp_code", "payment_details.uin",
    ],
}


def test_flat_columns_paths_frozen():
    registry = get_registry()
    assert set(FROZEN_FLAT_COLUMNS) == set(registry.ids())
    for type_id in sorted(registry.ids()):
        spec = registry.get(type_id)
        columns = json.loads(
            (Path(spec.folder_path) / "flat_columns.json").read_text(encoding="utf-8")
        )
        paths = sorted(c["path"] for c in columns)
        assert paths == FROZEN_FLAT_COLUMNS[type_id], f"flat_columns изменён: {type_id}"


# =========================================================================
# Фасад: семь публичных методов, перечисленных в STABILITY
# =========================================================================
FROZEN_FACADE_METHODS = {
    "classify_via_vlm", "classify_document", "extract_document_data",
    "validate_document", "benchmark_against_ground_truth",
    "process_single_document", "process_batch",
}


def test_facade_public_methods_frozen():
    from scan_reader.facade import LegalDocPlatformFacade

    methods = {
        name for name, _ in inspect.getmembers(LegalDocPlatformFacade, inspect.isfunction)
        if not name.startswith("_")
    }
    assert methods == FROZEN_FACADE_METHODS


def test_stability_docs_list_facade_methods():
    for doc_name in ("STABILITY_RU.md", "STABILITY.md"):
        text = (REPO / doc_name).read_text(encoding="utf-8")
        for method in sorted(FROZEN_FACADE_METHODS):
            assert method in text, f"{doc_name} не упоминает метод фасада {method}"
