"""
Financial math verification and statutory limit checking for court and enforcement orders.
Checks monetary reconciliations and compliance with Federal Law No. 229-FZ / Labor Code.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Tuple

logger = logging.getLogger("verifier.math_verifier")


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
    def _coerce_float(val: Any) -> Optional[float]:
        if val is None or str(val).strip().lower() in ("", "none", "null"):
            return None
        if isinstance(val, (int, float)):
            return float(val)
        try:
            return float(str(val).replace(" ", "").replace(",", "."))
        except (ValueError, TypeError):
            return None

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


def parse_percentage_value(percent_str: str) -> Optional[float]:
    """Extract numeric percentage from Russian string (e.g. '50%', '25% ежемесячно')."""
    if not percent_str:
        return None
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*%", percent_str)
    if match:
        val_str = match.group(1).replace(",", ".")
        try:
            return float(val_str)
        except ValueError as e:
            logger.debug(f"Не удалось разобрать процентное значение '{percent_str}': {e}")
    return None


def verify_deduction_percentage(
    percentage_str: str,
    has_alimony_or_harm: bool = False,
) -> Tuple[bool, str]:
    """
    Verify percentage limit under Federal Law 229-FZ (Article 99):
    - Maximum standard salary deduction is 50%.
    - Maximum deduction for child support (алименты) or compensation of harm is 70%.
    - Anything above 70% is illegal.
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
            "Under Art. 99 229-FZ this requires an alimony or harm compensation order"
        )

    return True, f"Deduction percentage {val}% is within legal statutory limits"
