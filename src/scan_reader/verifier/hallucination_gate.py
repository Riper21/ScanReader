"""
Cross-Modal Consistency Gate: verifies that critical entities extracted by VLM
actually exist in the raw OCR/text layer, preventing AI hallucinations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional


def normalize_token(text: str) -> str:
    """Normalize text token for resilient cross-modal matching."""
    s = str(text or "").lower()
    return re.sub(r"[\s\-_.,;:/\"'«»()]+", "", s)


def check_presence_in_raw_text(
    target_value: str,
    raw_text: str,
    min_length: int = 4,
    norm_raw: Optional[str] = None,
    digit_groups: Optional[List[str]] = None,
) -> bool:
    """
    Check if a target value (e.g. INN, surname, number) appears in raw text.

    :param norm_raw: предвычисленная нормализованная форма raw_text (M-08, оптимизация).
    :param digit_groups: предвычисленные группы цифр raw_text (M-08, точность).
    """
    if not target_value or len(target_value.strip()) < min_length:
        return True  # Too short to reliably gate

    norm_target = normalize_token(target_value)
    if norm_raw is None:
        norm_raw = normalize_token(raw_text)

    if norm_target in norm_raw:
        return True

    # Check for digit sequences if numeric.
    # M-08: совпадение ищется по ЦЕЛЫМ группам цифр исходного текста, а не по
    # склейке всех цифр документа (склейка давала ложные подтверждения через
    # границы соседних чисел — например, ИНН 'складывался' из хвоста одного
    # числа и начала другого).
    digits_target = re.sub(r"\D", "", target_value)
    if len(digits_target) >= 6:
        if digit_groups is None:
            digit_groups = re.findall(r"\d+", raw_text)
        if digits_target in digit_groups:
            return True

    return False


def audit_cross_modal_consistency(
    extracted_data: Dict[str, Any],
    raw_text: str,
) -> List[Dict[str, Any]]:
    """
    Audit extracted document fields against the raw OCR layer text.
    Returns list of discrepancies where critical entities could not be corroborated.
    """
    if not raw_text or len(raw_text.strip()) < 20:
        return []  # No raw text layer available to cross-examine

    if not isinstance(extracted_data, dict):
        return []

    def _as_dict(val: Any) -> Dict[str, Any]:
        return val if isinstance(val, dict) else {}

    # M-08: нормализация сырого текста и группы цифр вычисляются один раз
    norm_raw = normalize_token(raw_text)
    digit_groups = re.findall(r"\d+", raw_text)

    debtor_dict = _as_dict(extracted_data.get("debtor"))
    claimant_dict = _as_dict(extracted_data.get("claimant"))
    court_dict = _as_dict(extracted_data.get("court"))
    pay_dict = _as_dict(extracted_data.get("payment_details"))

    suspicious = []

    # 1. Check numbers and codes (INN, SNILS, case numbers)
    candidate_fields = [
        ("debtor.inn", debtor_dict.get("inn")),
        ("claimant.inn", claimant_dict.get("inn")),
        ("court.case_number", court_dict.get("case_number")),
        ("ip_number", extracted_data.get("ip_number")),
        ("reg_number", extracted_data.get("reg_number")),
        ("payment_details.uin", pay_dict.get("uin")),
        ("payment_details.bik", pay_dict.get("bik")),
    ]

    for field_path, val in candidate_fields:
        if val and isinstance(val, str) and len(val.strip()) >= 5:
            if not check_presence_in_raw_text(
                val, raw_text, norm_raw=norm_raw, digit_groups=digit_groups
            ):
                suspicious.append({
                    "field": field_path,
                    "extracted_value": val,
                    "reason": f"Value '{val}' not corroborated by raw OCR/text layer",
                })

    # 2. Check Debtor surname (first token of debtor name)
    debtor_name = debtor_dict.get("name") or extracted_data.get("debtor_name")
    if debtor_name and isinstance(debtor_name, str):
        tokens = [t for t in debtor_name.split() if len(t) >= 4]
        if tokens:
            surname = tokens[0]
            if not check_presence_in_raw_text(surname, raw_text, norm_raw=norm_raw, digit_groups=digit_groups):
                suspicious.append({
                    "field": "debtor.name",
                    "extracted_value": debtor_name,
                    "reason": f"Debtor identifier/surname '{surname}' not found in raw OCR/text",
                })

    return suspicious
