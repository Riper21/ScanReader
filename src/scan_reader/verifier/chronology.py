"""
Date parsing and chronological consistency checks for legal proceedings.
Ensures that legal stages follow the temporal requirements of the law.
"""

from __future__ import annotations

import datetime
import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger("verifier.chronology")

_MONTHS_RU = {
    "января": 1, "январь": 1, "февраля": 2, "февраль": 2,
    "марта": 3, "март": 3, "апреля": 4, "апрель": 4,
    "мая": 5, "май": 5, "июня": 6, "июнь": 6,
    "июля": 7, "июль": 7, "августа": 8, "август": 8,
    "сентября": 9, "сентябрь": 9, "октября": 10, "октябрь": 10,
    "ноября": 11, "ноябрь": 11, "декабря": 12, "декабрь": 12,
}


def parse_flexible_date(date_raw: str) -> Optional[datetime.date]:
    """Parse various Russian date formats into a datetime.date."""
    if not date_raw:
        return None
    s = str(date_raw).strip().lower()

    # Format: YYYY-MM-DD
    m_iso = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", s)
    if m_iso:
        try:
            return datetime.date(int(m_iso.group(1)), int(m_iso.group(2)), int(m_iso.group(3)))
        except ValueError as e:
            logger.debug(f"Некорректная ISO-дата '{date_raw}': {e}")

    # Format: DD.MM.YYYY
    m_dot = re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{4})\b", s)
    if m_dot:
        try:
            return datetime.date(int(m_dot.group(3)), int(m_dot.group(2)), int(m_dot.group(1)))
        except ValueError as e:
            logger.debug(f"Некорректная дата '{date_raw}': {e}")

    # Format: DD/MM/YYYY (S-11: VLM периодически возвращает даты через слэш;
    # \b-границы + строгая валидация месяца/дня отсекают номера ИП вида 10701/16/3001)
    m_slash = re.search(r"(?:^|[^\w/])(\d{1,2})/(\d{1,2})/(\d{4})(?![\d/])", s)
    if m_slash:
        try:
            return datetime.date(int(m_slash.group(3)), int(m_slash.group(2)), int(m_slash.group(1)))
        except ValueError as e:
            logger.debug(f"Некорректная слэш-дата '{date_raw}': {e}")

    # Format: DD <month_ru> YYYY
    for m_name, m_num in _MONTHS_RU.items():
        if m_name in s:
            pattern = rf"(\d{{1,2}})\s+{m_name}\s+(\d{{4}})"
            m_text = re.search(pattern, s)
            if m_text:
                try:
                    return datetime.date(int(m_text.group(2)), m_num, int(m_text.group(1)))
                except ValueError as e:
                    logger.debug(f"Некорректная текстовая дата '{date_raw}': {e}")

    return None


def verify_chronology(
    act_date: Optional[str] = None,
    writ_date: Optional[str] = None,
    enforcement_date: Optional[str] = None,
    resolution_date: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Verify chronological sequence:
    Court Act Date <= Writ Date <= Enforcement Initiation Date <= Resolution Date
    """
    d_act = parse_flexible_date(act_date or "")
    d_writ = parse_flexible_date(writ_date or "")
    d_enf = parse_flexible_date(enforcement_date or "")
    d_res = parse_flexible_date(resolution_date or "")

    # Act date vs Writ date
    if d_act and d_writ and d_act > d_writ:
        return False, (
            f"Chronology inversion: Court Act Date ({d_act.isoformat()}) "
            f"is after Writ Date ({d_writ.isoformat()})"
        )

    # Writ date vs Enforcement initiation
    if d_writ and d_enf and d_writ > d_enf:
        return False, (
            f"Chronology inversion: Writ Date ({d_writ.isoformat()}) "
            f"is after Enforcement Initiation Date ({d_enf.isoformat()})"
        )

    # Enforcement initiation vs Resolution date
    if d_enf and d_res and d_enf > d_res:
        return False, (
            f"Chronology inversion: Enforcement Date ({d_enf.isoformat()}) "
            f"is after Resolution Date ({d_res.isoformat()})"
        )

    # Future date check
    today = datetime.date.today()
    threshold = today + datetime.timedelta(days=30)
    for name, dt in [("Act", d_act), ("Writ", d_writ), ("Enforcement", d_enf), ("Resolution", d_res)]:
        if dt and dt > threshold:
            return False, f"Date in future: {name} Date ({dt.isoformat()}) exceeds current date threshold"

    return True, "Chronology valid"
