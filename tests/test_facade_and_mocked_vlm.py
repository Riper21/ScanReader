# -*- coding: utf-8 -*-
"""
Тесты фасада LegalDocPlatformFacade, Guardrails валидации и бенчмарка.
"""

from unittest.mock import MagicMock

import pytest

from scan_reader.facade import LegalDocPlatformFacade, _vlm_http_timeout
from scan_reader.type_registry import UNKNOWN_CATEGORY
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


# =========================================================================
# P2.4: таймауты VLM, порог уверенности роутера, телеметрия модальности
# =========================================================================
def test_vlm_http_timeout_precedence(monkeypatch):
    """P2.4: VLM_HTTP_TIMEOUT сильнее устаревшего VLM_TIMEOUT; битые
    значения не роняют инициализацию клиента."""
    monkeypatch.delenv("VLM_HTTP_TIMEOUT", raising=False)
    monkeypatch.delenv("VLM_TIMEOUT", raising=False)
    assert _vlm_http_timeout() == 600.0

    monkeypatch.setenv("VLM_HTTP_TIMEOUT", "123.5")
    assert _vlm_http_timeout() == 123.5

    monkeypatch.setenv("VLM_TIMEOUT", "321")
    assert _vlm_http_timeout() == 123.5

    monkeypatch.delenv("VLM_HTTP_TIMEOUT")
    assert _vlm_http_timeout() == 321.0

    monkeypatch.setenv("VLM_HTTP_TIMEOUT", "не-число")
    assert _vlm_http_timeout() == 600.0


def test_rate_limiter_slot_timeout_variable(monkeypatch):
    """P2.4: ожидание слота читает VLM_SLOT_TIMEOUT, а не HTTP-таймаут."""
    monkeypatch.delenv("VLM_TIMEOUT", raising=False)
    monkeypatch.setenv("VLM_SLOT_TIMEOUT", "7.5")
    limiter = RateLimiter(enabled=False)
    assert limiter.timeout == 7.5

    monkeypatch.setenv("VLM_SLOT_TIMEOUT", "не-число")
    with RateLimiter(enabled=False) as _:
        pass


def test_router_rejects_low_confidence(monkeypatch):
    """P2.4: уверенность роутера ниже VLM_ROUTER_CONFIDENCE_MIN отклоняет
    тип — документ уходит в unknown, а не в чужую ветку экстракции."""
    facade = LegalDocPlatformFacade()
    client = MagicMock()
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = (
        '{"doc_type": "hr_orders", "confidence": 0.3, "reason": "сомнительно"}'
    )
    resp.usage = None
    client.chat.completions.create.return_value = resp
    monkeypatch.setattr(facade, "_get_client", lambda: client)
    monkeypatch.setattr(facade, "processor", MagicMock())
    facade.processor.prepare_dual_zone_inputs.return_value = ("text", "Заголовок")
    monkeypatch.setenv("VLM_ROUTER_CONFIDENCE_MIN", "0.5")

    dt, conf, method, _parsed = facade.classify_via_vlm("order.jpg")
    assert dt == UNKNOWN_CATEGORY
    assert method == "vlm_low_confidence"

    resp.choices[0].message.content = (
        '{"doc_type": "hr_orders", "confidence": 0.9, "reason": "уверенно"}'
    )
    dt, conf, method, _parsed = facade.classify_via_vlm("order.jpg")
    assert dt == "hr_orders"
    assert method == "vlm_dual_zone_router"


def test_token_tracker_modality_breakdown():
    """P2.4: сводка различает text- и vision-вызовы."""
    tracker = TokenUsageTracker.get_tracker()
    tracker.reset()
    tracker.record_call(model="m", prompt_tokens=10, completion_tokens=5,
                        stage_name="s", modality="vision")
    tracker.record_call(model="m", prompt_tokens=10, completion_tokens=5, stage_name="s")
    tracker.record_call(model="m", prompt_tokens=10, completion_tokens=5,
                        stage_name="s", modality="неизвестное")

    summary = tracker.get_summary()
    assert summary["by_modality"] == {"text": 2, "vision": 1}


# =========================================================================
# P2.7: честность телеметрии и запрет вложенного захвата
# =========================================================================
def test_token_tracker_summary_survives_deque_eviction():
    """P2.7: сводка кумулятивна — вытеснение записей из deque не занижает
    итоги (раньше total_calls считался как len(records))."""
    tracker = TokenUsageTracker(max_records=2)
    for i in range(5):
        tracker.record_call(
            model="m",
            prompt_tokens=10,
            completion_tokens=5,
            latency_sec=1.0,
            is_cache_hit=(i == 0),
            stage_name="extraction",
        )

    assert len(tracker.records) == 2  # deque вытеснил 3 записи
    summary = tracker.get_summary()
    assert summary["total_calls"] == 5
    assert summary["cache_hits"] == 1
    assert summary["real_calls"] == 4
    assert summary["prompt_tokens"] == 50
    assert summary["completion_tokens"] == 25
    assert summary["total_tokens"] == 75
    assert summary["total_latency_sec"] == 5.0
    assert summary["by_stage"] == {
        "extraction": {"calls": 5, "total_tokens": 75, "latency_sec": 5.0}
    }


def test_rate_limiter_rejects_nested_with():
    """P2.7: вложенный 'with' не съедает второй слот параллелизма —
    это явная ошибка кода, а не «перегрузка VLM»."""
    limiter = RateLimiter(rpm_limit=60, max_concurrency=2, enabled=True, timeout=0.1)
    with limiter:
        with pytest.raises(RuntimeError, match="вложенный"):
            with limiter:
                pass  # pragma: no cover — сюда не доходим
    assert limiter._semaphore._value == 2
