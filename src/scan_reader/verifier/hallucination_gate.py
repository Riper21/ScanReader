"""
Cross-Modal Consistency Gate: verifies that critical entities extracted by VLM
actually exist in the raw OCR/text layer, preventing AI hallucinations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .spec import get_nested_value


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
    spec: Optional[Any] = None,
) -> List[Dict[str, Any]]:
    """
    Сверяет извлечённые реквизиты с исходным текстом документа.

    Проверяемые поля приходят из VerificationSpec (verification.json плагина).
    Раньше перечисления были захардкожены (7 путей, из которых девять типов
    документов оставались вне гейта, а денежные суммы не проверялись вовсе),
    что нарушало Правило 3 AGENTS.md.

    :param spec: VerificationSpec; если не передана, берётся из реестра по
        doc_type экстракции, которого здесь нет, поэтому передаётся вызывающим.
    """
    if not raw_text or len(raw_text.strip()) < MIN_REFERENCE_LENGTH:
        return []  # Нет эталона для сверки
    if not isinstance(extracted_data, dict):
        return []

    gate_fields = list(getattr(spec, "gate_fields", None) or [])
    gate_names = list(getattr(spec, "gate_names", None) or [])
    gate_authorities = list(getattr(spec, "gate_authorities", None) or [])

    # M-08: нормализация и группы цифр вычисляются один раз
    norm_raw = normalize_token(raw_text)
    digit_groups = _numeric_atoms(raw_text)

    suspicious: List[Dict[str, Any]] = []
    seen: set = set()

    def _value_of(path: str) -> Any:
        return get_nested_value(extracted_data, path)

    # 1. Числовые реквизиты и денежные суммы
    for item in gate_fields:
        field_path = item["path"] if isinstance(item, dict) else str(item)
        min_length = int(item.get("min_length", 5)) if isinstance(item, dict) else 5
        val = _value_of(field_path)
        if val is None or isinstance(val, (bool, dict, list)):
            continue
        # Ноль не несёт идентифицирующей информации и не может быть
        # «подтверждён» текстом: сумма «0.00 руб.» и отсутствие строки
        # неразличимы. Проверять такой реквизит бессмысленно.
        if isinstance(val, (int, float)) and float(val) == 0.0:
            continue
        value = str(val).strip()
        if len(value) < min_length or value in seen:
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

    # 2. Наименования сторон: первый значимый токен
    for field_path in gate_names:
        val = _value_of(field_path)
        if not val or not isinstance(val, str):
            continue
        tokens = [t for t in re.split(r"[\s,]+", val) if len(t) >= 4]
        if not tokens:
            continue
        surname = tokens[0]
        if surname in seen:
            continue
        seen.add(surname)
        if not check_presence_in_raw_text(
            surname, raw_text, norm_raw=norm_raw, digit_groups=digit_groups
        ):
            suspicious.append({
                "field": field_path,
                "extracted_value": val,
                "reason": f"Party identifier '{surname}' not found in raw OCR/text",
            })

    # 3. Наименование суда и органа: отклоняется, только если не подтверждён
    # НИ ОДИН значимый токен, иначе легальные сокращения дают ложные срабатывания
    for field_path in gate_authorities:
        val = _value_of(field_path)
        if not val or not isinstance(val, str):
            continue
        tokens = [t for t in re.split(r"[\s,]+", val) if len(t) >= 4]
        if not tokens:
            continue
        confirmed = any(
            check_presence_in_raw_text(t, raw_text, norm_raw=norm_raw, digit_groups=digit_groups)
            for t in tokens
        )
        if not confirmed:
            suspicious.append({
                "field": field_path,
                "extracted_value": val,
                "reason": "Authority name not corroborated by raw OCR/text layer",
            })

    return suspicious