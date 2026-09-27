# -*- coding: utf-8 -*-
"""
Tests for Stage 0 fixes: Blockers B-01, B-02, B-03 and Critical C-10, C-15.
"""

from unittest.mock import MagicMock, patch
from scan_reader.type_registry import get_registry
from scan_reader.verifier.auditor import ZeroTrustAuditor
from scan_reader.verifier.math_verifier import verify_amounts_reconciliation
from scan_reader.verifier.status import VerificationStatus
from scan_reader.cli import handle_run, EXIT_OK, EXIT_DISCREPANCY, EXIT_FALLBACK


def test_b01_all_nine_plugins_loaded():
    """B-01: Ensure all 9 plugins are loaded with schemas and required files."""
    reg = get_registry()
    assert len(reg.plugins) == 9
    expected_ids = {
        "acceptance_certificates",
        "commercial_contracts",
        "enforcement_orders",
        "executive_documents",
        "hr_orders",
        "invoices_upd",
        "legal_claims",
        "powers_of_attorney",
        "salary_deductions",
    }
    assert set(reg.plugins.keys()) == expected_ids
    assert len(reg.load_errors) == 0


def test_b02_autonomous_json_dsl_validation():
    """B-02: Ensure all plugins have valid DSL objects in autonomous.json."""
    reg = get_registry()
    for pid, plugin in reg.plugins.items():
        fields = plugin.autonomous_config.get("fields", [])
        assert len(fields) > 0, f"Plugin {pid} has empty autonomous fields"
        for idx, item in enumerate(fields):
            assert isinstance(item, dict), f"Plugin {pid} field #{idx} is not a dict: {item}"
            assert "field" in item, f"Plugin {pid} field #{idx} missing 'field'"
            assert "rule" in item, f"Plugin {pid} field #{idx} missing 'rule'"


def test_b02_facade_validate_document_all_plugins():
    """B-02: Validate that facade.validate_document does not raise AttributeError for any plugin."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)
    for pid in facade.registry.plugins.keys():
        res = facade.validate_document({}, pid)
        assert isinstance(res, dict)
        assert "score" in res
        assert "passed" in res
        assert "issues" in res


def test_b03_zero_trust_auditor_resilience():
    """B-03: ZeroTrustAuditor must not crash on malformed/non-dict inputs from VLM."""
    malformed_inputs = [
        ("debtor is a string", {"debtor": "Иванов Иван Иванович"}),
        ("debtor is None", {"debtor": None}),
        ("finances is a string", {"finances": "всего 50000 руб"}),
        ("court is a list", {"court": ["Арбитражный суд", "Дело 123"]}),
        ("total is a string", {"finances": {"total_rub": "invalid_number"}}),
        ("data is a raw string", "неструктурированный текст"),
        ("nested non-dict structures", {
            "claimant": 12345,
            "payment_details": "р/с 40702810...",
            "seller": None,
            "buyer": ["ООО Покупатель"],
            "principal": False,
        }),
    ]

    for name, payload in malformed_inputs:
        report = ZeroTrustAuditor.audit_document(payload, doc_type="enforcement_orders")
        assert report is not None, f"Failed on {name}"
        assert isinstance(report.issues, list), f"Issues not a list on {name}"
        assert report.status in (
            VerificationStatus.DISCREPANCY_DETECTED,
            VerificationStatus.VLM_UNVERIFIED,
            VerificationStatus.ZERO_TRUST_VERIFIED,
            VerificationStatus.HEURISTIC_FALLBACK,
        ), f"Unexpected status {report.status} on {name}"


def test_b03_math_verifier_resilience():
    """B-03: verify_amounts_reconciliation must safely handle string amounts."""
    # Valid string amounts
    ok, msg = verify_amounts_reconciliation(debt="1000.0", fee_penalty="100.0", total="1100.0")
    assert ok is True

    # Financial discrepancy
    ok, msg = verify_amounts_reconciliation(debt="1000.0", fee_penalty="100.0", total="1200.0")
    assert ok is False
    assert "Financial discrepancy" in msg

    # Non-numeric string
    ok, msg = verify_amounts_reconciliation(debt="abc", total="1000.0")
    assert ok is False


def test_c15_bank_account_verification():
    """C-15: validate_bank_account must be triggered during audit."""
    payload = {
        "payment_details": {
            "payment_account": "40702810000000000000",  # invalid checksum
            "bik": "044525225",  # Sberbank BIK
        }
    }
    report = ZeroTrustAuditor.audit_document(payload, doc_type="enforcement_orders")
    issue_codes = [i.code for i in report.issues]
    assert "INVALID_BANK_ACCOUNT" in issue_codes
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED


def test_c10_cli_exit_codes(tmp_path):
    """C-10: handle_run must return EXIT_DISCREPANCY when zero_trust_status is discrepancy_detected."""
    fake_file = tmp_path / "test_doc.pdf"
    fake_file.write_text("dummy")

    args = MagicMock()
    args.scan_path = str(fake_file)
    args.output_dir = str(tmp_path)
    args.type = "auto"
    args.format = "default"
    args.json = False
    args.verbose = False

    # Mock process_single_document returning discrepancy
    mock_result_disc = {
        "doc_type": "enforcement_orders",
        "zero_trust_status": "discrepancy_detected",
    }

    with patch("scan_reader.cli.LegalDocPlatformFacade") as mock_facade_cls:
        instance = mock_facade_cls.return_value
        instance.process_single_document.return_value = mock_result_disc

        code = handle_run(args)
        assert code == EXIT_DISCREPANCY

        # Mock returning clean verified
        mock_result_ok = {
            "doc_type": "enforcement_orders",
            "zero_trust_status": "zero_trust_verified",
        }
        instance.process_single_document.return_value = mock_result_ok
        code = handle_run(args)
        assert code == EXIT_OK

        # Mock returning heuristic fallback
        mock_result_fb = {
            "doc_type": "enforcement_orders",
            "zero_trust_status": "heuristic_fallback",
        }
        instance.process_single_document.return_value = mock_result_fb
        code = handle_run(args)
        assert code == EXIT_FALLBACK
