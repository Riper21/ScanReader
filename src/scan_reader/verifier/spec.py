# -*- coding: utf-8 -*-
"""
Декларативное описание верификации типа документа.

AGENTS.md Правило 3 требует, чтобы ядро системы НЕ содержало захардкоженных
категорий документов. До этого шага auditor.py перечислял 20 путей к ИНН,
5 путей к ОГРН, 3 к СНИЛС и 33 пути кросс-модального гейта руками, а
metrics_evaluator — 9 идентификаторов плагинов. Добавление десятого типа
документа требовало правки ядра.

VerificationSpec переносит эти знания в данные: каждый плагин получает
verification.json (восьмой файл контракта), а ядро превращается в
интерпретатор, которому не известны ни имена сторон, ни коды документов.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..core.utils import get_logger

logger = get_logger("verifier.spec")

VERIFICATION_FILENAME = "verification.json"

#: Виды проверки идентификатора. Соответствуют функциям verifier.checksums.
IDENTIFIER_KINDS = frozenset({"inn", "snils", "ogrn", "ogrnip", "bik", "bank_account"})

#: Виды структурной (несверяемой контрольной суммой) проверки реквизита.
FORMAT_KINDS = frozenset({"kpp", "oktmo", "uin_rosp", "doc_ref_number"})

#: Роли банковского блока. Это словарь ИНТЕРПРЕТАТОРА, а не поля документа:
#: конкретные пути задаёт плагин (payment_details.bik, bank_requisites.account и т.п.).
BANK_ROLES = ("bik", "account", "uin", "rosp_code", "kpp", "oktmo", "recipient_inn", "recipient_name")


def _as_paths(value: Any) -> List[str]:
    """Принимает строку или список строк и возвращает список."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value if v]
    return []


class VerificationSpec:
    """
    Описание того, что и как проверять для одного типа документа.

    Ничего не знает о конкретных типах документов: все имена полей приходят
    из verification.json плагина.
    """

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        raw = data or {}

        # Стороны документа и их идентификаторы.
        # [{"path": "<корневое поле стороны>", "label": "человекочитаемое имя",
        #   "ids": ["inn", "ogrn", "snils"]}, ...]
        self.parties: List[Dict[str, Any]] = []
        for item in raw.get("parties", []) or []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            if not path:
                continue
            ids = [str(i).lower() for i in _as_paths(item.get("ids")) if str(i).lower() in IDENTIFIER_KINDS]
            self.parties.append({
                "path": path,
                "label": str(item.get("label", path)),
                "ids": ids,
            })

        # Плоские идентификаторы верхнего уровня:
        # [{"path": "organization_inn", "kind": "inn", "severity": "error"}]
        self.identifier_checks: List[Dict[str, Any]] = []
        for item in raw.get("identifier_checks", []) or []:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            kind = str(item.get("kind", "inn")).lower()
            if not path or kind not in IDENTIFIER_KINDS:
                continue
            self.identifier_checks.append({
                "path": path,
                "kind": kind,
                "severity": str(item.get("severity", "error")).lower(),
            })

        # Банковский блок: БИК, счёт, УИН/РОСП, ОКТМО, КПП получателя.
        self.bank: Dict[str, Any] = {}
        for item in raw.get("bank", []) or []:
            if not isinstance(item, dict):
                continue
            for role in BANK_ROLES:
                value = item.get(role)
                if value:
                    self.bank[role] = str(value)

        # Поля, по которым определяется, что счёт открыт в Банке России (ГРКЦ):
        # ключевание 565-П к таким счетам неприменимо, и ложный error недопустим.
        self.bank_recipient_paths: List[str] = _as_paths(raw.get("bank_recipient_paths"))

        # Поля с номерами бланков и документов-оснований (8-9 цифр у «длинных»).
        self.doc_ref_fields: List[str] = _as_paths(raw.get("doc_ref_fields"))

        # Поля, где банковские реквизиты хранятся одной строкой, а не объектом.
        self.freeform_bank_fields: List[str] = _as_paths(raw.get("freeform_bank_fields"))

        # Правила сверки денежных сумм. Применяются по порядку, первое
        # подходящее выигрывает: наборы слагаемых у разных типов пересекаются.
        self.money_rules: List[Dict[str, Any]] = []
        for item in raw.get("money_rules", []) or []:
            if not isinstance(item, dict):
                continue
            totals = _as_paths(item.get("total"))
            if not totals:
                continue
            components: List[Tuple[str, List[str]]] = []
            for comp in item.get("components", []) or []:
                if isinstance(comp, (list, tuple)) and len(comp) == 2:
                    label, paths = str(comp[0]), _as_paths(comp[1])
                elif isinstance(comp, dict):
                    label, paths = str(comp.get("label", "")), _as_paths(comp.get("paths"))
                else:
                    continue
                if paths:
                    components.append((label, paths))
            if not components:
                continue
            self.money_rules.append({
                "code": str(item.get("code", "MONEY_DISCREPANCY")),
                "total": totals,
                "components": components,
                "min_components": int(item.get("min_components", 2)),
                "tolerance": float(item.get("tolerance", 0.05)),
                "severity": str(item.get("severity", "error")).lower(),
                "doc_hint": str(item.get("doc_hint", "")),
            })

        # Проверка процента удержания по ст. 99 229-ФЗ.
        self.deduction_limit: Optional[Dict[str, Any]] = None
        limit = raw.get("deduction_limit")
        if isinstance(limit, dict) and _as_paths(limit.get("path")):
            self.deduction_limit = {
                "path": _as_paths(limit.get("path"))[0],
                "basis_fields": _as_paths(limit.get("basis_fields")),
                "severity": str(limit.get("severity", "error")).lower(),
            }

        # Правила хронологии: поле A обязано предшествовать полю B.
        self.chronology_rules: List[Dict[str, Any]] = []
        for item in raw.get("chronology_rules", []) or []:
            if not isinstance(item, dict):
                continue
            before = _as_paths(item.get("before"))
            after = _as_paths(item.get("after"))
            if not before or not after:
                continue
            self.chronology_rules.append({
                "before": before,
                "after": after,
                "code": str(item.get("code", "CHRONOLOGY_INVERSION")),
                "message": str(item.get("message", "")),
                "severity": str(item.get("severity", "warning")).lower(),
                "require_before": bool(item.get("require_before", False)),
            })

        # Поля кросс-модального гейта: что подтверждать по исходному тексту.
        self.gate_fields: List[Dict[str, Any]] = []
        for item in raw.get("gate_fields", []) or []:
            if isinstance(item, dict) and item.get("path"):
                self.gate_fields.append({
                    "path": str(item["path"]),
                    "min_length": int(item.get("min_length", 5)),
                })
            elif isinstance(item, str) and item:
                self.gate_fields.append({"path": item, "min_length": 5})

        # Наименования сторон и органов для гейта.
        self.gate_names: List[str] = [p for p in _as_paths(raw.get("gate_names")) if p]
        self.gate_authorities: List[str] = [p for p in _as_paths(raw.get("gate_authorities")) if p]

    # ------------------------------------------------------------------
    def is_empty(self) -> bool:
        return not (
            self.parties or self.identifier_checks or self.bank or self.money_rules
            or self.deduction_limit or self.chronology_rules or self.gate_fields
            or self.gate_names or self.gate_authorities
        )

    def summary(self) -> Dict[str, int]:
        return {
            "parties": len(self.parties),
            "identifier_checks": len(self.identifier_checks),
            "bank_fields": len(self.bank),
            "money_rules": len(self.money_rules),
            "chronology_rules": len(self.chronology_rules),
            "gate_fields": len(self.gate_fields),
            "gate_names": len(self.gate_names),
            "gate_authorities": len(self.gate_authorities),
        }

    def validate(self, plugin_id: str) -> List[str]:
        """Возвращает список проблем конфигурации (пустой список = корректно)."""
        problems: List[str] = []
        for rule in self.money_rules:
            if rule["min_components"] < 1:
                problems.append(f"{plugin_id}: правило {rule['code']} с min_components < 1")
            if rule["min_components"] > len(rule["components"]):
                problems.append(
                    f"{plugin_id}: правило {rule['code']} требует {rule['min_components']} "
                    f"слагаемых, а объявлено {len(rule['components'])}"
                )
            if rule["severity"] not in ("error", "warning", "info"):
                problems.append(f"{plugin_id}: некорректный severity '{rule['severity']}'")
        for item in self.identifier_checks:
            if item["severity"] not in ("error", "warning", "info"):
                problems.append(f"{plugin_id}: некорректный severity '{item['severity']}'")
        return problems


_SPEC_CACHE: Dict[str, VerificationSpec] = {}


def load_verification_spec(plugin_folder: str) -> VerificationSpec:
    """
    Загружает verification.json из папки плагина.

    Отсутствие файла НЕ является ошибкой: возвращается пустая спецификация,
    и плагин проходит только общие проверки. Так добавляется новый тип
    документа — достаточно создать папку.
    """
    path = os.path.join(plugin_folder, VERIFICATION_FILENAME)
    if path in _SPEC_CACHE:
        return _SPEC_CACHE[path]
    if not os.path.exists(path):
        spec = VerificationSpec({})
    else:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                spec = VerificationSpec(json.load(fh))
        except Exception as e:
            logger.warning(f"verification.json плагина '{os.path.basename(plugin_folder)}' не прочитан: {e}")
            spec = VerificationSpec({})
    _SPEC_CACHE[path] = spec
    return spec


def clear_spec_cache() -> None:
    _SPEC_CACHE.clear()


def resolve_spec(doc_type: str) -> VerificationSpec:
    """
    Достаёт VerificationSpec типа документа из реестра плагинов.

    Для неизвестного типа возвращается пустая спецификация: выполняются только
    общие проверки отчёта, и никаких догадок о полях конкретного документа ядро
    не делает. Реестр импортируется лениво, чтобы spec.py оставался пригодным
    для использования без type_registry (например, в изолированных тестах).
    """
    try:
        from ..type_registry import get_registry

        plugin = get_registry().get(doc_type)
    except Exception as e:  # реестр может быть недоступен при частичном импорте
        logger.debug(f"Спецификация верификации для '{doc_type}' не получена: {e}")
        return VerificationSpec({})
    if plugin is None:
        return VerificationSpec({})
    return getattr(plugin, "verification_spec", None) or VerificationSpec({})


def get_nested_value(data: Any, path: str) -> Any:
    """
    Извлечение значения по точечному пути.

    Путь может перечислять альтернативы через "|": «court.act_date|act_date»
    означает «взять первое существующее». Это нужно, потому что один и тот же
    реквизит у разных типов лежит в разных местах (у исполнительного листа
    act_date на верхнем уровне, у приказа ФССП — внутри court).
    """
    if not path:
        return None
    for alternative in path.split("|"):
        alternative = alternative.strip()
        if not alternative:
            continue
        curr: Any = data
        ok = True
        for part in alternative.split("."):
            if isinstance(curr, dict) and part in curr:
                curr = curr[part]
            else:
                ok = False
                break
        if ok and curr is not None:
            return curr
    return None


def first_present(data: Any, paths: Sequence[str]) -> Tuple[Any, Optional[str]]:
    """Возвращает первое непустое значение и путь, по которому оно найдено."""
    for path in paths:
        value = get_nested_value(data, path)
        if value is not None and str(value).strip() not in ("", "None", "null", "nan"):
            return value, path
    return None, None
