# -*- coding: utf-8 -*-
"""
Tests for Zero-Trust verification engine:
Checksums (INN, SNILS, OGRN, BIK, Accounts), Math Reconciliations,
Statutory 229-FZ Limits, Chronology, and Auditor.
"""

from scan_reader.verifier import (
    VerificationStatus,
    ZeroTrustAuditor,
    split_deduction_basis,
    validate_bank_account,
    validate_bik,
    validate_inn,
    validate_ogrn,
    validate_snils,
    verify_amounts_reconciliation,
    verify_chronology,
    verify_deduction_percentage,
)


def test_inn_validation():
    # Valid 10-digit INN (Sberbank: 7707083893)
    ok, msg = validate_inn("7707083893")
    assert ok is True
    assert "Legal Entity" in msg

    # Invalid 10-digit INN
    ok, msg = validate_inn("7707083890")
    assert ok is False
    assert "Invalid 10-digit INN" in msg

    # Valid 12-digit INN (e.g. 500100732259)
    # Let's test a known formula:
    # 7*5 + 2*0 + 4*0 + 10*1 + 3*0 + 5*0 + 9*7 + 4*3 + 6*2 + 8*2 = 35+0+0+10+0+0+63+12+12+16 = 148 % 11 = 5, % 10 = 5
    # 3*5 + 7*0 + 2*0 + 4*1 + 10*0 + 3*0 + 5*7 + 9*3 + 4*2 + 6*2 + 8*5 = 15+0+0+4+0+0+35+27+8+12+40 = 141 % 11 = 9, % 10 = 9
    ok, msg = validate_inn("500100732259")
    assert ok is True
    assert "Individual" in msg

    # Invalid length
    ok, msg = validate_inn("12345")
    assert ok is False


def test_snils_validation():
    # Standard valid SNILS: 112-233-445 95
    # 1*9 + 1*8 + 2*7 + 2*6 + 3*5 + 3*4 + 4*3 + 4*2 + 5*1 = 9+8+14+12+15+12+12+8+5 = 95
    ok, msg = validate_snils("112-233-445 95")
    assert ok is True

    # Invalid checksum
    ok, msg = validate_snils("112-233-445 00")
    assert ok is False

    # Historical range
    ok, msg = validate_snils("001-001-997 00")
    assert ok is True


def test_ogrn_validation():
    # Valid 13-digit OGRN: 1027700132195 (Sberbank)
    # 102770013219 % 11 = 5
    ok, msg = validate_ogrn("1027700132195")
    assert ok is True

    # Invalid OGRN
    ok, msg = validate_ogrn("1027700132190")
    assert ok is False


def test_bik_validation():
    ok, msg = validate_bik("044525225")
    assert ok is True

    # S-5: казначейские БИКи УФК (01xxxxxxx) валидны — типичный получатель в документах ФССП
    ok, msg = validate_bik("015004950")
    assert ok is True

    # Неверный префикс (не 04 и не 01)
    ok, msg = validate_bik("124525225")
    assert ok is False


def test_bank_account_validation():
    # Sberbank correspondent account: 30101810400000000225 with BIK 044525225
    ok, msg = validate_bank_account("30101810400000000225", "044525225")
    assert ok is True


def test_math_reconciliation():
    # Exact match: debt 1000 + fee 70 = 1070
    ok, msg = verify_amounts_reconciliation(debt=1000.0, fee_penalty=70.0, total=1070.0)
    assert ok is True

    # Discrepancy: debt 1000 + fee 70 != 1200
    ok, msg = verify_amounts_reconciliation(debt=1000.0, fee_penalty=70.0, total=1200.0)
    assert ok is False
    assert "Financial discrepancy" in msg

    # Negative total
    ok, msg = verify_amounts_reconciliation(total=-100.0)
    assert ok is False


def test_deduction_percentage_limits():
    # Valid 50%
    ok, msg = verify_deduction_percentage("50% ежемесячно", has_alimony=False)
    assert ok is True

    # Alert > 50% without alimony
    ok, msg = verify_deduction_percentage("70%", has_alimony=False)
    assert ok is False
    assert "Statutory limit alert" in msg

    # Allowed 70% with alimony
    ok, msg = verify_deduction_percentage("70%", has_alimony=True)
    assert ok is True

    # Prohibited > 70%
    ok, msg = verify_deduction_percentage("80%", has_alimony=True)
    assert ok is False
    assert "Statutory violation" in msg


def test_deduction_percentage_harm_to_health_has_no_ceiling():
    """
    P1.1 (Правило 2 AGENTS.md): возмещение вреда здоровью — без процентного
    потолка (ФЗ № 314-ФЗ от 06.03.2019). Раньше «вред» смешивался с алиментами
    и подчинялся 70%-лимиту, которого для него не существует.
    """
    # 100% на возмещение вреда здоровью — passed с пояснением, а не провал
    ok, msg = verify_deduction_percentage("100%", has_harm=True)
    assert ok is True
    assert "314-FZ" in msg

    # 80% при алиментах + вреде — passed: вред снимает потолок
    ok, msg = verify_deduction_percentage("80%", has_alimony=True, has_harm=True)
    assert ok is True

    # 100% при алиментах без вреда — превышение 70%-потолка алиментов
    ok, msg = verify_deduction_percentage("100%", has_alimony=True)
    assert ok is False
    assert "Statutory violation" in msg

    # 150% — не процент вовсе, а мусор от модели
    ok, msg = verify_deduction_percentage("150%", has_harm=True)
    assert ok is False
    assert "not a valid percentage" in msg


def test_split_deduction_basis_separates_alimony_and_harm():
    """P1.1: разложение текста основания на два независимых флага."""
    a, h = split_deduction_basis("взыскание алиментов на несовершеннолетних детей")
    assert (a, h) == (True, False)

    a, h = split_deduction_basis("возмещение вреда, причинённого здоровью")
    assert (a, h) == (False, True)

    a, h = split_deduction_basis("тяжкий вред здоровью, компенсация")
    assert (a, h) == (False, True)

    # Моральный вред без «здоров» harm'ом не считается
    a, h = split_deduction_basis("компенсация морального вреда")
    assert (a, h) == (False, False)

    # Смерть кормильца — 70%-ветка вместе с алиментами
    a, h = split_deduction_basis("вред в связи со смертью кормильца")
    assert (a, h) == (True, False)

    a, h = split_deduction_basis("")
    assert (a, h) == (False, False)


def test_chronology_verification():
    # Valid chronological order
    ok, msg = verify_chronology(
        act_date="10.01.2023",
        writ_date="15.01.2023",
        enforcement_date="01.02.2023",
        resolution_date="10.02.2023",
    )
    assert ok is True

    # Inversion: act date after writ date
    ok, msg = verify_chronology(act_date="20.02.2023", writ_date="15.01.2023")
    assert ok is False
    assert "Chronology inversion" in msg


def test_zero_trust_auditor_full():
    # Реквизиты соответствуют verification.json плагина salary_deductions: у этого
    # типа нет вложенного «debtor» (используется плоское debtor_name), а ИНН
    # получателя платежа лежит в payment_details.recipient_inn.
    sample_doc = {
        "court": {
            "act_date": "10.03.2021",
            "issue_date": "15.03.2021",
        },
        "debtor_name": "Иванов Иван Иванович",
        "payment_details": {"recipient_inn": "7707083893"},
        "finances": {
            "debt_amount_rub": 50000.0,
            "fee_penalty_rub": 3500.0,
            "total_deduction_rub": 53500.0,
            "deduction_percentage": "50%",
        },
        "doc_date": "20.03.2021",
    }

    report = ZeroTrustAuditor.audit_document(sample_doc, doc_type="salary_deductions")
    assert report.is_valid is True
    assert report.status == VerificationStatus.ZERO_TRUST_VERIFIED
    assert report.has_errors is False
    assert report.details["checksums_verified_ok"] >= 1
    assert report.details["math_verified_ok"] is True


def test_p13_unreadable_short_identifier_is_flagged():
    """
    P1.3 (Правило 1): обрывок идентификатора (1-8 знаков) раньше молча
    выпадал из всех проверок — ни warning, ни чексуммы.
    """
    sample_doc = {
        "payment_details": {"recipient_inn": "123"},  # обрывок вместо 10-значного ИНН
        "finances": {
            "debt_amount_rub": 50000.0,
            "fee_penalty_rub": 3500.0,
            "total_deduction_rub": 53500.0,
        },
    }
    report = ZeroTrustAuditor.audit_document(sample_doc, doc_type="salary_deductions")
    codes = [i.code for i in report.issues]
    assert "UNREADABLE_IDENTIFIER" in codes
    # Короткое значение не должно попадать в знаменатель чексумм
    assert report.details["checksums_checked"] == 0
    # Warning, а не error: документ валиден, но реквизит нечитаем
    assert report.is_valid is True
    unreadable = next(i for i in report.issues if i.code == "UNREADABLE_IDENTIFIER")
    assert unreadable.severity == "warning"
    assert unreadable.field_name == "payment_details.recipient_inn"
    # Само значение не выводится в сообщении (Правило 5 — ПДн не утекают)
    assert "123" not in unreadable.message


def test_p13_account_without_bik_is_flagged():
    """
    P1.3: счёт без БИК раньше молча пропускался — ключ счета по 565-П
    не проверялся вовсе, и в отчёте не было ни строчки об этом.
    """
    sample_doc = {
        "payment_details": {
            "recipient_inn": "7707083893",
            "payment_account": "40817810000000000001",
        },
        "finances": {
            "debt_amount_rub": 50000.0,
            "fee_penalty_rub": 3500.0,
            "total_deduction_rub": 53500.0,
        },
    }
    report = ZeroTrustAuditor.audit_document(sample_doc, doc_type="salary_deductions")
    codes = [i.code for i in report.issues]
    assert "ACCOUNT_WITHOUT_BIK" in codes
    warning = next(i for i in report.issues if i.code == "ACCOUNT_WITHOUT_BIK")
    assert warning.severity == "warning"
    assert warning.field_name == "payment_details.payment_account"
    # БИК отсутствует в документе — это не ошибка, но и не «проверено»
    assert report.is_valid is True


def test_zero_trust_auditor_on_upd_and_claims():
    # 1. Valid UPD
    upd_valid = {
        "seller": {"inn": "7707083893"},
        "buyer": {"inn": "7707083893"},
        "finances": {
            "total_rub_no_vat": 100000.0,
            "total_vat_rub": 20000.0,
            "total_rub": 120000.0,
        },
    }
    rep_upd = ZeroTrustAuditor.audit_document(upd_valid, doc_type="invoices_upd")
    assert rep_upd.is_valid is True
    assert rep_upd.status == VerificationStatus.ZERO_TRUST_VERIFIED
    assert rep_upd.details["checksums_verified_ok"] >= 1
    assert rep_upd.details["math_verified_ok"] is True

    # 2. Math discrepancy in UPD
    upd_bad_math = {
        "seller": {"inn": "7707083893"},
        "finances": {
            "total_rub_no_vat": 100000.0,
            "total_vat_rub": 20000.0,
            "total_rub": 150000.0,  # Wrong total!
        },
    }
    rep_bad_upd = ZeroTrustAuditor.audit_document(upd_bad_math, doc_type="invoices_upd")
    assert rep_bad_upd.is_valid is False
    assert rep_bad_upd.status == VerificationStatus.DISCREPANCY_DETECTED
    assert any(i.code == "UPD_MATH_DISCREPANCY" for i in rep_bad_upd.issues)

    # 3. Legal claim discrepancy
    claim_bad = {
        "sender": {"inn": "7707083893"},
        "finances": {
            "principal_debt_rub": 50000.0,
            "penalty_rub": 5000.0,
            "interest_rub": 1000.0,
            "total_claim_rub": 70000.0,  # Wrong total!
        },
    }
    rep_claim = ZeroTrustAuditor.audit_document(claim_bad, doc_type="legal_claims")
    assert rep_claim.is_valid is False
    assert any(i.code == "CLAIM_MATH_DISCREPANCY" for i in rep_claim.issues)

    # 4. Inverted dates in Power of Attorney
    poa_bad_dates = {
        "principal": {"inn": "7707083893"},
        "agent": {"snils": "112-233-445 95"},
        "issue_date": "20.05.2024",
        "valid_until": "10.05.2023",  # Expired before issue!
    }
    rep_poa = ZeroTrustAuditor.audit_document(poa_bad_dates, doc_type="powers_of_attorney")
    assert rep_poa.is_valid is False
    assert any(i.code == "INVALID_VALIDITY_TERM" for i in rep_poa.issues)

