"""
Unified Zero-Trust Auditor for ScanReader.
Applies mathematical reconciliations, statutory limits, checksum algorithms,
chronology validation, and cross-modal hallucination gating.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from ..core.utils import get_logger
from .checksums import clean_digits, validate_bik, validate_inn, validate_ogrn, validate_snils, validate_bank_account
from .chronology import parse_flexible_date
from .hallucination_gate import audit_cross_modal_consistency
from .math_verifier import verify_deduction_percentage, parse_percentage_value
from .spec import VerificationSpec, get_nested_value, resolve_spec
from .status import VerificationIssue, VerificationReport, VerificationStatus

logger = get_logger("verifier.auditor")


def _as_dict(val: Any) -> Dict[str, Any]:
    """Безопасный доступ к вложенным структурам: возвращает словарь или пустой словарь."""
    return val if isinstance(val, dict) else {}


_EMPTY_STRINGS = ("", "none", "null", "nan")

#: Суффиксы имён полей, по которым объект считается датой. Проверка формата
#: применяется только к ним, иначе любая строка вроде «ФС 002000001» трактовалась
#: бы как нечитаемая дата.
_DATE_FIELD_SUFFIXES = (
    "date", "date_from", "date_to", "valid_until", "term_start", "term_end",
    "period_start", "period_end", "signed_date", "deadline_date", "ip_date",
)


def _looks_like_date(path: str) -> bool:
    leaf = path.rsplit(".", 1)[-1].lower()
    return any(leaf == suffix or leaf.endswith("_" + suffix) for suffix in _DATE_FIELD_SUFFIXES)


def _first_alternative(data: Any, paths: Sequence[str]) -> Tuple[Any, Optional[str]]:
    """Первое непустое значение из списка альтернативных путей + сам путь."""
    for path in paths:
        value = get_nested_value(data, path)
        if value is not None and str(value).strip().lower() not in _EMPTY_STRINGS:
            return value, path
    return None, None


def _iter_leaves(data: Any, prefix: str = "") -> Iterator[Tuple[str, Any]]:
    """Обход всех скалярных значений с построением точечных путей."""
    if isinstance(data, dict):
        for key, value in data.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield from _iter_leaves(value, child)
    elif isinstance(data, (list, tuple)):
        for index, value in enumerate(data):
            child = f"{prefix}.{index}" if prefix else str(index)
            yield from _iter_leaves(value, child)
    else:
        yield prefix, data


def _first_present(source: Any, *keys: str) -> Any:
    """
    Возвращает значение ПЕРВОГО ключа, который реально присутствует и не является
    пустым представлением отсутствия.

    Нужен потому, что `d.get("a") or d.get("b")` отбрасывает легитимные нули:
    0.0, 0 и "0" — falsy. Долг, списанный полностью, нулевая госпошлина и нулевой
    итог молча выпадали из сверки, а отчёт утверждал, что сверка не выполнялась.

    Пустыми считаются только None и строковые "empty-like" заглушки, пришедшие от
    VLM ("", "None", "null"); числа (включая 0) и непустые строки значимы.
    """
    d = _as_dict(source)
    for key in keys:
        val = d.get(key)
        if val is None:
            continue
        if isinstance(val, str) and val.strip().lower() in _EMPTY_STRINGS:
            continue
        return val
    return None


def _to_number(val: Any) -> Optional[float]:
    """
    Приведение денежного значения к float с учётом обоих форматов разделителей.

    VLM и форматы 1С отдают суммы как «1 234,56», «1234.56», «1,234.56» и как
    числа. Прежняя замена str.replace(",", ".") ломала «1,234.56» (две точки)
    и молча превращала значение в ошибку парсинга.
    """
    if val is None or isinstance(val, bool):
        return None
    if isinstance(val, (int, float)):
        return float(val)
    s = re.sub(r"[^\d,.\-]", "", str(val))
    if not s:
        return None
    has_comma, has_dot = "," in s, "." in s
    if has_comma and has_dot:
        # Разделитель дробной части — последний из присутствующих
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_comma:
        # Одна запятая: дробная часть, если после неё 1-2 цифры, иначе разделитель тысяч
        tail = s.split(",")[-1]
        s = s.replace(",", ".") if len(tail) in (1, 2) else s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


        return


class ZeroTrustAuditor:
    """
    Independent Zero-Trust Verification Engine.
    Never blindly trusts VLM or external model outputs.
    """

    @classmethod
    def audit_document(
        cls,
        data: Dict[str, Any],
        doc_type: str = "unknown",
        raw_ocr_text: Optional[str] = None,
        extraction_method: str = "vlm",
        scan_dpi: Optional[float] = None,
        gate_source: Optional[str] = None,
        gate_expected: bool = False,
        spec: Optional["VerificationSpec"] = None,
    ) -> VerificationReport:
        """
        :param gate_expected: True, если исходный документ передан и эталонный текст
            ОБЯЗАН быть доступен (конвейер facade). False — если аудит идёт по
            готовому JSON без документа (CLI `verify`, MCP `verify_legal_data`):
            отсутствие эталона там штатно и не является отказом инфраструктуры.
        :param spec: декларация верификации типа документа (verification.json).
            Если не передана, берётся из реестра плагинов; для неизвестного типа
            применяется пустая спецификация, и выполняются только общие проверки.
        """
        issues: List[VerificationIssue] = []
        details: Dict[str, Any] = {}

        if not isinstance(data, dict):
            return VerificationReport(
                status=VerificationStatus.DISCREPANCY_DETECTED,
                is_valid=False,
                issues=[VerificationIssue("error", "INVALID_PAYLOAD", f"Expected dictionary payload, got {type(data).__name__}")],
                details={"raw_payload": str(data)},
            )

        if doc_type in ("unknown", "unsupported"):
            return VerificationReport(
                status=VerificationStatus.REJECTED_UNSUPPORTED,
                is_valid=False,
                issues=[VerificationIssue("error", "DOC_UNKNOWN", "Document type is unsupported or unknown")],
            )

        if spec is None:
            spec = resolve_spec(doc_type)
        details["verification_spec"] = spec.summary()
        details["verification_spec_declared"] = not spec.is_empty()

        # 1. Алгоритмические проверки (контрольные суммы ФНС/ПФР/ЦБ)
        cls._audit_identifiers(data, spec, issues, details)

        # 2. Финансовая сверка
        cls._audit_finances(data, spec, issues, details)

        # 3. Хронология
        cls._audit_dates(data, spec, issues, details)

        # 4. Cross-Modal Hallucination Gate
        # C-01: неподтверждённые VLM-сущности признаются ОШИБКОЙ. При severity="warning"
        # документ с выдуманными ИНН/номером дела/ФИО получал zero_trust_verified
        # и is_valid=True — статус прямо противоречил содержимому отчёта.
        if raw_ocr_text:
            discrepancies = audit_cross_modal_consistency(data, raw_ocr_text, spec)
            if discrepancies:
                for disc in discrepancies:
                    issues.append(
                        VerificationIssue(
                            severity="error",
                            code="HALLUCINATION_RISK",
                            message=disc["reason"],
                            field_name=disc["field"],
                        )
                    )
                details["hallucination_discrepancies"] = discrepancies
            details["gate_executed"] = True
            details["gate_source"] = gate_source or "text_layer"
            if details["gate_source"] == "vlm_transcription":
                # Эталон получен той же моделью, что и извлечение, поэтому ошибки
                # распознавания неотделимы от ошибок извлечения: это НЕ независимая
                # сверка. Гейт полезен (ловит правки и склейки реквизитов), но его
                # результат нельзя предъявлять как подтверждение исходным текстом.
                details["gate_independent"] = False
                issues.append(
                    VerificationIssue(
                        "warning",
                        "GATE_REFERENCE_FROM_VLM",
                        "Эталон гейта получен VLM-транскрипцией, а не независимым OCR: "
                        "ошибки распознавания неотделимы от ошибок извлечения. "
                        "Для независимой сверки установите extra [ocr].",
                        "gate",
                    )
                )
            else:
                details["gate_independent"] = True
        else:
            # Отсутствие эталона фиксируется явно: раньше молчаливый пропуск гейта давал
            # zero_trust_verified, хотя кросс-модальная сверка не выполнялась вовсе.
            details["gate_executed"] = False
            details["gate_source"] = None
            details["gate_independent"] = False
            details["gate_expected"] = gate_expected
            if gate_expected:
                issues.append(
                    VerificationIssue(
                        "warning",
                        "GATE_NOT_EXECUTED",
                        "Кросс-модальный гейт не выполнен: эталонный текстовый слой/транскрипция "
                        "недоступен. Реквизиты не подтверждены исходным текстом документа.",
                        "gate",
                    )
                )

        # 5. Scan Quality Check (M-06): низкое разрешение скана -> OCR_LOW_CONFIDENCE
        if scan_dpi is not None and scan_dpi > 0 and scan_dpi < 150:
            issues.append(
                VerificationIssue(
                    "warning",
                    "OCR_LOW_CONFIDENCE",
                    f"Разрешение скана ({int(scan_dpi)} DPI) ниже порога 150 DPI — вероятны ошибки распознавания",
                    "scan",
                )
            )
            details["scan_dpi"] = int(scan_dpi)
            details["scan_low_quality"] = True

        # Determine Final Canonical Status
        has_error = any(i.severity == "error" for i in issues)
        gate_ok = details.get("gate_executed") or not details.get("gate_expected")

        # C-02/C-03: zero_trust_verified больше не выдаётся «за просто наличие числа».
        # Требуется, чтобы контрольные суммы реально прошли И сверка денег реально
        # сравнивала два и более числа. Кросс-модальный гейт обязателен только когда
        # исходный документ передан (gate_expected) — иначе его отсутствие штатно.
        if has_error:
            status = VerificationStatus.DISCREPANCY_DETECTED
        elif details.get("scan_low_quality"):
            status = VerificationStatus.OCR_LOW_CONFIDENCE
        elif extraction_method == "regex_fallback":
            status = VerificationStatus.HEURISTIC_FALLBACK
        elif not gate_ok:
            status = VerificationStatus.GATE_NOT_EXECUTED
        elif details.get("checksums_verified_ok", 0) > 0 and details.get("math_verified_ok"):
            status = VerificationStatus.ZERO_TRUST_VERIFIED
        elif details.get("checksums_checked", 0) > 0 or details.get("math_checked"):
            status = VerificationStatus.PARTIALLY_VERIFIED
        else:
            status = VerificationStatus.VLM_UNVERIFIED

        return VerificationReport(
            status=status,
            is_valid=not has_error,
            issues=issues,
            details=details,
        )


    # ==================================================================
    # ИНТЕРПРЕТАТОРЫ ДЕКЛАРАЦИИ ВЕРИФИКАЦИИ
    # Ниже нет ни одного имени поля конкретного типа документа: всё
    # определяется VerificationSpec, загруженной из verification.json.
    # ==================================================================

    @classmethod
    def _audit_identifiers(
        cls,
        data: Dict[str, Any],
        spec: VerificationSpec,
        issues: List[VerificationIssue],
        details: Dict[str, Any],
    ) -> None:
        """
        Контрольные суммы ФНС/ПФР/ЦБ по реквизитам, объявленным в спецификации.

        Раньше перечисления ИНН, СНИЛС и ОГРН были захардкожены (20 путей к ИНН,
        3 к СНИЛС, 5 к ОГРН), из-за чего добавление типа документа требовало
        правки ядра в нарушение Правила 3 AGENTS.md.
        """
        count = 0
        passed = 0

        def _bump(ok: bool) -> None:
            nonlocal count, passed
            count += 1
            if ok:
                passed += 1

        checkers = {
            "inn": (validate_inn, "INVALID_INN"),
            "snils": (validate_snils, "INVALID_SNILS"),
            "ogrn": (validate_ogrn, "INVALID_OGRN"),
            "ogrnip": (validate_ogrn, "INVALID_OGRN"),
            "bik": (validate_bik, "INVALID_BIK"),
        }

        targets: List[Tuple[str, str]] = []
        for party in spec.parties:
            for field in party["ids"]:
                targets.append((f"{party['path']}.{field}", field))
        for item in spec.identifier_checks:
            targets.append((item["path"], item["kind"]))

        seen: set = set()
        for path, kind in targets:
            if path in seen:
                continue
            seen.add(path)
            value = get_nested_value(data, path)
            if not value or not isinstance(value, str) or len(value.strip()) < 9:
                continue
            checker, code = checkers.get(kind, (validate_inn, "INVALID_INN"))
            ok, msg = checker(value)
            _bump(ok)
            if not ok:
                issues.append(VerificationIssue("error", code, msg, path))

        bank = spec.bank
        bik_value = get_nested_value(data, bank.get("bik", "")) if bank.get("bik") else None
        # Некоторые типы несут банковские реквизиты строкой, а не объектом.
        # Пути таких полей приходят из спецификации плагина.
        if not bik_value:
            for free_path in spec.freeform_bank_fields:
                blob = get_nested_value(data, free_path)
                if isinstance(blob, str):
                    import re as _re

                    match = _re.search(r"\b0[14]\d{7}\b", blob)
                    if match:
                        bik_value = match.group(0)
                        break

        if bik_value and isinstance(bik_value, str) and len(bik_value.strip()) >= 8:
            ok, msg = validate_bik(bik_value)
            _bump(ok)
            if not ok:
                issues.append(VerificationIssue("error", "INVALID_BIK", msg, bank.get("bik", "bik")))

        account_path = bank.get("account")
        if account_path and bik_value:
            account = get_nested_value(data, account_path)
            if account:
                ok_acc, msg_acc = validate_bank_account(str(account), str(bik_value))
                _bump(ok_acc)
                if not ok_acc:
                    # Счета Банка России (ГРКЦ) не подчиняются ключеванию 565-П:
                    # ложный error недопустим.
                    haystack = " ".join(
                        str(get_nested_value(data, p) or "")
                        for p in ([bank.get("recipient_inn", "")] + spec.bank_recipient_paths)
                        if p
                    ).lower()
                    if "банк россии" in haystack or "гркц" in haystack:
                        issues.append(
                            VerificationIssue(
                                "warning",
                                "BANK_ACCOUNT_UNVERIFIED",
                                f"Счет получателя в Банке России (ГРКЦ): стандартная проверка "
                                f"ключа неприменима ({msg_acc})",
                                account_path,
                            )
                        )
                    else:
                        issues.append(
                            VerificationIssue("error", "INVALID_BANK_ACCOUNT", msg_acc, account_path)
                        )

        # Структурные проверки реквизитов, не имеющих контрольной суммы
        if bank.get("uin") and bank.get("rosp_code"):
            uin_digits = clean_digits(str(get_nested_value(data, bank["uin"]) or ""))
            rosp_digits = clean_digits(str(get_nested_value(data, bank["rosp_code"]) or ""))
            if len(uin_digits) >= 20 and rosp_digits:
                _bump(rosp_digits in uin_digits)
                if rosp_digits not in uin_digits:
                    issues.append(
                        VerificationIssue(
                            "warning",
                            "UIN_ROSP_MISMATCH",
                            f"УИН не содержит ведомственный код РОСП ({rosp_digits})",
                            bank["uin"],
                        )
                    )

        if bank.get("oktmo"):
            oktmo = clean_digits(str(get_nested_value(data, bank["oktmo"]) or ""))
            if oktmo and len(oktmo) not in (8, 11):
                issues.append(
                    VerificationIssue(
                        "warning",
                        "INVALID_OKTMO",
                        f"ОКТМО должен содержать 8 или 11 цифр, получено {len(oktmo)}",
                        bank["oktmo"],
                    )
                )

        if bank.get("kpp"):
            kpp = clean_digits(str(get_nested_value(data, bank["kpp"]) or ""))
            if kpp and len(kpp) != 9:
                issues.append(
                    VerificationIssue(
                        "warning",
                        "INVALID_KPP",
                        f"КПП должен содержать 9 цифр, получено {len(kpp)}",
                        bank["kpp"],
                    )
                )

        # Номера бланков и документов-оснований: 8-9 цифр у «длинных» номеров
        for path in spec.doc_ref_fields:
            digits = clean_digits(str(get_nested_value(data, path) or ""))
            if len(digits) >= 7 and len(digits) not in (8, 9):
                issues.append(
                    VerificationIssue(
                        "warning",
                        "INVALID_DOC_REF_NUMBER",
                        f"Номер документа-основания имеет нетипичную длину цифр "
                        f"({len(digits)}, ожидается 8-9)",
                        path,
                    )
                )

        details["checksums_checked"] = count
        details["checksums_verified_ok"] = passed

    @classmethod
    def _audit_finances(
        cls,
        data: Dict[str, Any],
        spec: VerificationSpec,
        issues: List[VerificationIssue],
        details: Dict[str, Any],
    ) -> None:
        """
        Сверка денежных сумм по правилам, объявленным в спецификации.

        C-04: раньше правила для исполнительных листов, приказов ФССП и актов
        приемки отсутствовали, и реальные числа 157611.62 + 7004.39 + 60000.00 и
        подставные 999999 давали одинаковый zero_trust_verified с нулём замечаний.
        """
        for rule in spec.money_rules:
            if cls._apply_money_rule(data, rule, issues, details):
                # Правило сработало: наборы слагаемых у типов пересекаются,
                # и второе правило породило бы дублирующую ошибку.
                break

        limit = spec.deduction_limit
        if limit:
            value = get_nested_value(data, limit["path"])
            if value is not None and str(value).strip().lower() not in _EMPTY_STRINGS:
                basis = " ".join(
                    str(get_nested_value(data, p) or "")
                    for p in (limit["basis_fields"] or ["claim_subject"])
                ).lower()
                has_basis = any(
                    marker in basis
                    for marker in ("алимент", "несовершеннолетн", "ребен", "содержание", "вред")
                )
                ok, msg = verify_deduction_percentage(str(value), has_alimony_or_harm=has_basis)
                if not ok:
                    pct = parse_percentage_value(str(value))
                    severity = "error" if (pct is not None and pct > 70.0) else "warning"
                    if limit["severity"] == "error":
                        severity = "error" if severity == "error" else severity
                    issues.append(
                        VerificationIssue(severity, "STATUTORY_LIMIT_ALERT", msg, limit["path"])
                    )

    @classmethod
    def _apply_money_rule(
        cls,
        data: Dict[str, Any],
        rule: Dict[str, Any],
        issues: List[VerificationIssue],
        details: Dict[str, Any],
    ) -> bool:
        """Применяет одно правило сверки. Возвращает True, если оно сработало."""
        total_raw, total_path = _first_alternative(data, rule["total"])
        if total_raw is None:
            return False
        total_val = _to_number(total_raw)
        if total_val is None:
            issues.append(
                VerificationIssue(
                    "error",
                    rule["code"],
                    f"Нечисловой итог ({rule.get('doc_hint') or 'финансовый блок'}): {total_raw!r}",
                    total_path or "finances",
                )
            )
            return True

        present: List[Tuple[str, float]] = []
        unparsable: List[str] = []
        for label, paths in rule["components"]:
            raw, _path = _first_alternative(data, paths)
            if raw is None:
                continue
            num = _to_number(raw)
            if num is None:
                unparsable.append(f"{label}={raw!r}")
            else:
                present.append((label, num))

        severity = rule.get("severity", "error")
        if unparsable:
            details["math_checked"] = True
            details["math_verified_ok"] = False
            issues.append(
                VerificationIssue(
                    severity,
                    rule["code"],
                    f"Нечисловые слагаемые ({rule.get('doc_hint') or 'финансовый блок'}): "
                    f"{', '.join(unparsable)}",
                    total_path or "finances",
                )
            )
            return True

        if len(present) < rule["min_components"]:
            return False

        details["math_checked"] = True
        expected = round(sum(v for _, v in present), 2)
        tolerance = rule.get("tolerance", 0.05)
        details["math_verified_ok"] = abs(expected - total_val) <= tolerance
        if not details["math_verified_ok"]:
            breakdown = " + ".join(f"{label} ({v:.2f})" for label, v in present)
            hint = f" [{rule['doc_hint']}]" if rule.get("doc_hint") else ""
            issues.append(
                VerificationIssue(
                    severity,
                    rule["code"],
                    f"{breakdown} = {expected:.2f}, что не совпадает с итогом {total_val:.2f} "
                    f"(расхождение {abs(expected - total_val):.2f} руб.){hint}",
                    total_path or "finances",
                )
            )
        return True

    @classmethod
    def _audit_dates(
        cls,
        data: Dict[str, Any],
        spec: VerificationSpec,
        issues: List[VerificationIssue],
        details: Dict[str, Any],
    ) -> None:
        """
        Хронология по правилам спецификации плюс контроль нечитаемых дат.

        Правила сравнения дат раньше были захардкожены по типам документов
        (доверенность, договор, акт приемки, приказ по кадрам) прямо в ядре.
        """
        unparseable: List[str] = []
        for path, value in _iter_leaves(data):
            if not isinstance(value, str) or not value.strip():
                continue
            if _looks_like_date(path) and parse_flexible_date(value) is None:
                unparseable.append(path)
                issues.append(
                    VerificationIssue(
                        "warning",
                        "UNPARSEABLE_DATE",
                        f"Поле '{path}' содержит дату в нераспознаваемом формате: '{value}'",
                        path,
                    )
                )
        if unparseable:
            details["unparseable_dates"] = unparseable

        for rule in spec.chronology_rules:
            before_raw, before_path = _first_alternative(data, rule["before"])
            after_raw, after_path = _first_alternative(data, rule["after"])
            if before_raw is None or after_raw is None:
                if rule.get("require_before") and before_raw is None:
                    details.setdefault("chronology_incomplete", []).append(before_path or "")
                continue
            before = parse_flexible_date(str(before_raw))
            after = parse_flexible_date(str(after_raw))
            if before is None or after is None:
                continue
            if after < before:
                message = rule["message"] or (
                    f"Дата '{after_path}' ({after_raw}) предшествует '{before_path}' ({before_raw})"
                )
                issues.append(
                    VerificationIssue(rule.get("severity", "warning"), rule["code"], message, after_path or "")
                )
