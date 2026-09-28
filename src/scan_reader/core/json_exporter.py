# -*- coding: utf-8 -*-
"""
Модуль экспорта структурированных JSON реестров и атомарного чекпоинтинга для ScanReader.
Обеспечивает:
1. Сохранение индивидуальных JSON карточек документов ({file_stem}_{doc_type}.json)
2. Формирование консолидированных JSON реестров по типам документов:
   - documents_registry.json (Исполнительные листы)
   - enforcement_orders_registry.json (Приказы и постановления ИП)
   - salary_deductions_registry.json (Взыскания на зарплату)
   - all_documents_registry.json (Единый сводный реестр всех обработанных документов)
3. Атомарное инкрементальное сохранение чекпоинтов (.checkpoint_{doc_type}.json)
"""

import os
import sys
import re
import json
import time
from pathlib import Path
from typing import List, Dict, Any, Optional

from .utils import as_dict as _as_dict, get_logger, sanitize_filename
from .io_utils import write_atomic

logger = get_logger("json_exporter")

REGISTRY_ALIASES = {
    "executive_documents": ["documents_registry.json", "executive_documents_registry.json"],
    "executive": ["documents_registry.json", "executive_documents_registry.json"],
    "enforcement_orders": ["enforcement_orders_registry.json"],
    "enforcement": ["enforcement_orders_registry.json"],
    "salary_deductions": ["salary_deductions_registry.json"],
    "salary": ["salary_deductions_registry.json"],
}


# as_dict импортируется из .utils как _as_dict (Фаза 8.6: было три копии).


def format_to_iso_date(date_str: Any) -> str:
    """Приводит произвольную строковую дату (DD.MM.YYYY, YYYY-MM-DD) к стандарту ISO YYYY-MM-DD."""
    if not date_str:
        return ""
    s = str(date_str).strip()
    m = re.search(r"(\d{1,2})[./\-](\d{1,2})[./\-](\d{4})", s)
    if m:
        day, month, year = m.group(1).zfill(2), m.group(2).zfill(2), m.group(3)
        return f"{year}-{month}-{day}"
    m_iso = re.search(r"(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})", s)
    if m_iso:
        year, month, day = m_iso.group(1), m_iso.group(2).zfill(2), m_iso.group(3).zfill(2)
        return f"{year}-{month}-{day}"
    return s


def extract_rosp_full_code(
    ip_number: str = "",
    doc_number: str = "",
    text_blobs: Optional[List[str]] = None,
    explicit_code: str = ""
) -> str:
    """Извлекает 5-значный ведомственный код подразделения РОСП ФССП."""
    if explicit_code and len(explicit_code.strip()) == 5 and explicit_code.strip().isdigit():
        return explicit_code.strip()

    combined = f"{ip_number} {doc_number}"
    m = re.search(r"/(\d{5})-ИП", combined, re.IGNORECASE)
    if m:
        return m.group(1)
    m = re.search(r"\b(\d{5})-ИП\b", combined, re.IGNORECASE)
    if m:
        return m.group(1)

    if text_blobs:
        for t in text_blobs:
            if not t:
                continue
            m_vksp = re.search(r"(?:ВКСП|код|РОСП)[\s:]+(\d{5})\b", t, re.IGNORECASE)
            if m_vksp:
                return m_vksp.group(1)
    return explicit_code or ""


def extract_payment_attributes_from_texts(texts: List[str]) -> Dict[str, str]:
    """Фолбэк-извлечение банковских и казначейских реквизитов из произвольного текста постановления."""
    combined = " ".join([t for t in texts if t])
    attrs: Dict[str, str] = {}

    # БИК (9 цифр, начинается на 04 или 01)
    m_bik = re.search(r"(?:БИК|БИК\s*ТОФК)[\s:]*([0-9]{9})\b", combined, re.IGNORECASE)
    if not m_bik:
        m_bik = re.search(r"\b(0[1-4][0-9]{7})\b", combined)
    if m_bik:
        attrs["bik"] = m_bik.group(1)

    # Счет (20 цифр, казначейский счет 032... / 031... или 401...)
    m_acc = re.search(r"(?:казначейский счет|расчетный счет|р/с|счет|сч)[\s№:]*([0-9]{20})\b", combined, re.IGNORECASE)
    if not m_acc:
        m_acc = re.search(r"\b(03[12][0-9]{17}|401[0-9]{17})\b", combined)
    if not m_acc:
        m_acc = re.search(r"\b([0-9]{20})\b", combined)
    if m_acc:
        attrs["payment_account"] = m_acc.group(1)

    # ИНН получателя (10 цифр)
    m_inn = re.search(r"(?:ИНН|ИНН\s*получателя)[\s:]*([0-9]{10})\b", combined, re.IGNORECASE)
    if m_inn:
        attrs["recipient_inn"] = m_inn.group(1)

    # КПП получателя (9 цифр)
    m_kpp = re.search(r"(?:КПП|КПП\s*получателя)[\s:]*([0-9]{9})\b", combined, re.IGNORECASE)
    if m_kpp:
        attrs["recipient_kpp"] = m_kpp.group(1)

    # ОКТМО (8 или 11 цифр)
    m_oktmo = re.search(r"(?:ОКТМО)[\s:]*([0-9]{8,11})\b", combined, re.IGNORECASE)
    if m_oktmo:
        attrs["oktmo"] = m_oktmo.group(1)

    # УИН (20-25 цифр, часто начинается на 322...)
    m_uin = re.search(r"(?:УИН)[\s:]*([0-9]{20,25})\b", combined, re.IGNORECASE)
    if not m_uin:
        m_uin = re.search(r"\b(322[0-9]{17,22})\b", combined)
    if m_uin:
        attrs["uin"] = m_uin.group(1)

    return attrs


def extract_base_doc_attributes(base_doc: str) -> Dict[str, str]:
    """
    Извлекает номер и дату первичного исполнительного документа (ИЛ, судебный приказ)
    из текстовой строки основания (например: 'Исполнительный лист № ФС00001234 от 20.01.2016').
    """
    res = {"number": "", "date": ""}
    if not base_doc:
        return res
    s = str(base_doc).strip()

    # Дата (приоритет: после 'от', 'дата', 'выдан')
    m_date = re.search(r"(?:от|дата|выдан)[\s:]*(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})", s, re.IGNORECASE)
    if not m_date:
        m_date = re.search(r"\b(\d{1,2}[./\-]\d{1,2}[./\-]\d{4})\b", s)
    if m_date:
        res["date"] = m_date.group(1)

    # Номер (после №, номер, N или в скобках)
    m_num = re.search(r"(?:№|номер|N)[\s:]*([A-Za-zА-Яа-я0-9\-\./_]+)", s, re.IGNORECASE)
    if m_num:
        res["number"] = m_num.group(1).rstrip(".,; ")
    else:
        m_brack = re.search(r"\(([A-Za-zА-Яа-я0-9\-\./_]+)\)", s)
        if m_brack:
            res["number"] = m_brack.group(1).strip()

    return res


def determine_pay_type(claim_subject: str = "", notes: str = "") -> str:
    """Определяет классификатор типа платежа (PayType): Aliment, Debt, Tax, Penalty."""
    text = f"{claim_subject} {notes}".lower()
    if any(k in text for k in ["алимент", "содержание детей", "на ребенка", "на детей"]):
        return "Aliment"
    if any(k in text for k in ["налог", "сбор", "фнс", "пенсион", "пошлин"]):
        return "Tax"
    if any(k in text for k in ["штраф", "коап", "административн"]):
        return "Penalty"
    if any(k in text for k in ["кредит", "заем", "задолжен", "взыскани", "долг"]):
        return "Debt"
    return "ExecutiveProduction"


def determine_recipient_type(recipient: str = "", account: str = "") -> str:
    """Определяет тип получателя (RecipientType): Budget, Company, Individual (M-18)."""
    r_lower = recipient.lower()
    if "уфк" in r_lower or account.startswith("03") or account.startswith("401") or "управление федерального казначейства" in r_lower:
        return "Budget"
    if any(k in r_lower for k in ["ооо", "ао", "пао", "зао", "банк", "фирма", "кооператив", "ип "]):
        return "Company"
    # Физическое лицо по умолчанию (раньше возвращался Budget для всех остальных)
    return "Individual"


def convert_salary_to_target_1c(doc_or_wrapper: Dict[str, Any], default_db_code: int = 10) -> Dict[str, Any]:
    """
    Преобразует извлеченный документ (или обертку с метаданными и ключом 'data')
    в целевой плоский формат JSON для автоматического создания документа
    (например, 'Исполнительный лист' в 1C:ЗУП / учетной системе).
    
    Результат строго соответствует спецификации целевого JSON (19 полей PascalCase в алфавитном порядке).
    """
    if isinstance(doc_or_wrapper, dict) and "Bik" in doc_or_wrapper and "DbCode" in doc_or_wrapper:
        return dict(doc_or_wrapper)

    if isinstance(doc_or_wrapper, dict) and "data" in doc_or_wrapper and isinstance(doc_or_wrapper["data"], dict):
        data = doc_or_wrapper["data"]
    elif isinstance(doc_or_wrapper, dict):
        data = doc_or_wrapper
    else:
        data = {}

    authority = _as_dict(data.get("authority"))
    employer = _as_dict(data.get("employer"))
    finances = _as_dict(data.get("finances"))
    payment_details: Any = _as_dict(data.get("payment_details"))

    # Если передан объект модели Pydantic
    if hasattr(payment_details, "model_dump"):
        payment_details = payment_details.model_dump()
    elif hasattr(payment_details, "dict"):
        payment_details = payment_details.dict()

    # Сбор текстовых блоков для fallback regex-парсинга
    text_blobs = [
        finances.get("periodic_details", ""),
        data.get("claimant_details", ""),
        data.get("debtor_details", ""),
        data.get("notes", ""),
        authority.get("address", ""),
        data.get("claim_subject", "")
    ]
    regex_attrs = extract_payment_attributes_from_texts(text_blobs)

    # Платежные реквизиты (приоритет: явные поля payment_details -> regex фолбэк)
    bik = payment_details.get("bik") or regex_attrs.get("bik") or ""
    payment_account = payment_details.get("payment_account") or regex_attrs.get("payment_account") or ""
    recipient_inn = payment_details.get("recipient_inn") or regex_attrs.get("recipient_inn") or ""
    recipient_kpp = payment_details.get("recipient_kpp") or regex_attrs.get("recipient_kpp") or ""
    oktmo = payment_details.get("oktmo") or regex_attrs.get("oktmo") or ""
    uin = payment_details.get("uin") or regex_attrs.get("uin") or ""

    # Получатель платежа
    recipient = payment_details.get("recipient") or ""
    if not recipient:
        combined_text = " ".join(text_blobs)
        m_rec = re.search(r"(?:получател[ьяе]|в пользу)[\s:]*([^\n;,\.]{5,100}\([^)]+\)|УФК[^\n;,]{5,80})", combined_text, re.IGNORECASE)
        if m_rec:
            recipient = m_rec.group(1).strip()
        elif authority.get("name"):
            recipient = f"УФК ({authority.get('name')})"

    # Даты и номера документов
    # 1. ResolutionDate: дата вынесения постановления пристава
    doc_date = data.get("doc_date") or data.get("act_date") or ""
    resolution_iso_date = format_to_iso_date(doc_date)

    # 2. ExecutiveDocumentDate и ExecutiveDocumentNumber: первичный исполнительный документ
    base_doc = str(data.get("base_doc") or "").strip()
    base_attrs = extract_base_doc_attributes(base_doc)

    exec_date_raw = data.get("base_doc_date") or base_attrs.get("date") or ""
    exec_doc_iso_date = format_to_iso_date(exec_date_raw) if exec_date_raw else ""

    exec_number_raw = (
        data.get("base_doc_number")
        or base_attrs.get("number")
        or data.get("doc_number")
        or data.get("ip_number")
        or ""
    )

    # Ведомственный код РОСП
    rosp_code = extract_rosp_full_code(
        ip_number=data.get("ip_number", ""),
        doc_number=data.get("doc_number", ""),
        text_blobs=text_blobs,
        explicit_code=payment_details.get("rosp_code", "")
    )

    # Тип платежа и тип получателя
    claim_subject = data.get("claim_subject", "")
    pay_type = determine_pay_type(claim_subject, data.get("notes", ""))
    recipient_type = determine_recipient_type(recipient, payment_account)

    db_code_val = data.get("db_code") or doc_or_wrapper.get("db_code") or default_db_code
    try:
        db_code = int(db_code_val)
    except (ValueError, TypeError):
        db_code = default_db_code

    # Строго в алфавитном порядке ключей PascalCase
    target_dict = {
        "Bik": str(bik),
        "DbCode": db_code,
        "ExecutiveDocumentDate": exec_doc_iso_date,
        "ExecutiveDocumentNumber": str(exec_number_raw),
        "FSSP_Head": str(authority.get("name", "")),
        "Oktmo": str(oktmo),
        "OrganizationName": str(employer.get("name", "")),
        "PaymentAccount": str(payment_account),
        "PayType": pay_type,
        "PurposeType": "ExecutiveProduction",
        "Recipient": str(recipient),
        "RecipientInn": str(recipient_inn),
        "RecipientKpp": str(recipient_kpp),
        "RecipientType": recipient_type,
        "ResolutionDate": resolution_iso_date,
        "RospAddressFakt": str(authority.get("address", "")),
        "RospBailiffFio": str(authority.get("officer", "")),
        "RospFullCode": str(rosp_code),
        "Uin": str(uin)
    }

    return target_dict


def export_1c_target_json(documents: List[Dict[str, Any]], filepath: str, default_db_code: int = 10):
    """Экспорт реестра документов в целевой формат JSON (массив для 1С / учетной системы)."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    converted = [convert_salary_to_target_1c(d, default_db_code=default_db_code) for d in documents]
    write_atomic(filepath, json.dumps(converted, ensure_ascii=False, indent=2))
    logger.info(f"1C Целевой JSON реестр сохранен: {filepath}")


def export_single_1c_target_json(doc: Dict[str, Any], filepath: str, default_db_code: int = 10):
    """Экспорт одного документа в целевой плоский JSON-файл (для 1С / учетной системы)."""
    os.makedirs(os.path.dirname(os.path.abspath(filepath)), exist_ok=True)
    converted = convert_salary_to_target_1c(doc, default_db_code=default_db_code)
    write_atomic(filepath, json.dumps(converted, ensure_ascii=False, indent=2))
    logger.info(f"1C Целевой JSON документа сохранен: {filepath}")


def normalize_doc_data(doc_item: Dict[str, Any]) -> Dict[str, Any]:
    """Приводит результат обработки документа к чистому словарю данных."""
    if "data" in doc_item and isinstance(doc_item["data"], dict):
        base = dict(doc_item["data"])
        for meta_k in ("file_name", "file_path", "doc_type", "status", "processed_at"):
            if meta_k in doc_item and meta_k not in base:
                base[meta_k] = doc_item[meta_k]
        if "quality_score_percent" in doc_item:
            base["quality_score_percent"] = doc_item["quality_score_percent"]
        if "quality_status" in doc_item:
            base["quality_status"] = doc_item["quality_status"]
        return base
    return dict(doc_item)


def convert_to_flat_1c(doc_item: Dict[str, Any], default_db_code: int = 10) -> Dict[str, Any]:
    """
    Универсальное преобразование документа любого типа в плоский (Flat) JSON для 1С и учетных систем.
    Исключает вложенные словари. Все даты приводятся к ISO (YYYY-MM-DD), суммы — к числам.
    """
    doc_type = doc_item.get("doc_type", "unknown")
    is_salary = doc_type in ("salary_deductions", "salary") or ("Bik" in doc_item and "DbCode" in doc_item)

    if is_salary:
        flat = convert_salary_to_target_1c(doc_item, default_db_code=default_db_code)
        if "zero_trust_status" in doc_item or "zero_trust" in doc_item:
            flat["ZeroTrustStatus"] = str(doc_item.get("zero_trust_status") or doc_item.get("zero_trust", {}).get("status", "unknown"))
            flat["ZeroTrustValid"] = bool(doc_item.get("zero_trust", {}).get("is_valid", True))
        if "quality_score_percent" in doc_item:
            flat["QualityScore"] = float(doc_item.get("quality_score_percent", 100.0))
        return flat

    data_val: Any = doc_item.get("data")
    data: Dict[str, Any] = data_val if isinstance(data_val, dict) else doc_item
    authority = _as_dict(data.get("court") or data.get("fssp") or data.get("authority"))

    # Разрешение Стороны 1 (Взыскатель, Заказчик, Продавец, Доверитель, Заявитель, Работодатель)
    party1 = _as_dict(
        data.get("claimant")
        or data.get("party_one")
        or data.get("seller")
        or data.get("customer")
        or data.get("principal")
        or data.get("sender")
    )

    # Разрешение Стороны 2 (Должник, Исполнитель, Покупатель, Поверенный, Адресат, Работник)
    party2 = _as_dict(
        data.get("debtor")
        or data.get("party_two")
        or data.get("buyer")
        or data.get("contractor")
        or data.get("agent")
        or data.get("recipient")
        or data.get("employee")
    )

    p1_name = str(party1.get("name") or data.get("organization_name") or data.get("claimant_name", ""))
    p1_inn = str(party1.get("inn") or data.get("organization_inn", ""))
    p2_name = str(party2.get("name") or party2.get("full_name") or data.get("debtor_name", ""))
    p2_inn = str(party2.get("inn", ""))

    finances = _as_dict(data.get("finances"))
    total_rub = (
        finances.get("total_rub")
        or finances.get("total_deduction_rub")
        or finances.get("total_claim_rub")
        or finances.get("debt_amount_rub")
        or party2.get("salary_rub")
    )

    flat_dict = {
        "FileName": str(doc_item.get("file_name", "")),
        "DocType": str(doc_type),
        "DocTypeTitle": str(doc_item.get("doc_type_title", "")),
        "DocDate": format_to_iso_date(data.get("doc_date") or data.get("issue_date") or data.get("act_date") or authority.get("act_date", "")),
        "DocNumber": str(data.get("doc_number") or data.get("reg_number") or authority.get("case_number", "")),
        "CourtOrAuthority": str(authority.get("name", "")),
        "ClaimantName": p1_name,
        "ClaimantInn": p1_inn,
        "PartyOneName": p1_name,
        "PartyOneInn": p1_inn,
        "DebtorName": p2_name,
        "DebtorInn": p2_inn,
        "PartyTwoName": p2_name,
        "PartyTwoInn": p2_inn,
        "ClaimSubject": str(data.get("claim_subject") or data.get("powers_summary") or data.get("order_type", "")),
        "DebtAmountRub": finances.get("debt_amount_rub") or finances.get("main_debt_rub") or finances.get("principal_debt_rub") or finances.get("total_rub_no_vat"),
        "CourtFeeRub": finances.get("court_fee_rub") or finances.get("fee_penalty_rub") or finances.get("penalty_rub"),
        "TotalAmountRub": total_rub,
        "ZeroTrustStatus": str(doc_item.get("zero_trust_status") or doc_item.get("zero_trust", {}).get("status", "unknown")),
        "ZeroTrustValid": bool(doc_item.get("zero_trust", {}).get("is_valid", True)),
        "QualityScore": float(doc_item.get("quality_score_percent", 100.0)),
        "ProcessedAt": str(doc_item.get("processed_at", "")),
    }
    return flat_dict



def save_single_document_json(doc_result: Dict[str, Any], output_dir: str) -> str:
    """
    Сохраняет результаты обработки документа в два основных формата:
    1. {file_stem}_Full.json — полный иерархический JSON (данные, Zero-Trust аудит, Guardrails, метрики).
    2. {file_stem}_Flat.json — плоский JSON для 1С (без вложенных структур, ISO-даты, числа).
    
    Для 100% обратной совместимости также поддерживаются легаси-псевдонимы:
    {file_stem}_{doc_type}.json, {file_stem}_raw.json, 1C_Импорт/{file_stem}_1c.json.
    """
    os.makedirs(output_dir, exist_ok=True)
    file_name = doc_result.get("file_name", "document")
    file_path = doc_result.get("file_path", "")
    doc_type = doc_result.get("doc_type", "unknown")
    base_stem = sanitize_filename(Path(file_name).stem)

    # C-12: Защита от коллизий имен файлов (одинаковый stem при разных путях/файлах)
    stem = base_stem
    candidate_full = os.path.join(output_dir, f"{stem}_Full.json")
    if os.path.exists(candidate_full):
        existing_file_path: Optional[str] = None
        readable = True
        try:
            with open(candidate_full, "r", encoding="utf-8") as existing_f:
                existing_doc = json.load(existing_f)
                existing_file_path = existing_doc.get("file_path") if isinstance(existing_doc, dict) else None
        except Exception as e:
            # Фаза 7.15: нечитаемый существующий файл РАНЬШЕ приводил к тихой
            # перезаписи: existing_file_path становился "", условие коллизии не
            # срабатывало, и чужой документ затирался. Теперь коллизия
            # считается неразрешённой, и новый файл получает суффикс.
            readable = False
            logger.warning(
                f"Существующий '{os.path.basename(candidate_full)}' не читается ({e}); "
                "считаем имя занятым, чтобы не перезаписать неизвестное содержимое."
            )

        if not readable or (file_path and existing_file_path and file_path != existing_file_path):
            counter = 2
            while True:
                candidate_stem = f"{base_stem}__{counter}"
                candidate_path = os.path.join(output_dir, f"{candidate_stem}_Full.json")
                if not os.path.exists(candidate_path):
                    stem = candidate_stem
                    break
                counter += 1
            logger.warning(
                f"⚠️ [C-12 Collision Guard] Обнаружена коллизия имени '{base_stem}' "
                f"между '{existing_file_path or '<нечитаемый файл>'}' и '{file_path}'. "
                f"Файл сохранен как '{stem}'."
            )

    full_path = os.path.join(output_dir, f"{stem}_Full.json")
    flat_path = os.path.join(output_dir, f"{stem}_Flat.json")

    is_salary = doc_type in ("salary_deductions", "salary") or ("Bik" in doc_result and "DbCode" in doc_result)
    flat_data = convert_to_flat_1c(doc_result)

    # 1. Атомарное сохранение Full JSON (H-03)
    try:
        write_atomic(full_path, json.dumps(doc_result, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"Не удалось записать Full JSON '{full_path}': {e}")

    # 2. Атомарное сохранение Flat JSON (1C) (H-03)
    try:
        write_atomic(flat_path, json.dumps(flat_data, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"Не удалось записать Flat JSON '{flat_path}': {e}")

    # 3. Сохранение легаси-копий для совместимости (H-03)
    legacy_type_path = os.path.join(output_dir, f"{stem}_{doc_type}.json")
    try:
        write_atomic(legacy_type_path, json.dumps(flat_data if is_salary else doc_result, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.debug(f"Не удалось записать legacy JSON '{legacy_type_path}': {e}")

    raw_path = os.path.join(output_dir, f"{stem}_raw.json")
    try:
        write_atomic(raw_path, json.dumps(doc_result, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.debug(f"Не удалось записать raw JSON '{raw_path}': {e}")

    if is_salary:
        import_dir = os.path.join(output_dir, "1C_Импорт")
        os.makedirs(import_dir, exist_ok=True)
        import_path = os.path.join(import_dir, f"{stem}_1c.json")
        try:
            write_atomic(import_path, json.dumps(flat_data, ensure_ascii=False, indent=2))
        except Exception as e:
            logger.debug(f"Не удалось записать 1C import JSON '{import_path}': {e}")

    # Прикрепляем пути сохраненных файлов к объекту результата
    doc_result["saved_files"] = {
        "full": full_path,
        "flat": flat_path
    }

    return legacy_type_path if is_salary else full_path


def save_checkpoint(docs: List[Dict[str, Any]], checkpoint_path: str):
    """Атомарно сохраняет контрольную точку текущей обработки (H-03)."""
    try:
        write_atomic(checkpoint_path, json.dumps(docs, ensure_ascii=False, indent=2))
    except Exception as e:
        logger.warning(f"Не удалось записать чекпоинт '{checkpoint_path}': {e}")


def load_checkpoint(checkpoint_path: str) -> List[Dict[str, Any]]:
    """Загружает сохраненную контрольную точку при наличии."""
    if os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception as e:
            logger.warning(f"Ошибка чтения чекпоинта '{checkpoint_path}': {e}")
    return []


def clean_checkpoint(checkpoint_path: str):
    """Удаляет файл контрольной точки после успешного завершения."""
    if os.path.exists(checkpoint_path):
        try:
            os.remove(checkpoint_path)
        except OSError as e:
            logger.debug(f"Не удалось удалить чекпоинт '{checkpoint_path}': {e}")


def _result_identity(doc_item: Dict[str, Any]) -> str:
    """
    Ключ уникальности записи реестра.

    Полный путь предпочтительнее имени файла: два документа с одинаковым именем
    в разных папках остаются разными записями. Повторная обработка того же файла
    обновляет существующую запись, а не плодит дубли.
    """
    path = str(doc_item.get("file_path") or "").strip()
    name = str(doc_item.get("file_name") or "").strip()
    doc_type = str(doc_item.get("doc_type") or "unknown")
    return f"{path or name}||{doc_type}"


def _merge_with_existing_results(
    results: List[Dict[str, Any]],
    results_dir: str,
) -> List[Dict[str, Any]]:
    """
    C-07: слияние с уже накопленным реестром вместо полной перезаписи.

    Раньше process_single_document вызывал export_consolidated_registries([result], ...),
    а функция писала переданный список целиком через write_atomic. Обработка одного
    файла в одиночном режиме (CLI run, MCP scan_document) уничтожала реестр,
    накопленный предыдущими запусками.

    Единый источник правды — Registry_Full.json: он хранит исходные result-записи,
    из которых выводятся все остальные реестры. Поэтому достаточно слить один раз.
    """
    registry_path = os.path.join(results_dir, "Registry_Full.json")
    if not os.path.exists(registry_path):
        return list(results)

    try:
        with open(registry_path, "r", encoding="utf-8") as fh:
            previous = json.load(fh)
    except Exception as e:
        # Нечитаемый реестр не должен приводить ни к потере новых, ни к молчаливой
        # замене старых данных: отводим его в сторону с явным следом.
        stamp = time.strftime("%Y%m%d_%H%M%S")
        backup = f"{registry_path}.corrupt_{stamp}.bak"
        try:
            os.replace(registry_path, backup)
            logger.warning(
                f"Реестр '{registry_path}' не читается ({e}); перемещен в '{backup}', "
                "создаётся заново из текущих результатов."
            )
        except Exception as be:
            logger.error(f"Не удалось сохранить нечитаемый реестр '{registry_path}': {be}")
        return list(results)

    if not isinstance(previous, list):
        logger.warning(
            f"Формат реестра '{registry_path}' неожидан ({type(previous).__name__}); "
            "используются только текущие результаты."
        )
        return list(results)

    merged: List[Dict[str, Any]] = [r for r in previous if isinstance(r, dict)]
    position = {_result_identity(r): i for i, r in enumerate(merged)}

    for rec in results:
        if not isinstance(rec, dict):
            continue
        key = _result_identity(rec)
        if key in position:
            merged[position[key]] = rec
        else:
            position[key] = len(merged)
            merged.append(rec)

    logger.info(
        f"Реестр слит с существующим: {len(previous)} + {len(results)} -> {len(merged)} записей."
    )
    return merged


def export_consolidated_registries(
    results: List[Dict[str, Any]],
    results_dir: str,
    merge: bool = False,
) -> Dict[str, str]:
    """
    Формирует консолидированные структурированные JSON-реестры с атомарной записью (H-03):
    - documents_registry.json
    - enforcement_orders_registry.json
    - salary_deductions_registry.json
    - salary_deductions_registry_1c.json (Целевой массив документов для 1С)
    - all_documents_registry.json
    Также сохраняет отдельные карточки в папку 1C_Импорт/ для зарплатных документов.
    Возвращает словарь {имя_файла: абсолютный_путь}.

    :param merge: True — слить с уже накопленным реестром в results_dir вместо
        полной перезаписи (C-07). Используется в одиночной обработке документа.
        False — полная перезапись, как в пакетном режиме, где передан полный набор.
    """
    os.makedirs(results_dir, exist_ok=True)
    saved_files: Dict[str, str] = {}

    if merge:
        results = _merge_with_existing_results(results, results_dir)

    # C-08: фильтрация выполняется ОДИН раз здесь, до формирования любого выхода.
    # Раньше проверка стояла только в цикле группировки, а Registry_Full.json писался
    # из сырого results в обход неё, поэтому отказавшиеся записи всё равно попадали
    # в полный реестр.
    results = [
        item for item in results
        if isinstance(item, dict)
        and item.get("status") != "FAILED"
        and not (
            isinstance(item.get("data"), dict)
            and item["data"].get("_extraction_failed")
        )
    ]

    # 1. Группировка по типам документов
    by_category: Dict[str, List[Dict[str, Any]]] = {}
    all_clean_docs: List[Dict[str, Any]] = []

    for item in results:
        doc_type = item.get("doc_type", "unknown")
        clean_doc = normalize_doc_data(item)
        all_clean_docs.append(clean_doc)

        if doc_type not in by_category:
            by_category[doc_type] = []
        by_category[doc_type].append(clean_doc)

    # 2. Экспорт по категориям
    for cat_id, cat_docs in by_category.items():
        aliases = REGISTRY_ALIASES.get(cat_id, [f"{cat_id}_registry.json"])
        for alias_name in aliases:
            out_path = os.path.join(results_dir, alias_name)
            try:
                write_atomic(out_path, json.dumps(cat_docs, ensure_ascii=False, indent=2))
                saved_files[alias_name] = out_path
                logger.info(f"💾 Экспортирован JSON реестр '{alias_name}': {len(cat_docs)} записей.")
            except Exception as e:
                logger.warning(f"Не удалось записать реестр '{out_path}': {e}")

        # Для постановлений о взыскании на зарплату дополнительно формируем целевой реестр 1С
        if cat_id in ("salary_deductions", "salary"):
            target_1c_registry = [convert_salary_to_target_1c(d) for d in cat_docs]
            reg_1c_name = "salary_deductions_registry_1c.json"
            reg_1c_path = os.path.join(results_dir, reg_1c_name)
            try:
                write_atomic(reg_1c_path, json.dumps(target_1c_registry, ensure_ascii=False, indent=2))
                saved_files[reg_1c_name] = reg_1c_path
                logger.info(f"💾 Экспортирован 1С целевой реестр '{reg_1c_name}': {len(target_1c_registry)} записей.")
            except Exception as e:
                logger.warning(f"Не удалось записать целевой 1С реестр '{reg_1c_path}': {e}")

            # Индивидуальные JSON-файлы под каждый документ в папку 1C_Импорт
            import_1c_dir = os.path.join(results_dir, "1C_Импорт")
            os.makedirs(import_1c_dir, exist_ok=True)
            for idx, doc in enumerate(cat_docs, 1):
                raw_filename = doc.get("file_name") or f"doc_{idx}"
                base_doc_name = sanitize_filename(Path(raw_filename).stem)
                single_1c_path = os.path.join(import_1c_dir, f"{base_doc_name}_1c.json")
                try:
                    write_atomic(single_1c_path, json.dumps(convert_salary_to_target_1c(doc), ensure_ascii=False, indent=2))
                except Exception as e:
                    logger.debug(f"Не удалось записать 1С карточку '{single_1c_path}': {e}")

    # 3. Единый сводный реестр всех документов
    all_path = os.path.join(results_dir, "all_documents_registry.json")
    try:
        write_atomic(all_path, json.dumps(all_clean_docs, ensure_ascii=False, indent=2))
        saved_files["all_documents_registry.json"] = all_path
        logger.info(f"💾 Экспортирован единый сводный JSON реестр 'all_documents_registry.json': {len(all_clean_docs)} записей.")
    except Exception as e:
        logger.warning(f"Не удалось записать сводный реестр '{all_path}': {e}")

    # 4. Реестры Full и Flat
    registry_full_path = os.path.join(results_dir, "Registry_Full.json")
    try:
        write_atomic(registry_full_path, json.dumps(results, ensure_ascii=False, indent=2))
        saved_files["Registry_Full.json"] = registry_full_path
        logger.info(f"💾 Экспортирован сводный реестр 'Registry_Full.json': {len(results)} записей.")
    except Exception as e:
        logger.warning(f"Не удалось записать 'Registry_Full.json': {e}")

    flat_list = [convert_to_flat_1c(item) for item in results]
    registry_flat_path = os.path.join(results_dir, "Registry_Flat.json")
    try:
        write_atomic(registry_flat_path, json.dumps(flat_list, ensure_ascii=False, indent=2))
        saved_files["Registry_Flat.json"] = registry_flat_path
        logger.info(f"💾 Экспортирован сводный реестр 1С 'Registry_Flat.json': {len(flat_list)} записей.")
    except Exception as e:
        logger.warning(f"Не удалось записать 'Registry_Flat.json': {e}")

    return saved_files


if __name__ == "__main__":
    # Фаза 8.7: третья копия настройки UTF-8 удалена, осталась в core/utils
    from .utils import setup_console_utf8

    setup_console_utf8()

    if len(sys.argv) > 1:
        in_path = sys.argv[1]
        out_path = sys.argv[2] if len(sys.argv) > 2 else None
        if os.path.exists(in_path):
            with open(in_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            if isinstance(raw, list):
                converted_list: List[Any] = [convert_salary_to_target_1c(item) for item in raw]
                res = converted_list
            else:
                res = [convert_salary_to_target_1c(raw)]

            if out_path:
                os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
                with open(out_path, "w", encoding="utf-8") as f:
                    json.dump(res, f, ensure_ascii=False, indent=2)
                print(f"[OK] Конвертированный 1C JSON сохранен в: {out_path}")
            else:
                print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            print(f"[ОШИБКА] Файл не найден: {in_path}")
    else:
        print("Использование: py -3 json_exporter.py <входной_файл.json> [<выходной_файл_1c.json>]")

