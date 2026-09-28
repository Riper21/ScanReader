# -*- coding: utf-8 -*-
"""
Единый исполнитель правил autonomous.json (Guardrails DSL).

До появления этого модуля правила выполнялись в двух местах с РАЗНЫМИ словарями:
LegalDocPlatformFacade.validate_document знал {not_empty, valid_date_format,
positive_number_or_percentage, positive_number, valid_inn, ip_number_format,
ip_number_format_optional}, а core.metrics_evaluator — другой набор
{required, not_empty, date_format, positive_number, valid_inn} с веткой
«просто проверь на непустоту» для всего остального.

Из-за этого одно и то же поле получало противоположные вердикты: правило
valid_date_format пятью плагинами использовалось, но для метрик было невидимо,
поэтому «НЕ-ДАТА-ВОБЩЕ» получало 100.0 по пути метрик и правильно отклонялось
по пути фасада.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from ..verifier.checksums import validate_inn
from ..verifier.chronology import parse_flexible_date

# Полный словарь типов правил. Плагины, использующие правило вне этого набора,
# получают явное предупреждение и не считаются молча пройденными.
RULE_KINDS = frozenset({
    "not_empty",
    "required",              # синоним not_empty
    "valid_date_format",
    "date_format",           # синоним, ранее понимался только метриками
    "positive_number",
    "positive_number_or_percentage",
    "non_negative_number",
    "valid_inn",
    "valid_snils",
    "valid_ogrn",
    "valid_bik",
    "ip_number_format",
    "ip_number_format_optional",
    "valid_percentage",
    "deduction_limit",
})


def get_nested(data: Dict[str, Any], path: str) -> Any:
    """Извлечение вложенного значения по точечному пути (например 'finances.total_rub')."""
    if not isinstance(data, dict) or not path:
        return None
    curr: Any = data
    for part in path.split("."):
        if isinstance(curr, dict):
            curr = curr.get(part)
        else:
            return None
    return curr


def _to_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = re.sub(r"[^\d,.\-]", "", str(value))
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        tail = s.split(",")[-1]
        s = s.replace(",", ".") if len(tail) in (1, 2) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def validate_ip_number_format(ip_num: Optional[str]) -> Tuple[bool, float]:
    """
    Проверяет формат исполнительного производства (ХХХХХ/ГГ/ДД/РЕГИОН-ИП).

    S-12: длинные номера без разделителей подозрительны — VLM мог склеить или
    исказить номер; оценка снижается.
    """
    if not ip_num:
        return False, 0.0
    s = str(ip_num).strip()
    if "/" in s and any(c.isdigit() for c in s):
        return True, 100.0
    if len(s) >= 4 and any(c.isdigit() for c in s):
        return True, 80.0
    return False, 30.0


def _check_percentage_limit(value: Any, data: Dict[str, Any]) -> bool:
    from ..verifier.math_verifier import verify_deduction_percentage

    subject = " ".join(
        str(data.get(k) or "") for k in ("claim_subject", "claim", "subject", "deduction_basis")
    ).lower()
    has_basis = any(
        marker in subject
        for marker in ("алимент", "несовершеннолетн", "ребен", "содержание", "вред")
    )
    ok, _msg = verify_deduction_percentage(str(value), has_alimony_or_harm=has_basis)
    return ok


def evaluate_rule(rule_type: str, value: Any, data: Optional[Dict[str, Any]] = None) -> Tuple[bool, str]:
    """
    Выполняет одно правило autonomous.json.

    :returns: (is_valid, note). note непусто, если правило не реализовано.
    """
    data = data or {}

    if rule_type in ("not_empty", "required"):
        return bool(value is not None and str(value).strip()), ""

    if rule_type in ("valid_date_format", "date_format"):
        if not value:
            return False, ""
        return parse_flexible_date(str(value)) is not None, ""

    if rule_type == "positive_number":
        num = _to_number(value)
        return (num is not None and num > 0), ""

    if rule_type == "non_negative_number":
        num = _to_number(value)
        return (num is not None and num >= 0), ""

    if rule_type == "positive_number_or_percentage":
        if value is None:
            return False, ""
        s = str(value).strip().replace(" ", "").replace("\u00a0", "").replace(",", ".")
        if s.endswith("%"):
            s = s[:-1]
        num = _to_number(s)
        return (num is not None and num > 0), ""

    if rule_type == "valid_percentage":
        if value is None:
            return False, ""
        s = str(value).strip()
        if s.endswith("%"):
            s = s[:-1]
        num = _to_number(s)
        return (num is not None and 0 <= num <= 100), ""

    if rule_type == "deduction_limit":
        if not value:
            return True, ""
        return _check_percentage_limit(value, data), ""

    if rule_type in ("valid_inn", "valid_snils", "valid_ogrn", "valid_bik"):
        if not value:
            return False, ""
        from ..verifier.checksums import validate_bik, validate_ogrn, validate_snils

        checker = {
            "valid_inn": validate_inn,
            "valid_snils": validate_snils,
            "valid_ogrn": validate_ogrn,
            "valid_bik": validate_bik,
        }[rule_type]
        ok, _msg = checker(str(value))
        return bool(ok), ""

    if rule_type in ("ip_number_format", "ip_number_format_optional"):
        if not value:
            return (rule_type.endswith("_optional")), ""
        ok, score = validate_ip_number_format(str(value))
        return bool(ok and score >= 90.0), ""

    return False, f"Неизвестный тип правила '{rule_type}' в autonomous.json (не реализован в DSL)"


def run_guardrails(
    rules: List[Dict[str, Any]],
    data: Dict[str, Any],
    plugin_id: str = "",
    on_unknown_rule: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Выполняет набор правил autonomous.json.

    C-06: при отсутствии правил оценка НЕ равна 100.0. Раньше пустой набор давал
    идеальный балл, и плагин с пустым autonomous.json проходил как корректный.
    """
    issues: List[Dict[str, Any]] = []
    unknown: List[Dict[str, Any]] = []
    passed = 0
    evaluated = 0

    for rule in rules or []:
        if not isinstance(rule, dict):
            continue
        field_path = rule.get("field", "")
        rule_type = str(rule.get("rule", ""))
        severity = rule.get("severity", "MEDIUM")
        message = rule.get("message", f"Ошибка проверки {field_path}")
        value = get_nested(data, field_path)

        is_valid, note = evaluate_rule(rule_type, value, data)
        if note:
            unknown.append({
                "field": field_path,
                "rule": rule_type,
                "message": note,
            })
            if on_unknown_rule:
                on_unknown_rule(plugin_id, field_path, rule_type, note)
            continue

        evaluated += 1
        if is_valid:
            passed += 1
        else:
            issues.append({"field": field_path, "severity": severity, "message": message})

    score = round(passed / evaluated * 100.0, 1) if evaluated > 0 else 0.0
    return {
        "passed": len(issues) == 0 and evaluated > 0,
        "score": score,
        "issues": issues,
        "rules_total": evaluated,
        "rules_passed": passed,
        "rules_unknown": unknown,
        "evaluator_degraded": evaluated == 0,
    }
