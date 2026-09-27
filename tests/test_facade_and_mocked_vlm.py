# -*- coding: utf-8 -*-
"""
Тесты фасада LegalDocPlatformFacade, Guardrails валидации и бенчмарка.
"""

from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.core.token_tracker import TokenUsageTracker
from scan_reader.core.rate_limiter import RateLimiter


def test_heuristic_classification():
    facade = LegalDocPlatformFacade()

    # Проверка классификации по имени/пути (как для incoming/, так и для обратной совместимости)
    dt1, conf1, m1 = facade.classify_document("Входящие_документы/Приказы_ИП/order_123.jpg")
    assert dt1 == "enforcement_orders"
    assert conf1 > 0.8

    dt1_inc, conf1_inc, _ = facade.classify_document("incoming/enforcement_orders/order_123.jpg")
    assert dt1_inc == "enforcement_orders"
    assert conf1_inc > 0.8

    dt2, conf2, m2 = facade.classify_document("docs/Исполнительные_листы/Ispol._list_1.jpg")
    assert dt2 == "executive_documents"
    assert conf2 > 0.8

    dt2_inc, conf2_inc, _ = facade.classify_document("incoming/executive_documents/doc_1.pdf")
    assert dt2_inc == "executive_documents"
    assert conf2_inc > 0.8

    dt3, conf3, m3 = facade.classify_document("Взыскание_на_зарплату/salary_doc.pdf")
    assert dt3 == "salary_deductions"
    assert conf3 > 0.8

    dt3_inc, conf3_inc, _ = facade.classify_document("incoming/salary_deductions/order.pdf")
    assert dt3_inc == "salary_deductions"
    assert conf3_inc > 0.8


def test_guardrails_validation(sample_enforcement_order_data):
    facade = LegalDocPlatformFacade()

    # Валидный документ
    res_valid = facade.validate_document(sample_enforcement_order_data, "enforcement_orders")
    assert res_valid["passed"] is True
    assert res_valid["score"] == 100.0
    assert len(res_valid["issues"]) == 0

    # Документ с нарушением обязательного поля
    broken_data = dict(sample_enforcement_order_data)
    broken_data["claimant"] = {"name": "", "party_type": ""}
    res_invalid = facade.validate_document(broken_data, "enforcement_orders")
    assert res_invalid["passed"] is False
    assert res_invalid["score"] < 100.0
    assert any(i["field"] == "claimant.name" for i in res_invalid["issues"])


def test_benchmark_calculation(sample_enforcement_order_data):
    facade = LegalDocPlatformFacade()

    # 100% совпадение с эталоном
    bench_100 = facade.benchmark_against_ground_truth(
        extracted=sample_enforcement_order_data,
        ground_truth=sample_enforcement_order_data,
        doc_type="enforcement_orders"
    )
    assert bench_100["accuracy"] == 100.0

    # Частичное несовпадение
    mutated = dict(sample_enforcement_order_data)
    mutated["court"] = {"name": "Другой суд", "case_number": "99-9999"}
    bench_part = facade.benchmark_against_ground_truth(
        extracted=mutated,
        ground_truth=sample_enforcement_order_data,
        doc_type="enforcement_orders"
    )
    assert 0.0 < bench_part["accuracy"] < 100.0


def test_token_tracker_and_rate_limiter():
    tracker = TokenUsageTracker.get_tracker()
    tracker.reset()

    tracker.record_call(
        model="qwen2.5-vl:7b",
        prompt_tokens=100,
        completion_tokens=50,
        latency_sec=1.5,
        stage_name="extraction"
    )

    summary = tracker.get_summary()
    assert summary["total_calls"] == 1
    assert summary["prompt_tokens"] == 100
    assert summary["completion_tokens"] == 50
    assert summary["total_tokens"] == 150
    assert summary["avg_speed_tokens_per_sec"] > 0

    limiter = RateLimiter.get_limiter()
    assert limiter.acquire() is True
    limiter.release()
