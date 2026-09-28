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


MIN_REFERENCE_LENGTH = 20

# Числовой «атом» исходного текста: maximal run of digits, possibly broken by
# thousands/decimal separators. "224 616,01" is ONE atom with digit sequence
# "22461601"; "177070838930" is one atom "177070838930". Comparing digit
# sequences atom-by-atom means a truncated identifier can never be "confirmed"
# from inside a longer number, while formatting variants still match.
#
# Пробел продолжает число ТОЛЬКО как разделитель тысяч, то есть за ним должны
# идти ровно три цифры. Без этого «ИНН 7707083893, 150000 руб.» сливается в
# один атом «7707083893150000», и оба реквизита перестают подтверждаться.
# Групповая форма проверяется первой, простая — второй.
_NUM_ATOM_RE = re.compile(
    r"\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?"
    r"|\d+(?:[.,]\d+)?"
)
_ALNUM_BOUNDARY = r"(?<![0-9a-zа-я]){}(?![0-9a-zа-я])"


def _numeric_atoms(raw_text: str) -> List[str]:
    """Цифровые последовательности исходного текста без разделителей."""
    atoms: List[str] = []
    for m in _NUM_ATOM_RE.finditer(raw_text):
        digits = re.sub(r"\D", "", m.group(0))
        if digits:
            atoms.append(digits)
    return atoms


def _numeric_candidates(value: str) -> List[str]:
    """
    Возможные записи числового значения в документе.

    VLM отдаёт суммы числами, документ печатает их с пробелами, запятыми и
    копейками. Для 1000.0 возможны «1000», «1000,00» и «1 000,00» — все должны
    подтверждаться; «100000» — уже другое число и подтверждаться не должен.

    Наивное «выбросить все не-цифры» для 100.0 даёт «1000», то есть другое
    число, поэтому при успешном разборе числа используются только числовые
    формы, а digit-strip применяется лишь как запасной вариант.
    """
    raw = str(value).strip()
    digits = re.sub(r"\D", "", raw)
    if not digits:
        return []
    out: List[str] = []
    try:
        num = float(raw.replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return [digits]
    if num == int(num):
        out.append(str(int(abs(num))))
    else:
        out.append(f"{abs(num):.2f}".replace(".", ""))
        out.append(str(abs(num)).replace(".", ""))
    return [d for d in dict.fromkeys(out) if d]


def check_presence_in_raw_text(
    target_value: str,
    raw_text: str,
    min_length: int = 3,
    norm_raw: Optional[str] = None,
    digit_groups: Optional[List[str]] = None,
) -> bool:
    """
    Check if a target value (e.g. INN, surname, number) appears in raw text.

    :param norm_raw: предвычисленная нормализованная форма raw_text (M-08, оптимизация).
    :param digit_groups: предвычисленные атомы цифр raw_text (M-08, точность).
    """
    if not target_value:
        return True
    value = str(target_value).strip()
    if not value:
        return True
    if len(value) < min_length:
        # Слишком короткое значение невозможно надёжно подтвердить, но и
        # опровергнуть нельзя: автопропуск молча оставлял такие поля
        # «непроверенными». Минимум снижен с 4 до 3.
        return True

    digits_target = re.sub(r"\D", "", value)
    has_letters = bool(re.search(r"[^\W\d_]", value, re.UNICODE))

    # Смешанный буквенно-цифровой идентификатор («А40-12345/2019», «98765/23/50026-ИП»)
    # подтверждается как ЦЕЛОЕ ТОКЕН по границам. Он не может быть собран из
    # числовых атомов: в исходном тексте его части разделены дефисами, поэтому
    # раньше такое значение не подтверждалось никогда.
    if has_letters:
        if norm_raw is None:
            norm_raw = normalize_token(raw_text)
        norm_target = normalize_token(value)
        if not norm_target:
            return True
        if re.search(_ALNUM_BOUNDARY.format(re.escape(norm_target)), norm_raw):
            return True
        if len(norm_target) >= 6:
            pattern = r".{0,2}".join(re.escape(ch) for ch in norm_target)
            if re.search(pattern, norm_raw):
                return True
        return False

    if digits_target:
        # Числовой идентификатор подтверждается только ЦЕЛЫМ числом исходного
        # текста. Раньше проверка шла и по склеенному blob без разделителей,
        # из-за чего усечённый «770708389» подтверждался внутри «7707083893».
        if digit_groups is None:
            digit_groups = _numeric_atoms(raw_text)
        atom_set = set(digit_groups)
        if any(candidate in atom_set for candidate in _numeric_candidates(value)):
            return True
        stripped = digits_target.lstrip("0")
        if stripped and any(atom.endswith(stripped) for atom in atom_set):
            return True
        return False

    # Чисто текстовое значение: поиск по границам слов, а не подстрокой.
    # «ИВАН» больше не подтверждается внутри «ИВАНОВ».
    if norm_raw is None:
        norm_raw = normalize_token(raw_text)
    norm_target = normalize_token(value)
    if not norm_target:
        return True
    if re.search(_ALNUM_BOUNDARY.format(re.escape(norm_target)), norm_raw):
        return True
    if len(norm_target) >= 6:
        pattern = r".{0,2}".join(re.escape(ch) for ch in norm_target)
        if re.search(pattern, norm_raw):
            return True
    return False


MIN_REFERENCE_LENGTH = 20


def audit_cross_modal_consistency(
    extracted_data: Dict[str, Any],
    raw_text: str,
) -> List[Dict[str, Any]]:
    """
    Audit extracted document fields against the raw OCR layer text.
    Returns list of discrepancies where critical entities could not be corroborated.
    """
    if not raw_text or len(raw_text.strip()) < MIN_REFERENCE_LENGTH:
        return []  # No raw text layer available to cross-examine

    if not isinstance(extracted_data, dict):
        return []

    def _as_dict(val: Any) -> Dict[str, Any]:
        return val if isinstance(val, dict) else {}

    # M-08: нормализация сырого текста и группы цифр вычисляются один раз
    norm_raw = normalize_token(raw_text)
    digit_groups = _numeric_atoms(raw_text)

    suspicious = []

    # 1. Числовые реквизиты: ИНН всех сторон, УИН, БИК, счёт, номера дел и ИП.
    # Раньше перечислялись 7 путей, и почти все плагины оставались вне гейта:
    # продавец, покупатель, заказчик, подрядчик, доверитель, поверенный, отправитель,
    # получатель, работник, организация, а также НИ ОДНОЙ денежной суммы —
    # выдуманный итог проходил беспрепятственно.
    def _nested(source: Any, *path: str) -> Any:
        cur: Any = source
        for part in path:
            if not isinstance(cur, dict):
                return None
            cur = cur.get(part)
        return cur

    numeric_candidates = [
        ("debtor.inn", ("debtor", "inn")),
        ("claimant.inn", ("claimant", "inn")),
        ("party_one.inn", ("party_one", "inn")),
        ("party_two.inn", ("party_two", "inn")),
        ("seller.inn", ("seller", "inn")),
        ("buyer.inn", ("buyer", "inn")),
        ("customer.inn", ("customer", "inn")),
        ("contractor.inn", ("contractor", "inn")),
        ("principal.inn", ("principal", "inn")),
        ("agent.inn", ("agent", "inn")),
        ("sender.inn", ("sender", "inn")),
        ("recipient.inn", ("recipient", "inn")),
        ("employee.inn", ("employee", "inn")),
        ("organization_inn", ("organization_inn",)),
        ("payment_details.recipient_inn", ("payment_details", "recipient_inn")),
        ("payment_details.uin", ("payment_details", "uin")),
        ("payment_details.bik", ("payment_details", "bik")),
        ("payment_details.payment_account", ("payment_details", "payment_account")),
        ("payment_details.account", ("payment_details", "account")),
        ("court.case_number", ("court", "case_number")),
        ("ip_number", ("ip_number",)),
        ("reg_number", ("reg_number",)),
        ("base_doc_number", ("base_doc_number",)),
        ("blank_number", ("blank_number",)),
        ("debtor.snils", ("debtor", "snils")),
        ("employee.snils", ("employee", "snils")),
        # Денежные суммы: главный вектор выдумывания, ранее не проверялся вовсе
        ("finances.total_rub", ("finances", "total_rub")),
        ("finances.total_deduction_rub", ("finances", "total_deduction_rub")),
        ("finances.main_debt_rub", ("finances", "main_debt_rub")),
        ("finances.debt_amount_rub", ("finances", "debt_amount_rub")),
        ("finances.court_fee_rub", ("finances", "court_fee_rub")),
        ("finances.total_claim_rub", ("finances", "total_claim_rub")),
    ]

    seen: set = set()
    for field_path, path in numeric_candidates:
        val = _nested(extracted_data, *path)
        if val is None or isinstance(val, bool) or isinstance(val, (dict, list)):
            continue
        value = str(val).strip()
        if len(value) < 5 or value in seen:
            continue
        seen.add(value)
        if not check_presence_in_raw_text(
            value, raw_text, norm_raw=norm_raw, digit_groups=digit_groups
        ):
            suspicious.append({
                "field": field_path,
                "extracted_value": value,
                "reason": f"Value '{value}' not corroborated by raw OCR/text layer",
            })

    # 2. Наименования сторон: фамилия/первый значимый токен.
    for field_path, path in (
        ("debtor.name", ("debtor", "name")),
        ("claimant.name", ("claimant", "name")),
        ("party_one.name", ("party_one", "name")),
        ("party_two.name", ("party_two", "name")),
        ("seller.name", ("seller", "name")),
        ("buyer.name", ("buyer", "name")),
        ("customer.name", ("customer", "name")),
        ("contractor.name", ("contractor", "name")),
        ("principal.name", ("principal", "name")),
        ("agent.name", ("agent", "name")),
        ("sender.name", ("sender", "name")),
        ("recipient.name", ("recipient", "name")),
    ):
        val = _nested(extracted_data, *path)
        if not val or not isinstance(val, str):
            continue
        tokens = [t for t in re.split(r"[\s,]+", val) if len(t) >= 4]
        if not tokens:
            continue
        surname = tokens[0]
        if not check_presence_in_raw_text(
            surname, raw_text, norm_raw=norm_raw, digit_groups=digit_groups
        ):
            suspicious.append({
                "field": field_path,
                "extracted_value": val,
                "reason": f"Party identifier '{surname}' not found in raw OCR/text",
            })

    # 3. Наименование суда и органа при прокуратуре: не менее двух значимых слов
    for field_path, path in (
        ("court.name", ("court", "name")),
        ("fssp.name", ("fssp", "name")),
        ("authority.name", ("authority", "name")),
    ):
        val = _nested(extracted_data, *path)
        if not val or not isinstance(val, str):
            continue
        tokens = [t for t in re.split(r"[\s,]+", val) if len(t) >= 4]
        missing = [
            t for t in tokens
            if not check_presence_in_raw_text(t, raw_text, norm_raw=norm_raw, digit_groups=digit_groups)
        ]
        if missing and len(missing) == len(tokens):
            suspicious.append({
                "field": field_path,
                "extracted_value": val,
                "reason": "Authority name not corroborated by raw OCR/text layer",
            })

    return suspicious
