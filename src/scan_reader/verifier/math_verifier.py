"""
Financial math verification and statutory limit checking for court and enforcement orders.
Checks monetary reconciliations and compliance with Federal Law No. 229-FZ / Labor Code.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

logger = logging.getLogger("verifier.math_verifier")


def _coerce_finite_number(val: Any) -> Optional[float]:
    """
    Приведение произвольного значения к конечному float.

    Общая защита от мусора от VLM: пустые строки, «None»/«null», строки с
    нечисловыми символами и бесконечности возвращают None, а не NaN/inf,
    с которыми арифметика молча даёт неверный вердикт.
    """
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        out = float(val)
    else:
        s = re.sub(r"[^\d,.\-]", "", str(val))
        if not s:
            return None
        if "," in s and "." in s:
            if s.rfind(",") > s.rfind("."):
                s = s.replace(".", "").replace(",", ".")
            else:
                s = s.replace(",", "")
        elif "," in s:
            tail = s.split(",")[-1]
            s = s.replace(",", ".") if len(tail) in (1, 2) else s.replace(",", "")
        try:
            out = float(s)
        except ValueError:
            return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


def verify_amounts_reconciliation(
    debt: Optional[Any] = None,
    fee_penalty: Optional[Any] = None,
    total: Optional[Any] = None,
    tolerance: float = 0.05,
) -> Tuple[bool, str]:
    """
    Verify that components sum up to the total deduction/debt within tolerance.
    Robust against string and non-numeric inputs.
    """
    _coerce_float = _coerce_finite_number

    if total is None:
        return True, "No total amount provided to reconcile"

    total_f = _coerce_float(total)
    if total_f is None:
        return False, f"Total amount cannot be parsed as number: {total}"

    debt_f = _coerce_float(debt)
    if debt is not None and debt_f is None:
        return False, f"Debt amount cannot be parsed as number: {debt}"

    fee_f = _coerce_float(fee_penalty)
    if fee_penalty is not None and fee_f is None:
        return False, f"Fee/penalty cannot be parsed as number: {fee_penalty}"

    if total_f < 0:
        return False, f"Total amount cannot be negative: {total_f}"

    if debt_f is not None and debt_f < 0:
        return False, f"Debt amount cannot be negative: {debt_f}"

    if fee_f is not None and fee_f < 0:
        return False, f"Fee/penalty cannot be negative: {fee_f}"

    if debt_f is not None and fee_f is not None:
        expected = round(debt_f + fee_f, 2)
        actual = round(total_f, 2)
        diff = abs(expected - actual)
        if diff > tolerance:
            return False, (
                f"Financial discrepancy: debt ({debt_f:.2f}) + fee ({fee_f:.2f}) = {expected:.2f}, "
                f"which does not match total ({actual:.2f}) by diff={diff:.2f} RUB"
            )
        return True, f"Reconciliation exact (debt + fee == total: {actual:.2f} RUB)"

    if debt_f is not None:
        if debt_f > total_f + tolerance:
            return False, f"Debt ({debt_f:.2f}) exceeds reported total ({total_f:.2f})"

    return True, "Amounts are logically consistent"


_PERCENT_WORDS = {
    "четверть": 25.0, "четвертую": 25.0, "четвертой": 25.0,
    "треть": 33.33, "трети": 33.33, "третью": 33.33,
    "половину": 50.0, "половина": 50.0, "половине": 50.0,
    "две трети": 66.67, "двух третей": 66.67,
    "десять": 10.0, "пятнадцать": 15.0, "двадцать": 20.0,
    "двадцать пять": 25.0, "тридцать": 30.0, "тридцать три": 33.0,
    "сорок": 40.0, "сорок пять": 45.0, "пятьдесят": 50.0,
    "шестьдесят": 60.0, "шестьдесят пять": 65.0, "семидесят": 70.0,
    "восемьдесят": 80.0, "девяносто": 90.0, "сто": 100.0,
}


def parse_percentage_value(percent_str: str) -> Optional[float]:
    """
    Извлечение числового процента из строки.

    Понимает проценты ('50%', '25% ежемесячно'), законные дробные доли
    ('1/4 заработка', '2/3 дохода' — S-10) и словесные формулировки
    ('50 процентов', 'семидесяти процентов', 'одну четверть'), которыми
    написаны настоящие судебные акты. Прежняя реализация требовала символ '%',
    из-за чего превышение лимита 229-ФЗ не обнаруживалось на документах, где
    процент выражен словом.
    """
    if not percent_str:
        return None
    s = str(percent_str).lower().replace("\u00a0", " ")
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", s)
    if match:
        val_str = match.group(1).replace(",", ".")
        try:
            return float(val_str)
        except ValueError as e:
            logger.debug(f"Не удалось разобрать процентное значение '{percent_str}': {e}")
        return None

    # «50 процентов», «семидесяти процентов», «25 процента»
    match = re.search(r"(\d+)\s*процент", s)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # «N ая/ой/ый часть»
    match = re.search(r"(\d+)\s*(?:ая|ой|ый)\s+часть", s)
    if match:
        try:
            return float(match.group(1))
        except ValueError:
            pass

    # Дробная доля: N/D, где D <= 12 и N <= D (отсекает номера ИП вида 10701/16/3001)
    frac = re.search(r"\b(\d{1,2})\s*/\s*(\d{1,2})(?!\d)", s)
    if frac:
        num, den = int(frac.group(1)), int(frac.group(2))
        if 0 < num <= den <= 12:
            return round(num * 100.0 / den, 2)
        logger.debug(f"Дробь '{frac.group(0)}' в '{percent_str}' не является долей удержания")
        return None

    for word, value in _PERCENT_WORDS.items():
        if word in s:
            return value
    return None


def verify_tk138_ceiling(
    amount_rub: Optional[Any],
    total_monthly_income_rub: Optional[Any] = None,
) -> Tuple[bool, str]:
    """
    Ст. 138 ТК РФ: удержание не может превышать 20% ОБЩЕГО месячного заработка.

    Отдельное правило, а не часть ст. 99 229-ФЗ: лимит 229-ФЗ (50/70%) считается
    от заработка НА РУКИ, лимит ст. 138 — от общего месячного дохода, и оба
    действуют одновременно. AGENTS.md заявлял соблюдение ТК РФ, но проверки не было.

    Если общий заработок не передан, проверка не выполняется: невозможно судить о
    превышении лимита, не зная базы исчисления.
    """
    amount = _coerce_finite_number(amount_rub)
    income = _coerce_finite_number(total_monthly_income_rub)
    if amount is None or income is None or income <= 0:
        return True, "Нет данных для проверки лимита ст. 138 ТК РФ"
    limit = round(income * 0.20, 2)
    if amount > limit + 0.05:
        return False, (
            f"Превышен лимит ст. 138 ТК РФ: удержание {amount:.2f} руб. > 20% "
            f"общего заработка ({income:.2f} руб.), допустимо {limit:.2f} руб."
        )
    return True, f"Удержание {amount:.2f} руб. в пределах 20% общего заработка ({limit:.2f} руб.)"


def verify_deduction_percentage(
    percentage_str: str,
    has_alimony_or_harm: bool = False,
) -> Tuple[bool, str]:
    """
    Проверка процента удержания по ст. 99 Федерального закона № 229-ФЗ.

    Правовая рамка:
    - ч. 1 — стандартный предел 50% от заработка НА РУКИ;
    - ч. 2 — до 70% при взыскании АЛИМЕНТОВ на несовершеннолетних детей (и при
      взыскании накопившихся алиментов за три и более года, с 2016 г.);
    - вред, причинённый здоровью, и смерть кормильца к 70% НЕ относятся: там
      компенсация выплачивается в полном объёме, 50%-й предел ст. 99 ч. 1 к ней
      не применяется (ФЗ № 314-ФЗ от 06.03.2019), и отдельного процентного
      потолка закон не устанавливает.

    Параметр назван has_alimony_or_harm исторически и сохранён ради совместимости
    вызывающего кода; по смыслу он означает наличие основания для превышения 50%.
    """
    val = parse_percentage_value(percentage_str)
    if val is None:
        return True, "No numeric percentage specified"

    if val <= 0:
        return False, f"Deduction percentage must be positive, got {val}%"

    if val > 70.0:
        return False, f"Statutory violation: deduction ({val}%) exceeds maximum legal limit (70%)"

    if val > 50.0 and not has_alimony_or_harm:
        return False, (
            f"Statutory limit alert: deduction is {val}% (> 50%). "
            "Under Art. 99 229-FZ exceeding 50% requires an alimony (child support) order; "
            "harm compensation is not subject to the 50% cap and has no 70% ceiling"
        )

    return True, f"Deduction percentage {val}% is within legal statutory limits"
