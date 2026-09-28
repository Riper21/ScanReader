# -*- coding: utf-8 -*-
"""
Модуль высоконадежного парсинга и нормализации денежных сумм (core/finance_parser.py).
Обеспечивает устойчивое извлечение сумм в рублях из текстов российских судебных актов,
постановлений ФССП и заявлений (с учетом копеек, прописи в скобках и различных разделителей).
"""

import re
from typing import Any, Optional, Dict

from .utils import get_logger

# Фаза 7.5: сырой logging.getLogger не имеет фильтра маскирования, поэтому
# секреты из разбираемых документов попадали в лог незамаскированными.
_parser_logger = get_logger("core.finance_parser")


def parse_russian_currency(val: Any) -> Optional[float]:
    """
    Преобразует произвольное значение (число или строку вида '125 432,50 руб.',
    '100 000 (сто тысяч) руб. 50 коп.', '45.000,00') в число float.

    ВАЖНО (Фаза 6.2): отрицательное значение НЕ превращается в None. Раньше
    VLM, вернувший -150000, получал «поле не заполнено» — неотличимо от
    отсутствия данных, то есть сведения о переплате терялись молча. Теперь знак
    сохраняется, и отрицательную сумму отвергает ограничение ge=0 схемы с
    понятным сообщением вместо тихой потери.
    """
    if val is None:
        return None
    if isinstance(val, (int, float)):
        # Исключаем 10- и 12-значные целые числа без указания валюты (ИНН)
        if isinstance(val, int) and (10**9 <= val < 10**10 or 10**11 <= val < 10**12):
            return None
        return round(float(val), 2)

    text = str(val).strip()
    if not text or text.lower() in ("none", "null", "-", "—", "нет"):
        return None
    if text in ("0", "0.0", "0,0", "0.00", "0 руб"):
        return 0.0

    # Отрицательная сумма: знак СОХРАНЯЕТСЯ, чтобы ограничение схемы сработало
    if re.search(r"(?:^|\s)-\s*\d", text):
        cleaned = re.sub(r"[^\d,.\s]", "", text).replace(" ", "").replace(",", ".")
        # Точка от сокращения «руб.» остаётся в хвосте: «100.50.» не парсится
        cleaned = cleaned.strip(".,")
        try:
            return -abs(float(cleaned))
        except ValueError:
            return None

    # Проверка наличия валютного маркера
    has_currency = bool(re.search(r"(?:руб|р\b|₽)", text, flags=re.IGNORECASE))

    # Если это процент (например, "50%"), не считать суммой, если нет валюты
    if "%" in text and not has_currency:
        return None

    # Очистка неразрывных пробелов и спецсимволов
    text = text.replace("\xa0", " ").replace("\u2009", " ").replace("\u202f", " ")

    # 1. Удаление поясняющего текста прописью в скобках: (сто тысяч рублей ...)
    text_clean = re.sub(r"\([^)]*\)", "", text).strip()

    # Если валютный маркер отсутствует, отклоняем идентификаторы (ИНН, ОГРН, СНИЛС, номера дел)
    if not has_currency:
        if re.search(r"\b(?:инн|кпп|огрн|огрнип|снилс|бик|л/с|р/с|дело|паспорт|№)\b", text_clean, flags=re.IGNORECASE):
            return None
        digits_only = re.sub(r"\D", "", text_clean)
        if len(digits_only) in (10, 12, 13, 15) and len(text_clean.split()) <= 2:
            return None

    # 2. Обработка формата "X руб. Y коп." или "X рублей Y копеек"
    match_rub_kop = re.search(r"(\d[\d\s\.,]*)\s*(?:руб|р\b|₽)[^\d]*(\d{1,2})\s*коп", text_clean, flags=re.IGNORECASE)
    if match_rub_kop:
        raw_rub = match_rub_kop.group(1).strip().replace(" ", "")
        kop_str = match_rub_kop.group(2)
        if "." in raw_rub and "," in raw_rub:
            if raw_rub.rfind(",") > raw_rub.rfind("."):
                raw_rub = raw_rub.replace(".", "").replace(",", ".")
            else:
                raw_rub = raw_rub.replace(",", "")
        elif "," in raw_rub:
            parts = raw_rub.split(",")
            if len(parts) == 2 and len(parts[1].strip()) <= 2:
                raw_rub = parts[0] + "." + parts[1].strip()
            else:
                raw_rub = raw_rub.replace(",", "")
        elif "." in raw_rub:
            parts = raw_rub.split(".")
            if len(parts) == 2 and len(parts[1].strip()) <= 2:
                raw_rub = parts[0] + "." + parts[1].strip()
            else:
                raw_rub = raw_rub.replace(".", "")

        try:
            rub_val = float(raw_rub)
            if rub_val < 0:
                return None
            if rub_val % 1 != 0:
                # В рублевой части уже содержались дробные копейки
                return round(rub_val, 2)
            return round(rub_val + float(kop_str) / 100.0, 2)
        except (ValueError, TypeError) as e:
            _parser_logger.debug(f"Не удалось разобрать сумму 'руб + коп' из '{text_clean}': {e}")

    # 3. Общий поиск числа с разделителями
    num_match = re.search(r"\b\d[\d\s\.,]*\d\b|\b\d+\b", text_clean)
    if not num_match:
        return None

    raw_num = num_match.group(0).strip()

    if "." in raw_num and "," in raw_num:
        if raw_num.rfind(",") > raw_num.rfind("."):
            raw_num = raw_num.replace(".", "").replace(",", ".")
        else:
            raw_num = raw_num.replace(",", "")
    elif "," in raw_num:
        parts = raw_num.split(",")
        if len(parts) == 2 and len(parts[1].strip()) <= 2:
            raw_num = parts[0].replace(" ", "") + "." + parts[1].strip()
        else:
            raw_num = raw_num.replace(",", "").replace(" ", "")
    else:
        parts = raw_num.split(".")
        if len(parts) == 2 and len(parts[1].strip()) <= 2:
            raw_num = parts[0].replace(" ", "") + "." + parts[1].strip()
        else:
            raw_num = raw_num.replace(" ", "")

    raw_num = raw_num.replace(" ", "")

    try:
        val_float = float(raw_num)
        if val_float < 0:
            return None
        if not has_currency and val_float >= 10**11:
            return None
        return round(val_float, 2)
    except Exception:
        return None


def extract_amounts_from_text(text: str) -> Dict[str, Optional[float]]:
    """
    Резервный Regex-сканер для поиска денежных сумм в неструктурированном тексте
    судебного акта или постановления пристава.
    """
    if not text:
        return {"total_rub": None, "main_debt_rub": None, "court_fee_rub": None, "penalty_rub": None}

    results: Dict[str, Optional[float]] = {
        "total_rub": None,
        "main_debt_rub": None,
        "court_fee_rub": None,
        "penalty_rub": None
    }

    # Паттерн строго для денежных сумм с обязательным указанием валюты (руб / коп / ₽)
    AMOUNT_PATTERN = r"(\d[\d\s\.,]{1,25}\s*(?:руб|р\b|₽)[^\d\n]*(?:\d{1,2}\s*коп)?|\d[\d\s\.,]{1,25}\s*(?:руб|р\b|₽))"

    # 1. Приоритетный поиск ИТОГОВОЙ суммы ("итого", "всего взыскать", "общая сумма задолженности")
    explicit_total_patterns = [
        r"(?:итого\s+к\s+взысканию|всего\s+к\s+взысканию|всего\s+взыскать|общей\s+суммы\s+задолженности\s+(?:в\s+размере)?|общая\s+сумма\s+(?:задолженности|взыскания|удержания))[^\d\n]{0,30}" + AMOUNT_PATTERN,
        r"(?:итого\s*[:\-–])[^\d\n]{0,20}" + AMOUNT_PATTERN,
    ]
    for p in explicit_total_patterns:
        match = re.search(p, text, flags=re.IGNORECASE)
        if match:
            parsed = parse_russian_currency(match.group(1))
            if parsed and parsed > 0:
                results["total_rub"] = parsed
                break

    # 2. Поиск госпошлины / расходов
    fee_match = re.search(
        r"(?:госпошлин[аыеу]|расход[ыов]\s+по\s+оплат[еы]\s+госпошлин[ые]|третейск(?:ий|ого)\s+сбор[ау]?)[^\d\n]{0,30}" + AMOUNT_PATTERN,
        text,
        flags=re.IGNORECASE
    )
    if fee_match:
        results["court_fee_rub"] = parse_russian_currency(fee_match.group(1))

    # 3. Поиск неустойки / пени / процентов
    penalty_match = re.search(
        r"(?:процент[ыов]|пен[иейя]|неустойк[ауеи]|штраф[ау]?)[^\d\n]{0,30}" + AMOUNT_PATTERN,
        text,
        flags=re.IGNORECASE
    )
    if penalty_match:
        results["penalty_rub"] = parse_russian_currency(penalty_match.group(1))

    # 4. Поиск основного долга
    debt_match = re.search(
        r"(?:основн(?:ой|ого)\s+долг[ау]?|задолженност[ьи]\s+по\s+договор[уа]|сумм[ау]\s+кредит[ау])[^\d\n]{0,30}" + AMOUNT_PATTERN,
        text,
        flags=re.IGNORECASE
    )
    if debt_match:
        results["main_debt_rub"] = parse_russian_currency(debt_match.group(1))

    # 5. Если явный "итого" не найден, ищем общие фразы взыскания (исключая проценты %)
    if not results["total_rub"]:
        general_total_patterns = [
            r"(?:взыскать\s+денежные\s+средства\s+в\s+размере|взыскани[ие]\s+задолженности\s+в\s+размере|суммы\s+задолженности\s+в\s+размере|сумму\s+задолженности\s+в\s+размере)[^\d\n]{0,30}" + AMOUNT_PATTERN,
            r"(?:взыскать\s+с[^\n]+?в\s+размере)[^\d\n]{0,30}" + AMOUNT_PATTERN,
            r"(?:в\s+размере)[^\d\n]{0,20}" + AMOUNT_PATTERN,
        ]
        for p in general_total_patterns:
            match = re.search(p, text, flags=re.IGNORECASE)
            if match:
                parsed = parse_russian_currency(match.group(1))
                if parsed and parsed > 0:
                    results["total_rub"] = parsed
                    break

    # 6. Если total_rub отсутствует или равен только одной части, суммируем слагаемые
    sub_sum = sum(v for v in [results["main_debt_rub"], results["court_fee_rub"], results["penalty_rub"]] if v is not None)
    if sub_sum > 0:
        if not results["total_rub"] or results["total_rub"] < sub_sum:
            results["total_rub"] = round(sub_sum, 2)

    return results
