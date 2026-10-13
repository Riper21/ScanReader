# -*- coding: utf-8 -*-
"""
Кастомный сборщик плоской 1С-записи для постановлений об обращении взыскания
на заработную плату (P2.1, Правило 3).

Раньше вся эта логика жила в core.json_exporter и зашивала в ядро лексику
конкретного типа документа (работодатель, платёжные реквизиты, коды РОСП).
Формат обмена 1С остаётся в ядре; сборка записи этого типа — здесь.
Подключается ключом manifest.json «flat_1c_builder»: «flat_1c.convert».

Интерпретаторы регулярных выражений (даты, реквизиты, типы платежа)
экспортируются ядром как переиспользуемые функции без знания о типах
документов.
"""

import re
from typing import Any, Dict

from scan_reader.core.json_exporter import (
    determine_pay_type,
    determine_recipient_type,
    extract_base_doc_attributes,
    extract_payment_attributes_from_texts,
    extract_rosp_full_code,
    format_to_iso_date,
)
from scan_reader.core.utils import as_dict as _as_dict
from scan_reader.core.verification_export import apply_verification_to_flat


def convert(doc_or_wrapper: Dict[str, Any], default_db_code: int = 10) -> Dict[str, Any]:
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

    # Признаки верификации обязаны попадать в запись для 1С: бухгалтер должен
    # видеть, что документ проверялся и требовал ли он ручного подтверждения.
    result = apply_verification_to_flat(target_dict, doc_or_wrapper)

    if "quality_score_percent" in doc_or_wrapper:
        result["QualityScore"] = float(doc_or_wrapper["quality_score_percent"])
    return result
