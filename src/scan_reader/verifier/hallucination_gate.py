"""
Cross-Modal Consistency Gate: verifies that critical entities extracted by VLM
actually exist in the raw OCR/text layer, preventing AI hallucinations.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .spec import get_nested_value


#: Разделители, которые OCR-движки могут потерять или добавить внутри реквизита.
_SEP_OPT = r"[\s\-_.,;:/\\'\"«»()№]*"
_ALNUM_EDGE = r"(?<![0-9A-Za-zА-Яа-яЁё])"


def _search_form(text: str) -> str:
    """
    Форма текста для поиска с ГРАНИЦАМИ СЛОВ.

    Полное удаление разделителей (прежний подход) склеивало соседние слова:
    «АКТ-88 от» превращалось в «акт88от», и проверка по границам не находила
    «акт88» нигде. Для сопоставления реквизитов нужна форма, где границы
    СОХРАНЕНЫ, а внутри самого реквизита разделители допускаются.
    """
    s = str(text or "").lower().replace("ё", "е")
    return re.sub(r"\s+", " ", s).strip()


def _requisite_pattern(value: str) -> Optional[re.Pattern]:
    """
    Регулярное выражение для реквизита с необязательными разделителями.

    Каждая пара соседних символов реквизита может быть разделена разделителем,
    но края обязаны стоять на границе слова. Это позволяет подтвердить и
    «АКТ-88» по тексту «АКТ-88 от 01.02.2024», и «А40-12345/2019» по
    «дело А40-12345/2019».
    """
    chars = [c for c in value.lower().replace("ё", "е") if not c.isspace()]
    if not chars:
        return None
    body = _SEP_OPT.join(re.escape(c) for c in chars)
    return re.compile(_ALNUM_EDGE + body + r"(?![0-9A-Za-zА-Яа-я])", re.UNICODE)


#: Мягкие прилагательные/фамилии: «ий/ый/ой» склоняются с изменением основы
#: («Римский» -> «Римского», «Заводской» -> «Заводского»), поэтому для них
#: стем укорачивается на две буквы, а окончание допускается длиннее.
_SOFT_ENDINGS = ("ий", "ый", "ой")


def _inflection_patterns(value: str) -> List[re.Pattern]:
    """
    Паттерны падежной терпимости для текстовых значений (фамилии, органы).

    VLM возвращает фамилию в именительном падеже, а документ склоняет её:
    «Иванов» в «Взыскать с должника Иванова Ивана Ивановича». Правила:

      * основное — стем плюс окончание до 3 букв (-а, -у, -ом, -ой, -ич...),
        для фамилий на согласный («Иванов» -> «Иванова»);
      * мягкое — «ий/ый/ой» меняют основу («Римский» -> «Римского»):
        стем без двух последних букв плюс окончание до 4;
      * женское — «а/я» заменяются («Ромашка» -> «Ромашки», «Иванова» ->
        «Ивановой»): стем без последней буквы плюс окончание до 3.

    Ограничения защищают точность:
      * значение короче 5 символов не падежится вовсе — короткий «Иван»
        не подтверждается внутри «Иванов» (инвариант Фазы 3.5);
      * суффикс не длиннее 3-4 букв — «Иванов» не подтверждается внутри
        «Ивановский» (4 буквы суффикса, другой человек);
      * заменяющие правила требуют суффикс хотя бы в 1 букву.
    """
    stem = [c for c in value.lower().replace("ё", "е") if not c.isspace()]
    if len(stem) < 5:
        return []
    body = _SEP_OPT.join(re.escape(c) for c in stem)
    out = [re.compile(_ALNUM_EDGE + body + r"[а-яё]{0,3}(?![0-9A-Za-zА-Яа-яЁё])", re.UNICODE)]
    if len(stem) >= 7 and "".join(stem[-2:]) in _SOFT_ENDINGS:
        soft = _SEP_OPT.join(re.escape(c) for c in stem[:-2])
        out.append(
            re.compile(_ALNUM_EDGE + soft + r"[а-яё]{1,4}(?![0-9A-Za-zА-Яа-яЁё])", re.UNICODE)
        )
    if len(stem) >= 6 and stem[-1] in ("а", "я"):
        fem = _SEP_OPT.join(re.escape(c) for c in stem[:-1])
        out.append(
            re.compile(_ALNUM_EDGE + fem + r"[а-яё]{1,3}(?![0-9A-Za-zА-Яа-яЁё])", re.UNICODE)
        )
    return out


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

    Форма «руб.коп» здесь НЕ генерируется: у атомов цифр позиция запятой
    теряется, и «100» с «,00» неотличимы от «10000». Суммы с копейками
    сверяются числом в _numeric_value_match.
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


#: Предел числового сравнения. 20-значные счета в float не отличимы от
#: соседних, поэтому они сверяются только по последовательности цифр.
_MAX_NUMERIC_MATCH = 1e13


def _parse_amount(value: str) -> Optional[float]:
    """Числовое значение суммы/идентификатора, если оно однозначно разбирается."""
    try:
        num = float(str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    if num != num or abs(num) >= _MAX_NUMERIC_MATCH:
        return None
    return round(num, 2)


def _numeric_value_match(value: str, raw_text: str) -> bool:
    """
    Числовое равенство суммы и «бухгалтерской» записи в документе.

    VLM возвращает сумму числом («5075.0»), документ печатает её с разрядами
    и копейками («5 075,00»); атом цифр «507500» теряет позицию запятой, и
    сумма с нулевыми копейками никогда не подтверждалась. Здесь обе формы
    разбираются в число и сравниваются напрямую. ИНН/СНИЛС/ОГРН (10-13
    знаков) в число тоже укладываются точно, счета — нет (см. предел).
    """
    num = _parse_amount(value)
    if num is None:
        return False
    for m in _NUM_ATOM_RE.finditer(raw_text):
        try:
            atom = float(m.group(0).replace(" ", "").replace("\u00a0", "").replace(",", "."))
        except ValueError:
            continue
        if round(atom, 2) == num:
            return True
    return False


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
    has_separators = bool(re.search(r"[\s\-_.,;:/\\]", value))

    # Буквенные и смешанные реквизиты («Иванов», «А40-12345/2019», «АКТ-88»)
    # подтверждаются как ЦЕЛОЕ ЗНАЧЕНИЕ по границам слов. Прежний подход искал
    # по «склеенному» тексту без разделителей: соседние слова срастались, и
    # граница терялась в обе стороны.
    if has_letters:
        if norm_raw is None:
            norm_raw = _search_form(raw_text)
        pattern = _requisite_pattern(value)
        if pattern is not None and pattern.search(norm_raw):
            return True
        # Падежная терпимость: документ склоняет фамилии и наименования
        # («Иванов» в «Иванова Ивана Ивановича»), VLM отдаёт именительный.
        for inflected in _inflection_patterns(value):
            if inflected.search(norm_raw):
                return True
        # Мягкое совпадение: вставленный при распознавании символ не должна
        # давать расхождение. Замены букв здесь НЕТ — «Ивамов» и «Иванов»
        # остаются разными фамилиями.
        if len(value) >= 6:
            chars = [c for c in value.lower().replace("ё", "е") if not c.isspace()]
            if len(chars) >= 6:
                body = r".{0,1}".join(re.escape(c) for c in chars)
                if re.search(_ALNUM_EDGE + body + r"(?![0-9A-Za-zА-Яа-я])", norm_raw, re.UNICODE):
                    return True
        return False

    # Числовой по форме, но разделённый («2-1234/2015», «5075.0») требует
    # ОБЕИХ проверок: в числовой ветке склеенные цифры никогда не образуют
    # один атом, и без альтернативного пути такой номер помечался
    # галлюцинацией всегда.
    if has_separators and digits_target:
        if norm_raw is None:
            norm_raw = _search_form(raw_text)
        pattern = _requisite_pattern(value)
        if pattern is not None and pattern.search(norm_raw):
            return True
        # Сумма числом: «5075.0» подтверждается по «5 075,00». Для значений,
        # однозначно разбираемых в число, сверка по цифрам НЕ применяется:
        # атом «507500» из «5 075,00» подтверждал бы постороннюю сумму 507500.
        if _numeric_value_match(value, raw_text):
            return True
        if _parse_amount(value) is not None:
            return False
        # Идентификаторы с разделителями («2-1234/2015») — по цифрам.
        if digit_groups is None:
            digit_groups = _numeric_atoms(raw_text)
        if any(candidate in set(digit_groups) for candidate in _numeric_candidates(value)):
            return True
        return False

    if digits_target:
        # Числовой идентификатор подтверждается только ЦЕЛЫМ числом исходного
        # текста. Раньше проверка шла и по склеенному blob без разделителей,
        # из-за чего усечённый «770708389» подтверждался внутри «7707083893».
        # Суммы и короткие идентификаторы (ИНН, СНИЛС, ОГРН) сравниваются
        # числом: копейки и разряды не влияют, а десятикратная ошибка
        # («100000» против «100 000,00») не проходит.
        if _numeric_value_match(value, raw_text):
            return True
        if _parse_amount(value) is not None:
            return False
        # Счета (20 знаков) и прочие длинные идентификаторы — по цифрам.
        if digit_groups is None:
            digit_groups = _numeric_atoms(raw_text)
        atom_set = set(digit_groups)
        if any(candidate in atom_set for candidate in _numeric_candidates(value)):
            return True
        stripped = digits_target.lstrip("0")
        if stripped and any(atom.endswith(stripped) for atom in atom_set):
            return True
        return False

    # Значение из одних разделителей («№», «-»): подтверждать нечем, но и
    # опровергать нечего — как и для коротких значений, автопропуск.
    return True


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
    norm_raw = _search_form(raw_text)
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