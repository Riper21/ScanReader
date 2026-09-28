# -*- coding: utf-8 -*-
"""
Tests for Cross-Modal Anti-Hallucination Gate.
"""

from scan_reader.verifier.hallucination_gate import (
    audit_cross_modal_consistency,
    check_presence_in_raw_text,
)
from scan_reader.verifier.spec import VerificationSpec


def test_check_presence():
    raw_ocr = "Взыскать с должника Иванова Ивана Ивановича, ИНН 7707083893, сумму 15000 рублей."
    assert check_presence_in_raw_text("7707083893", raw_ocr) is True
    assert check_presence_in_raw_text("Иванов", raw_ocr) is True
    assert check_presence_in_raw_text("Сидоров", raw_ocr) is False


def test_audit_cross_modal_consistency_detects_hallucination():
    raw_ocr = "Судебный приказ вынесен в отношении Петрова П.П., сумма 10000 руб."
    extracted = {
        "debtor": {
            "name": "Сидоров Алексей Васильевич",
            "inn": "7707083893",
        },
        "ip_number": "99999/22/11111-ИП",
    }

    # После Фазы 6 гейт проверяет поля из verification.json плагина, поэтому
    # здесь передаётся явная спецификация.
    spec = VerificationSpec({
        "gate_fields": [{"path": "debtor.inn", "min_length": 10},
                        {"path": "ip_number", "min_length": 5}],
        "gate_names": ["debtor.name"],
    })
    discrepancies = audit_cross_modal_consistency(extracted, raw_ocr, spec)
    assert len(discrepancies) >= 2
    fields = [d["field"] for d in discrepancies]
    assert "debtor.name" in fields
    assert "debtor.inn" in fields
