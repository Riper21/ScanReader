# -*- coding: utf-8 -*-
"""
Example: Running independent Zero-Trust verification on structured document data.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from scan_reader.verifier import ZeroTrustAuditor


def main():
    # 1. Sample document with valid legal & financial attributes
    valid_doc = {
        "court": {
            "act_date": "10.02.2023",
            "issue_date": "20.02.2023",
        },
        "debtor": {
            "name": "Иванов Иван Иванович",
            "inn": "7707083893",
            "snils": "112-233-445 95",
        },
        "finances": {
            "debt_amount_rub": 45000.00,
            "fee_penalty_rub": 3150.00,
            "total_deduction_rub": 48150.00,
            "deduction_percentage": "50% ежемесячно",
        },
        "doc_date": "25.02.2023",
    }

    report = ZeroTrustAuditor.audit_document(
        data=valid_doc,
        doc_type="salary_deductions",
        raw_ocr_text="Постановление в отношении должника Иванова И.И., ИНН 7707083893, сумма 48150 руб."
    )

    print("--- Audit Result: Valid Document ---")
    print("Status:", report.status.value)
    print("Is Valid:", report.is_valid)
    print("Issues:", len(report.issues))

    # 2. Sample document with a mathematical discrepancy and statutory violation
    broken_doc = {
        "debtor": {"inn": "7707083890"},  # Invalid INN
        "finances": {
            "debt_amount_rub": 10000.00,
            "fee_penalty_rub": 700.00,
            "total_deduction_rub": 12000.00,  # 10000 + 700 != 12000
            "deduction_percentage": "85%",    # Exceeds statutory 70% limit
        },
    }

    report_broken = ZeroTrustAuditor.audit_document(broken_doc, doc_type="salary_deductions")
    print("\n--- Audit Result: Broken Document ---")
    print("Status:", report_broken.status.value)
    print("Is Valid:", report_broken.is_valid)
    print("Detected Issues:")
    for issue in report_broken.issues:
        print(f"  • [{issue.severity.upper()}] {issue.code}: {issue.message}")


if __name__ == "__main__":
    main()
