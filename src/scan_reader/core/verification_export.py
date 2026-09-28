# -*- coding: utf-8 -*-
"""
Перенос результата верификации в реестры и записи для 1С.

Проблема, закрытая здесь. normalize_doc_data переносил из результата
обработки только file_name, file_path, doc_type, status, processed_at и
quality_*. Поля zero_trust_status, zero_trust, errors и
measurement_caveats в реестры НЕ попадали, поэтому:

    salary_deductions_registry.json  ->  без следа о верификации
    salary_deductions_registry_1c.json ->  ZeroTrustStatus = None
    1C_Импорт/<файл>_1c.json        ->  ZeroTrustStatus = None

Бухгалтер, загружающий реестр в 1С, видел status: COMPLETED и не мог
отличить проверенный документ от непроверенного. Это особенно опасно для
статуса gate_not_executed: кросс-модальная сверка не выполнялась вообще,
то есть реквизиты не сопоставлялись с оригиналом ни разу.
"""

from __future__ import annotations

from typing import Any, Dict

#: БЛОКИРУЮЩИЕ статусы: документ меняет status на NEEDS_REVIEW и не должен
#: приниматься в учёт без участия человека. Соответствуют кодам возврата 3 и 4.
REVIEW_REQUIRED_STATUSES = frozenset({
    "discrepancy_detected",
    "gate_not_executed",
    "rejected_unsupported",
    "heuristic_fallback",
})

#: СОВЕТАТЕЛЬНЫЕ статусы: проверки выполнены частично либо документ низкого
#: качества. status остаётся COMPLETED, но оператор получает явный сигнал.
#: Смешивать их с блокирующими нельзя: тогда флаг потерял бы смысл и почти
#: каждый реальный документ попадал бы в ручную обработку.
ADVISORY_STATUSES = frozenset({
    "partially_verified",
    "vlm_unverified",
    "ocr_low_confidence",
})

#: Метаданные результата, обязательные для переноса в реестры.
CARRIED_META_KEYS = (
    "file_name",
    "file_path",
    "doc_type",
    "status",
    "processed_at",
    "zero_trust_status",
    "zero_trust",
    "errors",
    "recovered_by_regex",
    "measurement_caveats",
    "requires_human_review",
)


def verification_status_of(doc_item: Any) -> str:
    """
    Статус верификации записи; '' если запись не проходила верификацию.

    Читает обе формы записи: исходный результат обработки (snake_case) и уже
    сконвертированную плоскую запись для 1С (PascalCase). Без этого повторный
    прогон экспортера по готовому реестру СТИРАЛ бы статус: в плоской записи
    ключ ZeroTrustStatus, а не zero_trust_status.
    """
    if not isinstance(doc_item, dict):
        return ""
    for key in ("zero_trust_status", "ZeroTrustStatus"):
        value = doc_item.get(key)
        if value:
            return str(value)
    for key in ("zero_trust", "ZeroTrustReport"):
        report = doc_item.get(key)
        if isinstance(report, dict) and report.get("status"):
            return str(report["status"])
    return ""


def _report_of(doc_item: Any) -> Any:
    if not isinstance(doc_item, dict):
        return None
    for key in ("zero_trust", "ZeroTrustReport"):
        report = doc_item.get(key)
        if isinstance(report, dict):
            return report
    return None


def requires_human_review(doc_item: Any) -> bool:
    """
    Требуется ли ручная проверка перед принятием документа в учёт.

    True для блокирующих статусов и для провальной экстракции: документ без
    данных не может быть принят автоматически, каким бы ни был статус
    верификации. Пустой результат проверки (нет статуса) НЕ считается
    требующим проверки: это бытовая запись, а не признак недостоверности.
    """
    if isinstance(doc_item, dict) and str(doc_item.get("status") or "").upper() == "FAILED":
        return True
    return verification_status_of(doc_item) in REVIEW_REQUIRED_STATUSES


def requires_advisory_review(doc_item: Any) -> bool:
    """Проверки выполнены частично: сигнал оператору без блокировки импорта."""
    return verification_status_of(doc_item) in ADVISORY_STATUSES


def normalize_doc_data(doc_item: Dict[str, Any]) -> Dict[str, Any]:
    """Приводит результат обработки к чистому словарю данных, сохраняя верификацию."""
    if "data" in doc_item and isinstance(doc_item["data"], dict):
        base = dict(doc_item["data"])
        for meta_k in CARRIED_META_KEYS:
            if meta_k in doc_item and meta_k not in base:
                base[meta_k] = doc_item[meta_k]
        # Статус верификации и флаг ручной проверки вычисляются, а не копируются:
        # запись может прийти из Registry_Full.json, где они уже есть, либо
        # из обработки, где их нет, либо уже быть плоской (PascalCase).
        zt_status = verification_status_of(base) or verification_status_of(doc_item)
        if zt_status:
            base["zero_trust_status"] = zt_status
        report = _report_of(base) or _report_of(doc_item)
        if report is not None:
            base.setdefault("zero_trust", report)
        review = doc_item.get("requires_human_review")
        base["requires_human_review"] = bool(
            review if review is not None
            else (requires_human_review(base) or requires_human_review(doc_item))
        )
        advisory = doc_item.get("requires_advisory_review")
        base["requires_advisory_review"] = bool(
            advisory if advisory is not None
            else (requires_advisory_review(base) or requires_advisory_review(doc_item))
        )
        if "quality_score_percent" not in base and "quality_score_percent" in doc_item:
            base["quality_score_percent"] = doc_item["quality_score_percent"]
        if "quality_status" not in base and "quality_status" in doc_item:
            base["quality_status"] = doc_item["quality_status"]
        return base
    out = dict(doc_item)
    if "zero_trust_status" not in out:
        zt_status = verification_status_of(out)
        if zt_status:
            out["zero_trust_status"] = zt_status
    if "requires_human_review" not in out:
        out["requires_human_review"] = requires_human_review(out)
    return out


def apply_verification_to_flat(flat: Dict[str, Any], doc_item: Any) -> Dict[str, Any]:
    """
    Проставляет признаки верификации в плоской записи для 1С.

    Раньше convert_salary_to_target_1c их не добавляла вовсе, и 1С получала
    запись без следа проверки. Теперь поля присутствуют всегда, даже когда
    верификация не выполнялась, — тогда ZeroTrustStatus пуст, а
    RequiresHumanReview истин.
    """
    zt_status = verification_status_of(doc_item)
    is_valid = None
    report = _report_of(doc_item)
    if isinstance(report, dict) and "is_valid" in report:
        is_valid = bool(report["is_valid"])

    flat["ZeroTrustStatus"] = zt_status
    flat["ZeroTrustValid"] = is_valid
    review = doc_item.get("requires_human_review") if isinstance(doc_item, dict) else None
    flat["RequiresHumanReview"] = bool(
        review if review is not None else requires_human_review(doc_item)
    )
    advisory = doc_item.get("requires_advisory_review") if isinstance(doc_item, dict) else None
    flat["AdvisoryReview"] = bool(
        advisory if advisory is not None else requires_advisory_review(doc_item)
    )
    return flat
