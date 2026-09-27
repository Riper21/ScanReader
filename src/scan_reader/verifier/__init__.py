"""
Zero-Trust Verification Engine for ScanReader.
"""

from .status import VerificationStatus, VerificationIssue, VerificationReport
from .checksums import validate_inn, validate_snils, validate_ogrn, validate_bik, validate_bank_account
from .math_verifier import verify_amounts_reconciliation, verify_deduction_percentage
from .chronology import verify_chronology, parse_flexible_date
from .hallucination_gate import audit_cross_modal_consistency
from .auditor import ZeroTrustAuditor

__all__ = [
    "VerificationStatus",
    "VerificationIssue",
    "VerificationReport",
    "validate_inn",
    "validate_snils",
    "validate_ogrn",
    "validate_bik",
    "validate_bank_account",
    "verify_amounts_reconciliation",
    "verify_deduction_percentage",
    "verify_chronology",
    "parse_flexible_date",
    "audit_cross_modal_consistency",
    "ZeroTrustAuditor",
]
