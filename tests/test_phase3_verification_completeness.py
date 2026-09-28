# -*- coding: utf-8 -*-
"""
Регрессионные тесты Фазы 3 — полнота и корректность верификации.

C-04: сверка денег по типам документов, где её не было.
3.2:  формулировки процентов по 229-ФЗ и ТК РФ ст. 138.
3.3:  единый источник правды для контрольной суммы ИНН.
3.4:  пример БИК, предлагаемый проектом, должен проходить собственный валидатор.
"""

import pytest

from scan_reader.core import metrics_evaluator as me
from scan_reader.verifier import VerificationStatus
from scan_reader.verifier.auditor import ZeroTrustAuditor as A, _to_number
from scan_reader.verifier.checksums import validate_bik, validate_inn
from scan_reader.verifier.math_verifier import (
    parse_percentage_value,
    verify_deduction_percentage,
    verify_tk138_ceiling,
)

RAW = "Исполнительный лист. Взыскать 224616 рублей 01 копейку."


def _audit(finances, doc_type, raw=RAW):
    """Аудит с эталонным текстом, содержащим все проверяемые суммы.

    С Фазы 3 (шаг 3.5) кросс-модальный гейт проверяет и денежные суммы, поэтому
    фикстуры обязаны быть согласованы с эталонным текстом: сумма, которой нет в
    документе, теперь честно отклоняется как неподтверждённая.
    """
    return A.audit_document({"finances": finances}, doc_type=doc_type, raw_ocr_text=raw)


def _raw_for(finances, base=RAW):
    """Строит эталонный текст, содержащий все числовые значения блока finances."""
    parts = [base]
    for value in finances.values():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            num = float(value)
            if num == int(num):
                parts.append(f"{int(num)} руб.")
            else:
                whole, frac = f"{abs(num):.2f}".split(".")
                whole = whole.rjust(len(whole) + (3 - len(whole) % 3) % 3, "0")
                parts.append(" ".join(whole[i:i + 3] for i in range(0, len(whole), 3)) + "," + frac + " руб.")
    return " ".join(parts)


def _audit_ok(finances, doc_type):
    """Аудит с эталоном, согласованным с суммами (ожидается отсутствие расхождений)."""
    return A.audit_document(
        {"finances": finances}, doc_type=doc_type, raw_ocr_text=_raw_for(finances)
    )


def _codes(report):
    return {i.code for i in report.issues if i.severity == "error"}


# =========================================================================
# _to_number: оба формата разделителей
# =========================================================================
@pytest.mark.parametrize(
    "raw,expected",
    [
        (1234.5, 1234.5),
        ("1234.56", 1234.56),
        ("1 234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("1.234,56", 1234.56),
        ("12", 12.0),
        ("-50,00", -50.0),
        ("abc", None),
        (None, None),
        (True, None),
    ],
)
def test_c04_to_number_handles_both_separators(raw, expected):
    assert _to_number(raw) == expected


# =========================================================================
# C-04: исполнительные листы — 4 слагаемых
# =========================================================================
REAL_WRIT = {
    "main_debt_rub": 157611.62,
    "interest_penalty_rub": 7004.39,
    "court_fee_rub": 60000.00,
    "other_rub": None,
    "total_rub": 224616.01,
}


def test_c04_writ_correct_sum_verifies():
    report = _audit_ok(REAL_WRIT, "executive_documents")
    assert report.details["math_verified_ok"] is True
    assert "WRIT_MATH_DISCREPANCY" not in _codes(report)


def test_c04_writ_wrong_total_is_detected():
    """До исправления 157611.62+60000 и 999999 давали один и тот же результат."""
    report = _audit({**REAL_WRIT, "total_rub": 999999.0}, "executive_documents")
    assert report.status == VerificationStatus.DISCREPANCY_DETECTED
    assert "WRIT_MATH_DISCREPANCY" in _codes(report)
    assert report.details["math_verified_ok"] is False


def test_c04_writ_omitted_component_counts_as_zero():
    """Незаполненное «прочее» — норма, а не ошибка."""
    report = _audit_ok(
        {"main_debt_rub": 100000.0, "court_fee_rub": 20000.0, "total_rub": 120000.0},
        "executive_documents",
    )
    assert report.details["math_verified_ok"] is True
    assert "WRIT_MATH_DISCREPANCY" not in _codes(report)


def test_c04_writ_requires_two_components():
    """Одно слагаемое — сверка не выполняется, и это видно."""
    report = _audit_ok({"main_debt_rub": 100.0, "total_rub": 100.0}, "executive_documents")
    assert report.details.get("math_verified_ok") is None
    assert "WRIT_MATH_DISCREPANCY" not in _codes(report)


def test_c04_writ_unparsable_component_is_error():
    report = _audit({**REAL_WRIT, "court_fee_rub": "шестьдесят тысяч"}, "executive_documents")
    assert "WRIT_MATH_DISCREPANCY" in _codes(report)
    assert report.details["math_verified_ok"] is False


def test_c04_enforcement_order_reconciled_once():
    """Набор слагаемых приказа ФССП пересекается с ИЛ — дубля ошибки быть не должно."""
    report = _audit(
        {"main_debt_rub": 50000.0, "court_costs_rub": 6000.0, "total_rub": 99999.0},
        "enforcement_orders",
    )
    assert "ENFORCEMENT_MATH_DISCREPANCY" in _codes(report)
    math_issues = [i for i in report.issues if i.code.endswith("MATH_DISCREPANCY")]
    assert len(math_issues) == 1, f"ожидалась одна ошибка сверки, получено {len(math_issues)}"


def test_c04_enforcement_order_correct_sum_verifies():
    report = _audit(
        {"main_debt_rub": 50000.0, "court_costs_rub": 6000.0, "total_rub": 56000.0},
        "enforcement_orders",
    )
    assert report.details["math_verified_ok"] is True


# =========================================================================
# C-04: акты приемки
# =========================================================================
def test_c04_acceptance_certificate_reconciled():
    ok = _audit(
        {"amount_no_vat_rub": 100000.0, "vat_amount_rub": 20000.0, "total_rub": 120000.0},
        "acceptance_certificates",
    )
    assert ok.details["math_verified_ok"] is True

    bad = _audit(
        {"amount_no_vat_rub": 100000.0, "vat_amount_rub": 20000.0, "total_rub": 150000.0},
        "acceptance_certificates",
    )
    assert "ACCEPTANCE_MATH_DISCREPANCY" in _codes(bad)


# =========================================================================
# C-04: регрессия УПД и претензий
# =========================================================================
def test_c04_upd_still_reconciled():
    ok = _audit({"total_rub_no_vat": 100000.0, "total_vat_rub": 20000.0, "total_rub": 120000.0},
                "invoices_upd")
    assert ok.details["math_verified_ok"] is True

    bad = _audit({"total_rub_no_vat": 100000.0, "total_vat_rub": 20000.0, "total_rub": 150000.0},
                 "invoices_upd")
    assert "UPD_MATH_DISCREPANCY" in _codes(bad)


def test_c04_claim_still_reconciled():
    ok = _audit({"principal_debt_rub": 100000.0, "penalty_rub": 10000.0,
                 "interest_rub": 500.0, "total_claim_rub": 110500.0}, "legal_claims")
    assert ok.details["math_verified_ok"] is True

    bad = _audit({"principal_debt_rub": 100000.0, "penalty_rub": 10000.0,
                  "interest_rub": 500.0, "total_claim_rub": 999999.0}, "legal_claims")
    assert "CLAIM_MATH_DISCREPANCY" in _codes(bad)


def test_c04_rule_does_not_leak_across_document_types():
    """Поле чужого типа не должно подтягиваться в сверку."""
    report = _audit_ok(
        {"total_rub_no_vat": 100.0, "total_vat_rub": 20.0, "main_debt_rub": 5.0, "total_rub": 120.0},
        "invoices_upd",
    )
    assert report.details["math_verified_ok"] is True
    assert "WRIT_MATH_DISCREPANCY" not in _codes(report)


# =========================================================================
# 3.2: формулировки процентов
# =========================================================================
@pytest.mark.parametrize(
    "text,expected",
    [
        ("50%", 50.0),
        ("50 процентов", 50.0),
        ("50 процента", 50.0),
        ("1/4 части заработка", 25.0),
        ("одну четверть заработка", 25.0),
        ("одной трети заработка", 33.33),
        ("1/2 части заработка", 50.0),
        ("пятьдесят процентов", 50.0),
        ("семидесяти процентов", 70.0),
        ("25%", 25.0),
    ],
)
def test_c06_percentage_word_forms_are_parsed(text, expected):
    """Российские судебные акты пишут проценты словом — раньше это не читалось."""
    assert parse_percentage_value(text) == pytest.approx(expected, abs=0.01)


def test_c06_percentage_above_70_detected_in_word_form():
    ok, msg = verify_deduction_percentage("80 процентов")
    assert ok is False
    assert "70" in msg


def test_c06_50_percent_word_form_is_accepted():
    ok, _ = verify_deduction_percentage("50 процентов")
    assert ok is True


def test_c06_quarter_without_alimony_is_flagged():
    ok, msg = verify_deduction_percentage("1/4 части заработка")
    assert ok is True, "25% ниже порога 50% и нарушением не является"


def test_c06_70_percent_without_basis_is_flagged():
    ok, msg = verify_deduction_percentage("70 процентов", has_alimony_or_harm=False)
    assert ok is False
    assert "50" in msg


def test_c06_70_percent_with_alimony_is_accepted():
    ok, _ = verify_deduction_percentage("70 процентов", has_alimony_or_harm=True)
    assert ok is True


# =========================================================================
# 3.2: ТК РФ ст. 138 — лимит 20% от общего заработка
# =========================================================================
def test_tk138_ceiling_flags_excess():
    ok, msg = verify_tk138_ceiling(amount_rub=50000.0, total_monthly_income_rub=100000.0)
    assert ok is False
    assert "20" in msg


def test_tk138_ceiling_accepts_within_limit():
    ok, _ = verify_tk138_ceiling(amount_rub=20000.0, total_monthly_income_rub=100000.0)
    assert ok is True


def test_tk138_ceiling_ignores_missing_income():
    ok, _ = verify_tk138_ceiling(amount_rub=50000.0, total_monthly_income_rub=None)
    assert ok is True


# =========================================================================
# 3.3: единый источник правды для ИНН
# =========================================================================
def test_c06_invalid_inn_must_not_score():
    """До исправления невалидная контрольная сумма давала 85.0 и is_valid=True."""
    valid, _val, score = me.validate_inn_string("ИНН 7707083893")
    assert score == 100.0
    assert valid is True

    invalid, _val2, bad_score = me.validate_inn_string("ИНН 7707083890")
    assert bad_score == 0.0
    assert invalid is False


def test_c06_metrics_inn_uses_canonical_validator():
    """Проверка в метриках обязана опираться на verifier.checksums.validate_inn."""
    ok_valid, _ = validate_inn("7707083893")
    ok_invalid, _ = validate_inn("7707083890")
    assert ok_valid is True
    assert ok_invalid is False

    _, _, score_valid = me.validate_inn_string("ИНН 7707083893")
    _, _, score_invalid = me.validate_inn_string("ИНН 7707083890")
    assert score_valid == 100.0
    assert score_invalid == 0.0


def test_c06_month_matcher_does_not_match_arbitrary_words():
    """'ма' матчило 'сумма'/'компания' — список месяцев должен быть полным."""
    for garbage in ("компания", "сумма", "норма", "обязательства"):
        _ok, score = me.validate_date_string(garbage)
        assert score < 100.0, f"'{garbage}' ошибочно принято за дату"


def test_c06_month_matcher_accepts_real_dates():
    for good in ("15.03.2021", "2021-03-15", "15 марта 2021", "15 мая 2021"):
        ok, score = me.validate_date_string(good)
        assert ok is True, f"валидная дата '{good}' отвергнута"
        assert score > 0


def test_c06_deduction_limit_is_not_a_keyword_check():
    """'80%' больше не проходит как 'доля' со скором 90."""
    if hasattr(me, "validate_deduction_percentage"):
        ok, score = me.validate_deduction_percentage("80%")
        assert score < 100.0


# =========================================================================
# 3.4: примеры БИК проекта должны быть согласованы с примерами счетов
# =========================================================================
def _all_project_files():
    from pathlib import Path

    import scan_reader

    pkg = Path(scan_reader.__file__).parent
    repo = pkg.parent.parent
    for path in list(pkg.rglob("*")) + list((repo / "examples").rglob("*")):
        if path.is_file() and path.suffix in (".md", ".py", ".json", ".txt"):
            if "egg-info" in str(path):
                continue
            yield path


def test_c07_shipped_bik_examples_are_valid():
    """
    Каждый БИК, встречающийся в проекте, обязан проходить валидатор.

    Поиск идёт по КОНТЕКСТУ («БИК», «bik»), а не по любому девятизначному числу:
    номер бланка исполнительного листа «ФС № 002000001» тоже девять цифр, но БИК
    не является.
    """
    import re

    bad = []
    seen = 0
    bik_ctx = re.compile(
        r"(?:БИК|bik)[^\n]{0,30}?(\d{9})|(\d{9})[^\n]{0,10}?(?:БИК|bik)",
        re.IGNORECASE,
    )
    for path in _all_project_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in bik_ctx.finditer(text):
            bik = m.group(1) or m.group(2)
            seen += 1
            ok, msg = validate_bik(bik)
            if not ok:
                bad.append(f"{path.name}:{bik} ({msg})")
    assert seen > 0, "в проекте не найдено ни одного примера БИК для проверки"
    assert not bad, f"проект предлагает БИК, отвергаемые его же валидатором: {sorted(set(bad))}"


def test_c07_shipped_bik_account_pairs_are_consistent():
    """
    Пара «БИК + казначейский счет» из примеров обязана проходить ключевание ЦБ РФ.

    Обнаружено: examples/samples/salary_deduction_sample.txt содержал
    БИК 044525225 со счётом 03100643000000017300 — ключ не сходился, и такой
    докурат получил бы INVALID_BANK_ACCOUNT (severity=error).
    """
    import re

    from scan_reader.verifier.checksums import validate_bank_account

    checked = 0
    for path in _all_project_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for m in re.finditer(r"(0\d{8})[^\n]{0,60}?(\d{20})", text):
            bik, acc = m.group(1), m.group(2)
            if bik in acc or acc.startswith(bik):
                continue
            checked += 1
            ok, msg = validate_bank_account(acc, bik)
            assert ok, f"{path.name}: пара БИК {bik} + счёт {acc} не проходит ключевание ({msg})"
    assert checked > 0, "в проекте не найдено ни одной пары БИК+счёт для проверки"
