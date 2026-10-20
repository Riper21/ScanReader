# -*- coding: utf-8 -*-
"""
Каскадный роутинг экстракции «быстрая → мощная» (VLM_FAST_MODEL_NAME).

Экономика: быстрая модель (~30-40 с/документ) обрабатывает документ первой;
при провале барьеров Уровня 1 (парсинг JSON, схема плагина, заглушка doc_type,
финансовый regex-восстановитель) документ полностью переэкстрактируется
основной моделью с нуля. Пустая переменная — одно-модельный режим,
поведение идентично прежнему (обратная совместимость).

Транспортные сбои эскалацией не считаются: эндпоинт общий, основная модель
упала бы так же, а двойная стоимость бессмысленна.
"""

import json
import types

from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.verifier.status import (
    VerificationIssue,
    VerificationReport,
    VerificationStatus,
)


FAST = "fast-moe-test-model"
PRIMARY = "primary-dense-test-model"

GOOD_FAST = json.dumps({"doc_date": "15.01.2023", "doc_number": "42"}, ensure_ascii=False)
GOOD_PRIMARY = json.dumps({"doc_date": "15.01.2023", "doc_number": "77"}, ensure_ascii=False)

SALARY_DOC_TYPE = "Постановление об обращении взыскания на заработную плату и иные доходы должника"


# =========================================================================
# Поддельный OpenAI-совместимый клиент с диспетчеризацией по kwarg model=
# =========================================================================
class _Usage:
    prompt_tokens = 100
    completion_tokens = 20
    total_tokens = 120


class _Message:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Message(content)


class _Response:
    def __init__(self, content):
        self.choices = [_Choice(content)]
        self.usage = _Usage()


class DualModelClient:
    """Ответ выбирается по kwarg model=; журнал вызовов — в .models."""

    def __init__(self, fast_reply, primary_reply):
        self.fast_reply = fast_reply
        self.primary_reply = primary_reply
        self.models = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        model = kwargs.get("model")
        self.models.append(model)
        if model == FAST:
            reply = self.fast_reply
        elif model == PRIMARY:
            reply = self.primary_reply
        else:
            raise AssertionError(f"Неожиданная модель в вызове VLM: {model!r}")
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)


class _NoThrottle:
    """Отключает общий RateLimiter, чтобы тесты не спали между вызовами."""

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _facade(monkeypatch, tmp_path, fast="", cache=False) -> LegalDocPlatformFacade:
    """Фасад с детерминированным окружением и изолированными каталогами."""
    monkeypatch.setenv("AI_MODEL_NAME", PRIMARY)
    monkeypatch.setenv("VLM_FAST_MODEL_NAME", fast)
    monkeypatch.setenv("SCANREADER_OUTPUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("SCANREADER_CACHE_DIR", str(tmp_path / "cache"))
    # Изоляция бенчмарка: без этого evaluate_dataset может найти реальный
    # ground truth репозитория, и метрики перестанут быть детерминированными.
    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", str(tmp_path / "gt"))
    facade = LegalDocPlatformFacade(enable_cache=cache)
    facade.rate_limiter = _NoThrottle()
    return facade


def _txt(tmp_path, name="doc.txt", text="Постановление от 15.01.2023 № 42."):
    doc = tmp_path / name
    doc.write_text(text, encoding="utf-8")
    return str(doc)


# =========================================================================
# Одно-модельный режим (env пуст) — обратная совместимость
# =========================================================================
def test_env_off_single_model_no_cascade(tmp_path, monkeypatch):
    """Пустая VLM_FAST_MODEL_NAME: один вызов основной модели, каскада нет."""
    facade = _facade(monkeypatch, tmp_path, fast="")
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client

    result = facade.extract_document_data(_txt(tmp_path), doc_type="hr_orders")

    assert client.models == [PRIMARY]
    assert result["_extraction_model"] == PRIMARY
    assert "_escalated_to_primary" not in result


def test_env_off_cache_hit_uses_primary_key(tmp_path, monkeypatch):
    """Повторный прогон в одно-модельном режиме идёт в кэш, а не в модель."""
    facade = _facade(monkeypatch, tmp_path, fast="", cache=True)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    path = _txt(tmp_path, "cache_off.txt")

    first = facade.extract_document_data(path, doc_type="hr_orders")
    second = facade.extract_document_data(path, doc_type="hr_orders")

    assert client.models == [PRIMARY], "кэш-хит не должен звать модель повторно"
    assert first["_extraction_model"] == PRIMARY
    assert second["_extraction_model"] == PRIMARY


# =========================================================================
# Каскад: чистый проход быстрой модели
# =========================================================================
def test_fast_clean_pass_no_escalation(tmp_path, monkeypatch):
    """Схема чиста — основная модель не дёргается вовсе, маркер каскада снят."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client

    result = facade.extract_document_data(_txt(tmp_path), doc_type="hr_orders")

    assert client.models == [FAST]
    assert result["_extraction_model"] == FAST
    assert result.get("doc_number") == "42"
    assert "_escalated_to_primary" not in result


# =========================================================================
# Уровень 1: триггеры эскалации
# =========================================================================
def test_escalate_on_unparsed_raw_reply(tmp_path, monkeypatch):
    """Быстрая модель не выдала JSON — полная переэкстракция основной."""
    monkeypatch.setenv("SCANREADER_VLM_ATTEMPTS", "1")
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient("Извините, не могу разобрать документ.", GOOD_PRIMARY)
    facade._client = client

    result = facade.extract_document_data(_txt(tmp_path, "raw.txt"), doc_type="hr_orders")

    assert client.models == [FAST, PRIMARY]
    assert result["_extraction_model"] == PRIMARY
    assert result["_escalated_to_primary"] is True
    assert result["_escalated_from"] == FAST
    assert "raw_reply_unparsed" in result["_escalation_reason"]
    assert result.get("doc_number") == "77"


def test_escalate_on_schema_failure(tmp_path, monkeypatch):
    """JSON валиден, но нарушает схему плагина (отрицательная сумма)."""
    fast_reply = json.dumps(
        {"doc_type": SALARY_DOC_TYPE, "finances": {"total_deduction_rub": -150000.0}},
        ensure_ascii=False,
    )
    primary_reply = json.dumps({"doc_type": SALARY_DOC_TYPE}, ensure_ascii=False)
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(fast_reply, primary_reply)
    facade._client = client

    result = facade.extract_document_data(
        _txt(tmp_path, "schema.txt"), doc_type="salary_deductions"
    )

    assert client.models == [FAST, PRIMARY]
    assert result["_escalated_to_primary"] is True
    assert result["_escalation_reason"] == "schema_failed"
    assert result.get("schema_validated") is not False


def test_escalate_on_doc_type_placeholder(tmp_path, monkeypatch):
    """Заглушка doc_type в ответе быстрой модели (инцидент Б2) — эскалация."""
    fast_reply = json.dumps({"doc_type": "unknown", "case_number": "2-1234/2020"}, ensure_ascii=False)
    primary_reply = json.dumps(
        {"doc_type": "Исполнительный лист", "case_number": "2-1234/2020"}, ensure_ascii=False
    )
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(fast_reply, primary_reply)
    facade._client = client

    result = facade.extract_document_data(
        _txt(tmp_path, "placeholder.txt"), doc_type="executive_documents"
    )

    assert client.models == [FAST, PRIMARY]
    assert result["_escalated_to_primary"] is True
    assert "doc_type_placeholder" in result["_escalation_reason"]
    assert result["doc_type"] == "Исполнительный лист"
    assert "_doc_type_normalized" not in result


def test_escalate_on_regex_recovery(tmp_path, monkeypatch):
    """Суммы восстановил regex, а не модель — быстрый ответ не эталон."""
    fast_reply = json.dumps(
        {"doc_date": "17.06.2015", "finances": {"total_rub": None}}, ensure_ascii=False
    )
    primary_reply = json.dumps(
        {
            "doc_date": "17.06.2015",
            "claim_subject": "Задолженность",
            "finances": {"main_debt_rub": 150000.0, "total_rub": 150000.0},
        },
        ensure_ascii=False,
    )
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(fast_reply, primary_reply)
    facade._client = client

    result = facade.extract_document_data(
        _txt(tmp_path, "recovery.txt", "Взыскать 150 000,00 рублей."),
        doc_type="enforcement_orders",
    )

    assert client.models == [FAST, PRIMARY]
    assert result["_escalated_to_primary"] is True
    assert "regex_recovery" in result["_escalation_reason"]


# =========================================================================
# Политики: транспорт и вырожденная конфигурация
# =========================================================================
def test_transport_failure_not_escalated(tmp_path, monkeypatch):
    """Сбой сети/таймаут — свойство эндпоинта, а не модели: переэкстракция
    основной моделью на том же эндпоинте дала бы тот же исход."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(Exception("VLM call failed: connection refused"), GOOD_PRIMARY)
    facade._client = client

    result = facade.extract_document_data(_txt(tmp_path, "transport.txt"), doc_type="hr_orders")

    assert client.models == [FAST], "транспортный сбой не должен звать основную модель"
    assert result["_extraction_failed"] is True
    assert "VLM call failed" in str(result.get("error", ""))
    assert "_escalated_to_primary" not in result


def test_degenerate_config_disabled(tmp_path, monkeypatch):
    """VLM_FAST_MODEL_NAME == AI_MODEL_NAME — каскад выключен, один вызов."""
    facade = _facade(monkeypatch, tmp_path, fast=PRIMARY)
    assert facade.fast_model_name is None
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client

    result = facade.extract_document_data(_txt(tmp_path, "degenerate.txt"), doc_type="hr_orders")

    assert client.models == [PRIMARY]
    assert "_escalated_to_primary" not in result


# =========================================================================
# Кэш: изоляция моделей и непопадание мусора
# =========================================================================
def test_cache_isolation_between_models(tmp_path, monkeypatch):
    """Ключ кэша содержит модель: запись быстрой не выдаётся за ответ основной."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST, cache=True)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    path = _txt(tmp_path, "iso.txt")

    by_fast = facade.extract_document_data(path, doc_type="hr_orders")
    assert client.models == [FAST]
    assert by_fast["doc_number"] == "42"

    # Оператор выключил быструю модель: основная обязана промахнуться по
    # кэш-записи быстрой и посчитать свой ответ.
    facade.fast_model_name = None
    by_primary = facade.extract_document_data(path, doc_type="hr_orders")
    assert client.models == [FAST, PRIMARY], "ключи кэша моделей обязаны различаться"
    assert by_primary["doc_number"] == "77"
    assert by_primary["_extraction_model"] == PRIMARY

    # Оба ответа закэшированы под своими ключами: третий прогон без вызовов.
    facade.extract_document_data(path, doc_type="hr_orders")
    assert client.models == [FAST, PRIMARY]


def test_failed_fast_reply_not_cached(tmp_path, monkeypatch):
    """Провальный ответ быстрой модели не закрепляется в кэше (P0.6):
    повторный прогон снова пытает быструю модель, а не достаёт мусор."""
    monkeypatch.setenv("SCANREADER_VLM_ATTEMPTS", "1")
    facade = _facade(monkeypatch, tmp_path, fast=FAST, cache=True)
    client = DualModelClient("не могу разобрать документ", GOOD_PRIMARY)
    facade._client = client
    path = _txt(tmp_path, "nocache.txt")

    first = facade.extract_document_data(path, doc_type="hr_orders")
    second = facade.extract_document_data(path, doc_type="hr_orders")

    assert first.get("doc_number") == "77"
    assert second.get("doc_number") == "77"
    # 1-й прогон: fast + primary; 2-й: fast повторно (мусор не в кэше),
    # primary — из кэша.
    assert client.models == [FAST, PRIMARY, FAST]


# =========================================================================
# Уровень 2: эскалация по ошибкам Zero-Trust в process_single_document
# =========================================================================
class _FakeAuditor:
    """
    Zero-Trust аудит по таблице кодов: коды ошибок для ответа быстрой модели
    против кодов для ответа основной. Журнал аудитов — в .audited_models.
    """

    def __init__(self, fast=None, primary=None, warning=False):
        self.fast = list(fast or [])
        self.primary = list(primary or [])
        self.warning = warning
        self.audited_models = []

    def audit_document(self, data=None, **kwargs):
        model = (data or {}).get("_extraction_model")
        self.audited_models.append(model)
        codes = self.fast if model == FAST else self.primary
        severity = "warning" if self.warning else "error"
        if self.warning:
            status = VerificationStatus.PARTIALLY_VERIFIED
        elif codes:
            status = VerificationStatus.DISCREPANCY_DETECTED
        else:
            status = VerificationStatus.ZERO_TRUST_VERIFIED
        issues = [VerificationIssue(severity, c, f"инцидент {c}", "pipeline") for c in codes]
        return VerificationReport(status=status, is_valid=not codes, issues=issues, details={})


def test_l2_escalation_on_zt_error(tmp_path, monkeypatch):
    """Быстрая модель прошла Уровень 1, но Zero-Trust нашёл ERROR-коды:
    полная переэкстракция основной моделью, финальный data — от неё."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    auditor = _FakeAuditor(fast=["INN_CHECKSUM_FAILED", "AMOUNT_MISMATCH"])
    facade.auditor = auditor

    result = facade.process_single_document(
        _txt(tmp_path, "l2_ok.txt"), doc_type="hr_orders", update_registries=False
    )

    assert client.models == [FAST, PRIMARY]
    assert auditor.audited_models == [FAST, PRIMARY]
    assert result["extraction_model"] == PRIMARY
    assert result["escalated"] is True
    assert "INN_CHECKSUM_FAILED" in result["escalation_reason"]
    assert "AMOUNT_MISMATCH" in result["escalation_reason"]
    assert result["data"]["doc_number"] == "77"
    assert result["data"]["_escalated_from"] == FAST
    assert result["status"] == "COMPLETED"


def test_l2_warning_does_not_escalate(tmp_path, monkeypatch):
    """WARNING-уровень выдаст и основная модель: двойная стоимость без
    выигрыша — переэкстракция не запускается."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    auditor = _FakeAuditor(fast=["DATE_AMBIGUOUS"], warning=True)
    facade.auditor = auditor

    result = facade.process_single_document(
        _txt(tmp_path, "l2_warn.txt"), doc_type="hr_orders", update_registries=False
    )

    assert client.models == [FAST], "WARNING не должен звать основную модель"
    assert auditor.audited_models == [FAST]
    assert result["escalated"] is False
    assert result["extraction_model"] == FAST


def test_l2_primary_strictly_worse_keeps_fast(tmp_path, monkeypatch):
    """Основная модель ответила строго хуже (3 ошибки против 1): остаётся
    ответ быстрой, попытка честно помечается в data._escalation_l2_*."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    auditor = _FakeAuditor(fast=["A_CODE"], primary=["A_CODE", "B_CODE", "C_CODE"])
    facade.auditor = auditor

    result = facade.process_single_document(
        _txt(tmp_path, "l2_worse.txt"), doc_type="hr_orders", update_registries=False
    )

    assert client.models == [FAST, PRIMARY]
    assert result["escalated"] is False
    assert result["extraction_model"] == FAST
    assert result["data"]["doc_number"] == "42"
    assert result["data"]["_escalation_l2_attempted"] is True
    assert "хуже" in result["data"]["_escalation_l2_reason"]


def test_l2_tie_prefers_primary(tmp_path, monkeypatch):
    """Одинаковый счёт ошибок (ничья) — финален ответ основной модели:
    она заявленный эталон оператора."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    auditor = _FakeAuditor(fast=["A_CODE"], primary=["B_CODE"])
    facade.auditor = auditor

    result = facade.process_single_document(
        _txt(tmp_path, "l2_tie.txt"), doc_type="hr_orders", update_registries=False
    )

    assert result["escalated"] is True
    assert result["extraction_model"] == PRIMARY
    assert result["data"]["doc_number"] == "77"


def test_l2_primary_transport_failure_keeps_fast(tmp_path, monkeypatch):
    """Основная модель не смогла переэкстрактировать (транспорт): остаётся
    ответ быстрой с ошибками аудита, попытка помечена."""
    facade = _facade(monkeypatch, tmp_path, fast=FAST)
    client = DualModelClient(GOOD_FAST, Exception("VLM call failed: timeout"))
    facade._client = client
    auditor = _FakeAuditor(fast=["A_CODE"])
    facade.auditor = auditor

    result = facade.process_single_document(
        _txt(tmp_path, "l2_crash.txt"), doc_type="hr_orders", update_registries=False
    )

    assert client.models == [FAST, PRIMARY]
    assert result["escalated"] is False
    assert result["extraction_model"] == FAST
    assert result["data"]["_escalation_l2_attempted"] is True
    assert "не ответила" in result["data"]["_escalation_l2_reason"]
    # Ошибки Zero-Trust никуда не делись: документ требует человека.
    assert result["status"] == "NEEDS_REVIEW"


def test_l2_env_off_single_model_no_escalation(tmp_path, monkeypatch):
    """Одно-модельный режим: ошибки Zero-Trust не запускают вторую модель —
    её нет, переэкстракция тем же ответом бессмысленна."""
    facade = _facade(monkeypatch, tmp_path, fast="")
    client = DualModelClient(GOOD_FAST, GOOD_PRIMARY)
    facade._client = client
    auditor = _FakeAuditor(fast=["A_CODE"], primary=["A_CODE"])
    facade.auditor = auditor

    result = facade.process_single_document(
        _txt(tmp_path, "l2_off.txt"), doc_type="hr_orders", update_registries=False
    )

    assert client.models == [PRIMARY], "эскалации в одно-модельном режиме быть не должно"
    assert auditor.audited_models == [PRIMARY]
    assert result["escalated"] is False
    assert result["status"] == "NEEDS_REVIEW"


# =========================================================================
# Сводки запуска: статистика каскада в run_metrics_summary
# =========================================================================
def test_escalation_stats_and_run_summary_section(tmp_path):
    """Правило 8: сводка считает обе стороны — замены и неудачные попытки;
    сбои без data из знаменателя исключены, секция опускается при None."""
    from scan_reader.core.metrics_evaluator import (
        export_run_summary_markdown,
        generate_run_summary,
        summarize_escalations,
    )

    results = [
        {"status": "COMPLETED", "data": {"_extraction_model": FAST}, "escalated": True},
        {"status": "COMPLETED", "data": {"_extraction_model": FAST, "_escalation_l2_attempted": True}},
        {"status": "COMPLETED", "data": {"_extraction_model": FAST}},
        {"status": "FAILED", "data": {}},
        {"status": "FAILED"},
    ]
    stats = summarize_escalations(results)
    assert stats["documents_with_extraction"] == 3
    assert stats["escalated_to_primary"] == 1
    assert stats["l2_attempts_kept_fast"] == 1
    assert stats["second_model_runs"] == 2
    assert stats["escalation_rate_percent"] == 33.3

    cat = {"hr_orders": {"documents": [], "average_quality_score_percent": 50.0,
                         "total_documents": 1, "status_counts": {"excellent": 1}}}
    summary_with = generate_run_summary(cat, escalation_stats=stats)
    assert summary_with["escalation"] == stats
    summary_without = generate_run_summary(cat)
    assert "escalation" not in summary_without

    md_path = str(tmp_path / "summary.md")
    export_run_summary_markdown(summary_with, md_path)
    md_text = open(md_path, encoding="utf-8").read()
    assert "Каскад экстракции" in md_text
    assert "Запусков основной модели: **2**" in md_text
    assert "33.3%" in md_text


def test_stage5_prints_cascade_line(tmp_path, monkeypatch, capsys):
    """Этап 5 печатает строку каскада только при настроенной быстрой модели."""
    from scan_reader.type_registry import get_registry

    for fast, expected in ((FAST, True), (None, False)):
        facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
        facade.registry = get_registry()
        facade.results_dir = str(tmp_path)
        facade.fast_model_name = fast
        results = [{
            "doc_type": "hr_orders", "status": "COMPLETED",
            "file_name": "a.pdf", "data": {"x": 1}, "escalated": True,
        }]
        facade._batch_stage5_metrics({"hr_orders": ["a.pdf"]}, results)
        out = capsys.readouterr().out
        assert ("Каскад моделей" in out) is expected
        # Сводка пишется в обоих режимах: нули в одно-модельном режиме —
        # правда, а не «не измерено».
        summary = json.loads((tmp_path / "run_metrics_summary.json").read_text(encoding="utf-8"))
        assert summary["escalation"]["escalated_to_primary"] == 1


# =========================================================================
# Doctor: быстрая модель в диагностике
# =========================================================================
class _FakeUsage:
    prompt_tokens = 10
    completion_tokens = 5
    total_tokens = 15


class _FakeResp:
    def __init__(self):
        self.choices = [types.SimpleNamespace(message=types.SimpleNamespace(content="ok"))]
        self.usage = _FakeUsage()


class _FakeOpenAI:
    """Клиент OpenAI с журналом моделей;FAST может подниматься ошибкой."""

    fail_on = None
    journal: list = []

    def __init__(self, **kwargs):
        pass

    @property
    def chat(self):
        outer = self

        class _Completions:
            @staticmethod
            def create(**kwargs):
                model = kwargs.get("model")
                _FakeOpenAI.journal.append(model)
                if outer.fail_on == model:
                    raise RuntimeError(f"model {model} not found")
                return _FakeResp()

        return types.SimpleNamespace(completions=_Completions())


def _run_doctor(monkeypatch, fast, fail_on=None):
    from scan_reader.core import diagnostics

    _FakeOpenAI.fail_on = fail_on
    _FakeOpenAI.journal = []
    monkeypatch.setattr("openai.OpenAI", _FakeOpenAI)
    monkeypatch.setattr(
        diagnostics, "check_network_ping", lambda url: {"status": "ok", "latency_ms": 1}
    )
    monkeypatch.setenv("OPENAI_BASE_URL", "http://fake-gateway/v1")
    monkeypatch.setenv("AI_MODEL_NAME", PRIMARY)
    monkeypatch.setenv("VLM_FAST_MODEL_NAME", fast)
    return diagnostics.run_vlm_diagnostics(verbose=True)


def test_doctor_tests_fast_model_and_reports_it(capsys, monkeypatch):
    """Doctor показывает быструю модель в конфиге и делает тест-вызов ей."""
    report = _run_doctor(monkeypatch, FAST)

    assert report["config"]["fast_model"] == FAST
    assert report["inference_fast"]["status"] == "ok"
    assert report["status"] == "ok"
    assert _FakeOpenAI.journal == [PRIMARY, FAST]
    out = capsys.readouterr().out
    assert FAST in out
    assert "Тестовый запрос к быстрой модели" in out


def test_doctor_fast_model_failure_degrades_to_warning(capsys, monkeypatch):
    """Основная модель жива, быстрая — нет: статус warning, не error:
    конвейер работоспособен в одно-модельном режиме."""
    report = _run_doctor(monkeypatch, FAST, fail_on=FAST)

    assert report["inference"]["status"] == "ok"
    assert report["inference_fast"]["status"] == "error"
    assert report["status"] == "warning"
    assert "одно-модельного режима" in capsys.readouterr().out


def test_doctor_degenerate_config_shows_cascade_off(capsys, monkeypatch):
    """VLM_FAST_MODEL_NAME == AI_MODEL_NAME: каскад выключен, быстрая
    модель не тестируется повторно."""
    report = _run_doctor(monkeypatch, PRIMARY)

    assert report["config"]["fast_model"] == ""
    assert "inference_fast" not in report
    assert report["status"] == "ok"
    assert _FakeOpenAI.journal == [PRIMARY]
    assert "каскад выключен" in capsys.readouterr().out
