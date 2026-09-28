"""
Unified Zero-Trust Auditor for ScanReader.
Applies mathematical reconciliations, statutory limits, checksum algorithms,
chronology validation, and cross-modal hallucination gating.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from .checksums import clean_digits, validate_bik, validate_inn, validate_ogrn, validate_snils, validate_bank_account
from .chronology import verify_chronology, parse_flexible_date
from .hallucination_gate import audit_cross_modal_consistency
from .math_verifier import verify_amounts_reconciliation, verify_deduction_percentage, parse_percentage_value
from .status import VerificationIssue, VerificationReport, VerificationStatus


def _as_dict(val: Any) -> Dict[str, Any]:
    """Безопасный доступ к вложенным структурам: возвращает словарь или пустой словарь."""
    return val if isinstance(val, dict) else {}


_EMPTY_STRINGS = ("", "none", "null", "nan")


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


# C-04: декларативные правила сверки денежных сумм.
# Слагаемые и итог описаны данными, а не ветвлением по коду: правило применяется к
# любому документу, чей finances содержит соответствующие поля. Это же описание
# переносится в doc_types/<id>/verification.json на шаге изоляции плагинов,
# поэтому здесь не появляется новой захардкоженной логики.
#
# ПОРЯДОК ВАЖЕН: применяется ПЕРВОЕ подходящее правило, дальнейшие не
# рассматриваются. Правила упорядочены от специфичных к общим, потому что наборы
# слагаемых пересекаются: приказ ФССП содержит и main_debt_rub, и court_costs_rub,
# и потому удовлетворяет и WRIT, и ENFORCEMENT. Без приоритета один и тот же
# расхожий итог порождал бы две одинаковые ошибки.
#
# Правило срабатывает, только если присутствуют ИТОГ и не менее min_components
# СЛАГАЕМЫХ. Пропущенные слагаемые считаются нулём — так ведёт себя юридически
# корректный документ, где, например, «прочее» не заполнено.
_RECONCILE_RULES: Tuple[Dict[str, Any], ...] = (
    {
        "code": "WRIT_MATH_DISCREPANCY",
        "total": ("total_rub",),
        "field": "finances.total_rub",
        "doc_hint": "исполнительный лист / приказ ФССП",
        # Покрывает и executive_documents (main_debt + interest_penalty + court_fee
        # + other), и enforcement_orders (main_debt + court_costs): набор слагаемых
        # приказов ФССП строго подмножество этого, поэтому отдельное правило для них
        # было бы недостижимым - WRIT срабатывал бы первым.
        "components": (
            ("основной долг", ("main_debt_rub",)),
            ("проценты и неустойка", ("interest_penalty_rub", "penalty_rub")),
            ("судебные расходы", ("court_fee_rub", "court_costs_rub")),
            ("прочее", ("other_rub",)),
        ),
        "min_components": 2,
    },
    {
        "code": "UPD_MATH_DISCREPANCY",
        "total": ("total_rub",),
        "field": "finances.total_rub",
        "doc_hint": "УПД / счет-фактура",
        "components": (
            ("сумма без НДС", ("total_rub_no_vat",)),
            ("НДС", ("total_vat_rub",)),
        ),
        "min_components": 2,
    },
    {
        "code": "ACCEPTANCE_MATH_DISCREPANCY",
        "total": ("total_rub",),
        "field": "finances.total_rub",
        "doc_hint": "акт приемки",
        "components": (
            ("стоимость без НДС", ("amount_no_vat_rub",)),
            ("НДС", ("vat_amount_rub",)),
        ),
        "min_components": 2,
    },
    {
        "code": "CLAIM_MATH_DISCREPANCY",
        "total": ("total_claim_rub",),
        "field": "finances.total_claim_rub",
        "doc_hint": "досудебная претензия",
        "components": (
            ("основной долг", ("principal_debt_rub",)),
            ("неустойка", ("penalty_rub",)),
            ("проценты по ст. 395 ГК", ("interest_rub",)),
        ),
        "min_components": 1,
    },
)

_TOLERANCE_RUB = 0.05


def _apply_reconcile_rules(
    fin: Dict[str, Any],
    issues: List[VerificationIssue],
    details: Dict[str, Any],
) -> None:
    """Применяет _RECONCILE_RULES к блоку finances."""
    for rule in _RECONCILE_RULES:
        total_raw = _first_present(fin, *rule["total"])
        if total_raw is None:
            continue
        total_val = _to_number(total_raw)
        if total_val is None:
            issues.append(
                VerificationIssue(
                    "error",
                    rule["code"],
                    f"Нечисловой итог в финансовом блоке ({rule['doc_hint']}): {total_raw!r}",
                    rule["field"],
                )
            )
            continue

        present: List[Tuple[str, float]] = []
        unparsable: List[str] = []
        for label, keys in rule["components"]:
            raw = _first_present(fin, *keys)
            if raw is None:
                continue
            num = _to_number(raw)
            if num is None:
                unparsable.append(f"{label}={raw!r}")
            else:
                present.append((label, num))

        if unparsable:
            details["math_checked"] = True
            details["math_verified_ok"] = False
            issues.append(
                VerificationIssue(
                    "error",
                    rule["code"],
                    f"Нечисловые слагаемые ({rule['doc_hint']}): {', '.join(unparsable)}",
                    rule["field"],
                )
            )
            return

        if len(present) < rule["min_components"]:
            continue

        details["math_checked"] = True
        expected = round(sum(v for _, v in present), 2)
        details["math_verified_ok"] = abs(expected - total_val) <= _TOLERANCE_RUB
        if not details["math_verified_ok"]:
            breakdown = " + ".join(f"{label} ({v:.2f})" for label, v in present)
            issues.append(
                VerificationIssue(
                    "error",
                    rule["code"],
                    f"{breakdown} = {expected:.2f}, что не совпадает с итогом {total_val:.2f} "
                    f"(расхождение {abs(expected - total_val):.2f} руб.) [{rule['doc_hint']}]",
                    rule["field"],
                )
            )
        # Правило сработало — дальнейшие не применяются (см. порядок _RECONCILE_RULES)
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
    ) -> VerificationReport:
        """
        :param gate_expected: True, если исходный документ передан и эталонный текст
            ОБЯЗАН быть доступен (конвейер facade). False — если аудит идёт по
            готовому JSON без документа (CLI `verify`, MCP `verify_legal_data`):
            отсутствие эталона там штатно и не является отказом инфраструктуры.
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

        # 1. Algorithmic Checksums
        cls._audit_identifiers(data, issues, details)

        # 2. Financial & Math Reconciliation
        cls._audit_finances(data, issues, details)

        # 3. Chronology
        cls._audit_dates(data, issues, details)

        # 4. Cross-Modal Hallucination Gate
        # C-01: неподтверждённые VLM-сущности признаются ОШИБКОЙ. При severity="warning"
        # документ с выдуманными ИНН/номером дела/ФИО получал zero_trust_verified
        # и is_valid=True — статус прямо противоречил содержимому отчёта.
        if raw_ocr_text:
            discrepancies = audit_cross_modal_consistency(data, raw_ocr_text)
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
        else:
            # Отсутствие эталона фиксируется явно: раньше молчаливый пропуск гейта давал
            # zero_trust_verified, хотя кросс-модальная сверка не выполнялась вовсе.
            details["gate_executed"] = False
            details["gate_source"] = None
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

    @classmethod
    def _audit_identifiers(cls, data: Dict[str, Any], issues: List[VerificationIssue], details: Dict[str, Any]) -> None:
        count = 0
        passed = 0

        def _bump(ok: bool) -> None:
            nonlocal count, passed
            count += 1
            if ok:
                passed += 1

        # INN check helper
        def _check_inn(val: Any, field_name: str):
            if val and isinstance(val, str) and len(val.strip()) >= 9:
                ok, msg = validate_inn(val)
                _bump(ok)
                if not ok:
                    issues.append(VerificationIssue("error", "INVALID_INN", msg, field_name))

        # Check court & FSSP identifiers
        _check_inn(_as_dict(data.get("debtor")).get("inn"), "debtor.inn")
        _check_inn(_as_dict(data.get("claimant")).get("inn"), "claimant.inn")
        _check_inn(_as_dict(data.get("payment_details")).get("recipient_inn"), "payment_details.recipient_inn")

        # Check commercial contracts
        _check_inn(_as_dict(data.get("party_one")).get("inn"), "party_one.inn")
        _check_inn(_as_dict(data.get("party_two")).get("inn"), "party_two.inn")

        # Check invoices & UPD
        _check_inn(_as_dict(data.get("seller")).get("inn"), "seller.inn")
        _check_inn(_as_dict(data.get("buyer")).get("inn"), "buyer.inn")

        # Check acceptance certificates
        _check_inn(_as_dict(data.get("customer")).get("inn"), "customer.inn")
        _check_inn(_as_dict(data.get("contractor")).get("inn"), "contractor.inn")

        # Check powers of attorney
        _check_inn(_as_dict(data.get("principal")).get("inn"), "principal.inn")
        _check_inn(_as_dict(data.get("agent")).get("inn"), "agent.inn")

        # Check legal claims
        _check_inn(_as_dict(data.get("sender")).get("inn"), "sender.inn")
        _check_inn(_as_dict(data.get("recipient")).get("inn"), "recipient.inn")

        # Check HR orders
        _check_inn(data.get("organization_inn"), "organization_inn")
        _check_inn(_as_dict(data.get("employee")).get("inn"), "employee.inn")

        # SNILS check helper
        def _check_snils(val: Any, field_name: str):
            if val and isinstance(val, str) and len(val.strip()) >= 9:
                ok, msg = validate_snils(val)
                _bump(ok)
                if not ok:
                    issues.append(VerificationIssue("error", "INVALID_SNILS", msg, field_name))

        _check_snils(_as_dict(data.get("debtor")).get("snils"), "debtor.snils")
        _check_snils(_as_dict(data.get("agent")).get("snils"), "agent.snils")
        _check_snils(_as_dict(data.get("employee")).get("snils"), "employee.snils")

        # OGRN check helper
        def _check_ogrn(val: Any, field_name: str):
            if val and isinstance(val, str) and len(val.strip()) >= 12:
                ok, msg = validate_ogrn(val)
                _bump(ok)
                if not ok:
                    issues.append(VerificationIssue("error", "INVALID_OGRN", msg, field_name))

        _check_ogrn(_as_dict(data.get("claimant")).get("ogrn"), "claimant.ogrn")
        _check_ogrn(_as_dict(data.get("debtor")).get("ogrn"), "debtor.ogrn")
        _check_ogrn(_as_dict(data.get("party_one")).get("ogrn"), "party_one.ogrn")
        _check_ogrn(_as_dict(data.get("party_two")).get("ogrn"), "party_two.ogrn")
        _check_ogrn(_as_dict(data.get("principal")).get("ogrn"), "principal.ogrn")

        # BIK check
        pay_details = _as_dict(data.get("payment_details"))
        bik = pay_details.get("bik")
        if not bik and isinstance(data.get("bank_requisites"), str):
            m_bik = re.search(r"\b04\d{7}\b", data["bank_requisites"])
            if m_bik:
                bik = m_bik.group(0)

        if bik and isinstance(bik, str) and len(bik.strip()) >= 8:
            ok, msg = validate_bik(bik)
            _bump(ok)
            if not ok:
                issues.append(VerificationIssue("error", "INVALID_BIK", msg, "bik"))

        # Bank account check (C-15: validate_bank_account)
        account = _first_present(pay_details, "payment_account", "account")
        if account and bik:
            ok_acc, msg_acc = validate_bank_account(str(account), str(bik))
            _bump(ok_acc)
            if not ok_acc:
                # Счета, открытые в самом Банке России (ГРКЦ), не подчиняются
                # стандартному ключеванию 565-П: ложный error недопустим.
                recipient_str = str(
                    pay_details.get("recipient") or data.get("recipient") or data.get("bank_requisites") or ""
                ).lower()
                is_bank_of_russia = ("банк россии" in recipient_str) or ("гркц" in recipient_str)
                if is_bank_of_russia:
                    issues.append(
                        VerificationIssue(
                            "warning",
                            "BANK_ACCOUNT_UNVERIFIED",
                            f"Счет получателя в Банке России (ГРКЦ): стандартная проверка ключа неприменима ({msg_acc})",
                            "payment_details.payment_account",
                        )
                    )
                else:
                    issues.append(
                        VerificationIssue("error", "INVALID_BANK_ACCOUNT", msg_acc, "payment_details.payment_account")
                    )

        # S-12: перекрестные форматные проверки реквизитов (мусор от VLM не проходит молча)
        uin_digits = clean_digits(pay_details.get("uin") or "")
        rosp_digits = clean_digits(pay_details.get("rosp_code") or data.get("rosp_code") or "")
        if len(uin_digits) >= 20 and rosp_digits:
            _bump(rosp_digits in uin_digits)
            if rosp_digits not in uin_digits:
                issues.append(
                    VerificationIssue(
                        "warning",
                        "UIN_ROSP_MISMATCH",
                        f"УИН ({pay_details.get('uin')}) не содержит ведомственный код РОСП ({pay_details.get('rosp_code')})",
                        "payment_details.uin",
                    )
                )

        oktmo_digits = clean_digits(pay_details.get("oktmo") or "")
        if oktmo_digits and len(oktmo_digits) not in (8, 11):
            issues.append(
                VerificationIssue(
                    "warning",
                    "INVALID_OKTMO",
                    f"ОКТМО должен содержать 8 или 11 цифр, получено {len(oktmo_digits)}: '{pay_details.get('oktmo')}'",
                    "payment_details.oktmo",
                )
            )

        kpp_digits = clean_digits(pay_details.get("recipient_kpp") or "")
        if kpp_digits and len(kpp_digits) != 9:
            issues.append(
                VerificationIssue(
                    "warning",
                    "INVALID_KPP",
                    f"КПП должен содержать 9 цифр, получено {len(kpp_digits)}: '{pay_details.get('recipient_kpp')}'",
                    "payment_details.recipient_kpp",
                )
            )

        # Номер бланка ИЛ (ФС/ВС/АС + 8-9 цифр) и номер документа-основания
        for ref_val, field_name in (
            (data.get("blank_number"), "blank_number"),
            (data.get("base_doc_number"), "base_doc_number"),
        ):
            ref_digits = clean_digits(ref_val or "")
            # Короткие номера судебных приказов («2-967») пропускаем: проверяем только
            # «длинные» номера, где ожидаем 8-9 цифр бланка ИЛ
            if len(ref_digits) >= 7 and len(ref_digits) not in (8, 9):
                issues.append(
                    VerificationIssue(
                        "warning",
                        "INVALID_DOC_REF_NUMBER",
                        f"Номер документа-основания '{ref_val}' имеет нетипичную длину цифр ({len(ref_digits)}, ожидается 8-9)",
                        field_name,
                    )
                )

        details["checksums_checked"] = count
        details["checksums_verified_ok"] = passed

    @classmethod
    def _audit_finances(cls, data: Dict[str, Any], issues: List[VerificationIssue], details: Dict[str, Any]) -> None:
        # C-05: контейнер выбирается по типу, а не по truthiness — пустой словарь
        # из VLM не должен переключать выбор на другой источник.
        fin_src = data.get("finances")
        if not isinstance(fin_src, dict):
            fin_src = data.get("financials")
        fin = _as_dict(fin_src)
        debt = _first_present(fin, "debt_amount_rub", "principal_rub")
        fee = _first_present(fin, "fee_penalty_rub", "penalty_rub")
        total = _first_present(fin, "total_deduction_rub", "total_rub")

        # 1. Court amounts reconciliation.
        # math_verified_ok выставляется ТОЛЬКО когда сверка действительно сравнивала
        # два и более числа. Раньше math_checked=True при наличии одного числа, а
        # verify_amounts_reconciliation в вырожденной ветке возвращает True, ничего
        # не сравнивая, — это давало zero_trust_verified без единой проверки.
        components_present = sum(1 for v in (debt, fee) if v is not None)
        if total is not None and components_present >= 2:
            details["math_checked"] = True
            ok, msg = verify_amounts_reconciliation(debt=debt, fee_penalty=fee, total=total)
            if not ok:
                issues.append(VerificationIssue("error", "FINANCIAL_DISCREPANCY", msg, "finances.total_rub"))
            else:
                details["math_verified_ok"] = True
        elif total is not None and debt is not None:
            # Компонент один: сверка сводится к «долг <= итог». Сравнение реальное,
            # но неполное — math_verified_ok не выставляется.
            details["math_checked"] = True
            ok, msg = verify_amounts_reconciliation(debt=debt, fee_penalty=None, total=total)
            if not ok:
                issues.append(VerificationIssue("error", "FINANCIAL_DISCREPANCY", msg, "finances.total_rub"))

        # 2. Декларативные сверки по типам документов (C-04).
        # Раньше сверка существовала только для УПД и претензий, а по исполнительным
        # листам, приказам ФССП и актам приемки суммы вообще не сверялись: реальные
        # числа 157611.62 + 7004.39 + 60000.00 и подставные 999999 давали один и тот же
        # результат zero_trust_verified с нулём замечаний.
        _apply_reconcile_rules(fin, issues, details)

        # Deduction Percentage Check
        # C-05: 0% — значимое значение (долг погашен полностью), а не «процент не указан».
        pct_val_raw = _first_present(fin, "deduction_percentage")
        if pct_val_raw is None:
            pct_val_raw = data.get("deduction_percentage")
        if pct_val_raw is not None and str(pct_val_raw).strip().lower() not in _EMPTY_STRINGS:
            is_alimony = any(
                marker in str(_first_present(data, "claim_subject", "claim", "subject") or "").lower()
                for marker in ("алимент", "несовершеннолетн", "ребен", "содержание", "вред")
            )
            ok_pct, msg_pct = verify_deduction_percentage(str(pct_val_raw), has_alimony_or_harm=is_alimony)
            if not ok_pct:
                # M-04: severity определяется по данным (значению процента), а не по тексту сообщения
                pct_val = parse_percentage_value(str(pct_val_raw))
                severity = "error" if (pct_val is not None and pct_val > 70.0) else "warning"
                issues.append(VerificationIssue(severity, "STATUTORY_LIMIT_ALERT", msg_pct, "deduction_percentage"))

    @classmethod
    def _audit_dates(cls, data: Dict[str, Any], issues: List[VerificationIssue], details: Dict[str, Any]) -> None:
        court = _as_dict(data.get("court"))

        # S-11: непарсируемые даты (мусор от VLM) не должны проходить молча
        _date_fields = (
            "doc_date", "act_date", "issue_date", "base_doc_date",
            "effective_date_from", "effective_date_to", "valid_until",
            "term_start", "term_end", "contract_date", "period_start", "period_end",
        )
        date_pairs = [(f, data.get(f)) for f in _date_fields]
        date_pairs += [("court.act_date", court.get("act_date")), ("court.issue_date", court.get("issue_date"))]
        unparseable = []
        for f_name, raw in date_pairs:
            if raw is None or not str(raw).strip():
                continue
            if parse_flexible_date(str(raw)) is None:
                unparseable.append(f_name)
                issues.append(
                    VerificationIssue(
                        "warning",
                        "UNPARSEABLE_DATE",
                        f"Поле '{f_name}' содержит дату в нераспознаваемом формате: '{raw}'",
                        f_name,
                    )
                )
        if unparseable:
            details["unparseable_dates"] = unparseable

        # Court & FSSP Chronology
        act_date = court.get("act_date") or data.get("doc_date")
        writ_date = court.get("issue_date")
        enf_date = data.get("enforcement_date")
        res_date = data.get("doc_date")

        ok, msg = verify_chronology(act_date, writ_date, enf_date, res_date)
        if not ok:
            issues.append(VerificationIssue("warning", "CHRONOLOGY_ANOMALY", msg, "dates"))

        # Powers of Attorney Chronology (valid_until >= issue_date)
        poa_issue = parse_flexible_date(data.get("issue_date", ""))
        poa_valid = parse_flexible_date(data.get("valid_until", ""))
        if poa_issue and poa_valid and poa_valid < poa_issue:
            issues.append(
                VerificationIssue(
                    "error",
                    "INVALID_VALIDITY_TERM",
                    f"Срок действия доверенности ({data.get('valid_until')}) предшествует дате выдачи ({data.get('issue_date')})",
                    "valid_until",
                )
            )

        # Commercial Contracts Chronology (term_end >= term_start or doc_date)
        term_start = parse_flexible_date(data.get("term_start", "")) or parse_flexible_date(data.get("doc_date", ""))
        term_end = parse_flexible_date(data.get("term_end", ""))
        if term_start and term_end and term_end < term_start:
            issues.append(
                VerificationIssue(
                    "warning",
                    "INVALID_CONTRACT_TERM",
                    f"Дата окончания договора ({data.get('term_end')}) предшествует началу ({data.get('term_start') or data.get('doc_date')})",
                    "term_end",
                )
            )

        # Acceptance certificates (doc_date >= contract_date)
        act_dt = parse_flexible_date(data.get("doc_date", ""))
        ctr_dt = parse_flexible_date(data.get("contract_date", ""))
        if act_dt and ctr_dt and act_dt < ctr_dt:
            issues.append(
                VerificationIssue(
                    "warning",
                    "ACCEPTANCE_PRECEEDS_CONTRACT",
                    f"Дата составления акта ({data.get('doc_date')}) предшествует дате договора ({data.get('contract_date')})",
                    "doc_date",
                )
            )

        # HR orders (effective_date_to >= effective_date_from)
        eff_from = parse_flexible_date(data.get("effective_date_from", ""))
        eff_to = parse_flexible_date(data.get("effective_date_to", ""))
        if eff_from and eff_to and eff_to < eff_from:
            issues.append(
                VerificationIssue(
                    "error",
                    "INVALID_HR_ORDER_DATES",
                    f"Дата окончания ({data.get('effective_date_to')}) предшествует дате начала ({data.get('effective_date_from')})",
                    "effective_date_to",
                )
            )
