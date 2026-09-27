"""
Status taxonomy and verification dataclasses for ScanReader Zero-Trust Engine.
Follows explicit status semantics as established in TestCraft and Mermaid-Guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class VerificationStatus(str, Enum):
    """
    Explicit status taxonomy for document verification.
    """
    ZERO_TRUST_VERIFIED = "zero_trust_verified"
    VLM_UNVERIFIED = "vlm_unverified"
    HEURISTIC_FALLBACK = "heuristic_fallback"
    DISCREPANCY_DETECTED = "discrepancy_detected"
    OCR_LOW_CONFIDENCE = "ocr_low_confidence"
    REJECTED_UNSUPPORTED = "rejected_unsupported"


@dataclass
class VerificationIssue:
    severity: str  # "error", "warning", "info"
    code: str
    message: str
    field_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "field_name": self.field_name,
        }


@dataclass
class VerificationReport:
    status: VerificationStatus
    is_valid: bool
    issues: List[VerificationIssue] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(issue.severity == "warning" for issue in self.issues)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status.value,
            "is_valid": self.is_valid,
            "has_errors": self.has_errors,
            "has_warnings": self.has_warnings,
            "issues": [i.to_dict() for i in self.issues],
            "details": self.details,
        }
