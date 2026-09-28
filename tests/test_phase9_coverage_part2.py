# -*- coding: utf-8 -*-
"""
Тесты Фазы 9 (часть 2) — вызовы модели, рендеринг, OCR, конвейер, launcher.

Ключевое: ОБА обращения к VLM в facade.py (роутер и экстракция) никогда не
выполнялись в тестах. Тесты проверяли только чистые функции вокруг них.
"""

import json
import os
import sys
import types

import pytest
from PIL import Image

from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.type_registry import get_registry


# =========================================================================
# Поддельный OpenAI-совместимый клиент
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


class FakeClient:
    """
    Подмена client.chat.completions.create.

    Возвращает заранее заданные ответы по очереди и записывает запросы,
    чтобы тесты могли утверждать, ЧТО ушло в модель.
    """

    def __init__(self, *replies):
        self._replies = list(replies)
        self.requests = []
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create)
        )

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        if not self._replies:
            raise AssertionError("VLM вызван сверх ожидаемого числа раз")
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return _Response(reply)

    @property
    def text_payloads(self):
        """Текстовые блоки всех запросов — для проверки переданного модели текста."""
        out = []
        for req in self.requests:
            for msg in req.get("messages", []):
                content = msg.get("content")
                if isinstance(content, str):
                    out.append(content)
                elif isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            out.append(block.get("text", ""))
        return out


@pytest.fixture
def facade(tmp_path):
    from scan_reader.file_processor import FileProcessor
    from scan_reader.verifier.auditor import ZeroTrustAuditor

    f = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    f.registry = get_registry()
    f.base_url = "http://localhost:0/v1"
    f.api_key = "test-key"
    f.model_name = "test-vlm"
    f._client = None
    f.processor = FileProcessor()
    f.auditor = ZeroTrustAuditor()
    f.results_dir = str(tmp_path / "out")
    os.makedirs(f.results_dir, exist_ok=True)
    f.cache = _NullCache()
    f.tracker = _NullTracker()
    f.rate_limiter = _NullLimiter()
    return f


class _NullCache:
    def __init__(self, value=None):
        self.value = value

    def get(self, key):
        return self.value

    def set(self, key, value):
        self.value = value

    def compute_key(self, *a):
        return "key"


class _NullTracker:
    def record_call(self, **kwargs):
        pass

    def set_stage(self, *a):
        pass


class _NullLimiter:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# =========================================================================
# 9.1 Вызов VLM в роутере
# =========================================================================
def test_router_sends_dual_zone_images_and_parses_json(facade, tmp_path):
    """Фаза 9: роутер ни разу не вызывался с настоящим клиентом."""
    scan = tmp_path / "postanovlenie.jpg"
    Image.new("L", (900, 1200), color=210).save(scan)

    reply = json.dumps({
        "doc_type": "enforcement_orders",
        "confidence": 0.92,
        "issuing_authority": "УФССП России",
        "visual_anchors": "бланк постановления",
        "title_text": "ПОСТАНОВЛЕНИЕ",
    }, ensure_ascii=False)
    client = FakeClient(reply)
    facade._client = client

    doc_type, conf, method = facade.classify_document(str(scan))

    assert doc_type == "enforcement_orders"
    assert 0.0 < conf <= 1.0
    assert method == "vlm_dual_zone_router"
    assert len(client.requests) == 1

    # В модель ушли изображения как data URI
    images = [
        b for b in client.requests[0]["messages"][1]["content"]
        if isinstance(b, dict) and b.get("type") == "image_url"
    ]
    assert images, "роутер должен передавать изображение, а не пустой текст"
    assert images[0]["image_url"]["url"].startswith("data:image/")


def test_router_falls_back_on_model_error(facade, tmp_path):
    """Сбой модели не должен ронять классификацию (Правило 1)."""
    scan = tmp_path / "s.jpg"
    Image.new("L", (400, 500)).save(scan)
    facade._client = FakeClient(TimeoutError("VLM недоступен"))

    doc_type, conf, method = facade.classify_document(str(scan))

    assert doc_type == "unknown"
    assert conf == 0.0
    assert method == "unknown"


def test_router_uses_path_rules_before_calling_model(facade, tmp_path):
    """Эвристика пути должна выигрывать у модели: при совпадении VLM не зовётся."""
    scan = tmp_path / "Приказы_ИП_к_пробам.pdf"
    Image.new("L", (400, 500)).save(scan)
    client = FakeClient()
    facade._client = client

    doc_type, _conf, method = facade.classify_document(str(scan))

    assert method == "heuristic_path"
    assert doc_type == "enforcement_orders"
    assert client.requests == [], "модель не должна вызываться при срабатывании правила пути"


# =========================================================================
# 9.1 Вызов VLM при экстракции
# =========================================================================
def test_extraction_parses_model_json_and_validates(facade, tmp_path):
    """
    Фаза 9: ключевой путь «ответ модели -> JSON -> схема -> кэш» не был покрыт.
    """
    doc = tmp_path / "prikaz.txt"
    doc.write_text("Постановление о взыскании", encoding="utf-8")

    reply = json.dumps({
        "doc_date": "17.06.2015",
        "claim_subject": "Сформировать земельный участок",
        "debtor": {"inn": "7707083893", "name": "Иванов Иван Иванович"},
        "claimant": {"inn": "7802312751", "name": "ООО Ромашка"},
        "finances": {"main_debt_rub": 50000.0, "court_costs_rub": 6000.0, "total_rub": 56000.0},
    }, ensure_ascii=False)
    client = FakeClient(reply)
    facade._client = client

    result = facade.extract_document_data(str(doc), doc_type="enforcement_orders")

    assert result["finances"]["total_rub"] == 56000.0
    assert result["debtor"]["inn"] == "7707083893"
    assert client.requests[0]["temperature"] == 0.0
    # Текст документа реально ушёл в модель
    assert any("Постановление о взыскании" in t for t in client.text_payloads)


def test_extraction_marks_regex_recovered_amounts(facade, tmp_path):
    """Фаза 8.1: эвристическое восстановление помечается, а не применяется молча."""
    doc = tmp_path / "sroki.txt"
    doc.write_text("Взыскать 150 000,00 рублей согласно судебному приказу.", encoding="utf-8")

    # Модель не вернула сумму - её должен восстановить regex
    reply = json.dumps({
        "doc_date": "17.06.2015",
        "claim_subject": "Задолженность",
        "debtor": {"inn": "7707083893", "name": "Иванов"},
        "finances": {"main_debt_rub": None, "total_rub": None},
    }, ensure_ascii=False)
    facade._client = FakeClient(reply)

    result = facade.extract_document_data(str(doc), doc_type="enforcement_orders")
    assert result.get("_recovered_by_regex"), "восстановление regex не помечено"


def test_extraction_keeps_raw_reply_when_json_unparsable(facade, tmp_path):
    doc = tmp_path / "x.txt"
    doc.write_text("текст документа", encoding="utf-8")
    facade._client = FakeClient("Извините, я не могу разобрать этот документ.")

    result = facade.extract_document_data(str(doc), doc_type="hr_orders")
    assert result.get("_recovered_by_regex") == ["_raw_reply_unparsed"]
    assert "raw_reply" in result or result.get("_recovered_by_regex")


def test_extraction_uses_cache_on_second_call(facade, tmp_path):
    """Повторный прогон не должен звать модель."""
    doc = tmp_path / "c.txt"
    doc.write_text("текст", encoding="utf-8")
    reply = json.dumps({"doc_date": "15.01.2023", "doc_number": "1"})
    client = FakeClient(reply)
    facade._client = client

    facade.extract_document_data(str(doc), doc_type="hr_orders")
    assert len(client.requests) == 1

    # Кэш теперь отдаёт прошлый ответ
    cached = json.dumps({"doc_date": "15.01.2023", "doc_number": "1"}, ensure_ascii=False)
    facade.cache = _NullCache(cached)
    facade.extract_document_data(str(doc), doc_type="hr_orders")
    assert len(client.requests) == 1, "модель вызвана повторно при попадании в кэш"


def test_extraction_reports_failure_without_client(facade, tmp_path):
    """Фаза 1.4: отсутствие клиента даёт FAILED, а не пустую «успешную» запись."""
    doc = tmp_path / "n.txt"
    doc.write_text("текст", encoding="utf-8")
    facade._client = None
    facade._get_client = lambda: None

    result = facade.extract_document_data(str(doc), doc_type="hr_orders")
    assert result["_extraction_failed"] is True
    assert "LLM client" in str(result.get("error", ""))


def test_extraction_survives_model_exception(facade, tmp_path):
    doc = tmp_path / "e.txt"
    doc.write_text("текст", encoding="utf-8")
    facade._client = FakeClient(TimeoutError("request timed out after 600s"))

    result = facade.extract_document_data(str(doc), doc_type="hr_orders")
    assert result["_extraction_failed"] is True
    assert "timed out" in str(result.get("error", "")).lower()


def test_cache_class_accepts_initial_value():
    cache = _NullCache('{"doc_number": "1"}')
    assert cache.get("key") == '{"doc_number": "1"}'


# =========================================================================
# 9.2 Рендеринг: PDF, TIFF, Dual-Zone
# =========================================================================
def test_tiff_multiframe_produces_data_uris(tmp_path):
    from scan_reader.file_processor import FileProcessor

    path = tmp_path / "m.tif"
    frames = [Image.new("L", (300, 400), color=210 - i * 5) for i in range(3)]
    frames[0].save(path, save_all=True, append_images=frames[1:])

    uris = FileProcessor().process_image_file(str(path))
    assert len(uris) == 3
    assert all(u.startswith("data:image/") for u in uris)


def test_dual_zone_returns_header_and_full(tmp_path):
    """Двухзональный инжест роутера: шапка плюс страница целиком."""
    from scan_reader.file_processor import FileProcessor

    scan = tmp_path / "d.jpg"
    Image.new("L", (1000, 1400), color=200).save(scan)

    mode, payload = FileProcessor().prepare_dual_zone_inputs(str(scan))
    assert mode == "vision_dual_zone"
    assert set(payload) == {"header_uri", "full_uri"}
    assert payload["header_uri"].startswith("data:image/")
    assert payload["full_uri"].startswith("data:image/")


def test_dual_zone_text_mode_for_plain_text(tmp_path):
    from scan_reader.file_processor import FileProcessor

    doc = tmp_path / "t.txt"
    doc.write_text("текст документа", encoding="utf-8")
    mode, payload = FileProcessor().prepare_dual_zone_inputs(str(doc))
    assert mode == "text"
    assert isinstance(payload, str)


def test_process_pdf_file_renders_pages(tmp_path):
    """Рендер PDF через PyMuPDF — ранее не покрывался вовсе."""
    fitz = pytest.importorskip("fitz")

    from scan_reader.file_processor import FileProcessor

    path = tmp_path / "doc.pdf"
    doc = fitz.open()
    for _ in range(2):
        page = doc.new_page()
        page.insert_text((72, 100), "Test page content")
    doc.save(str(path))
    doc.close()

    uris = FileProcessor().process_pdf_file(str(path), max_pages=1)
    assert len(uris) == 1
    assert uris[0].startswith("data:image/")


def test_prepare_document_inputs_rejects_unknown_extension(tmp_path):
    from scan_reader.file_processor import FileProcessor

    bad = tmp_path / "file.xyz"
    bad.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError):
        FileProcessor().prepare_document_inputs(str(bad))


def test_input_size_limit_enforced_by_processor(tmp_path):
    from scan_reader.core.io_utils import MAX_INPUT_BYTES, InputFileError
    from scan_reader.file_processor import FileProcessor

    big = tmp_path / "big.jpg"
    Image.new("L", (10, 10)).save(big)
    with big.open("ab") as fh:
        fh.write(b"\0" * (MAX_INPUT_BYTES + 1))
    with pytest.raises(InputFileError):
        FileProcessor().process_image_file(str(big))


# =========================================================================
# 9.3 OCR: пути исполнения с подменённым бэкендом
# =========================================================================
def test_ocr_reads_rapidocr_output(monkeypatch, tmp_path):
    """
    Фаза 9: core/ocr.py был на 39 % - ветки разбора ответа бэкенда
    не исполнялись ни разу.
    """
    from scan_reader.core import ocr as ocr_mod

    scan = tmp_path / "s.jpg"
    Image.new("L", (600, 800), color=200).save(scan)

    class FakeRapid:
        def __call__(self, image):
            return (
                [
                    [[0, 0, 10, 0, 10, 10, 0, 10], "ИНН 7707083893", 0.99],
                    [[0, 20, 10, 20, 10, 30, 0, 30], "Взыскать 150 000 рублей", 0.98],
                ],
                0.42,
            )

    monkeypatch.setattr(ocr_mod.OcrEngine, "_detect_backend", staticmethod(lambda: ("rapidocr", None)))
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=FakeRapid))

    text = ocr_mod.OcrEngine().text(str(scan))
    assert "ИНН 7707083893" in text
    assert "150 000" in text


def test_ocr_reads_paddleocr_output(monkeypatch, tmp_path):
    """Ветка PaddleOCR (GPU-вариант) тоже должна быть проверена."""
    from scan_reader.core import ocr as ocr_mod

    scan = tmp_path / "p.jpg"
    Image.new("L", (600, 800), color=200).save(scan)

    class _Page:
        rec_texts = ["Постановление суда", "Дело 2-1234/2015"]

        class json:
            @staticmethod
            def __call__():
                return {"res": {"rec_texts": ["Постановление суда", "Дело 2-1234/2015"]}}

    class FakePaddle:
        def __init__(self, **kwargs):
            pass

        def predict(self, input=None):
            return [_Page()]

    monkeypatch.setattr(ocr_mod.OcrEngine, "_detect_backend", staticmethod(lambda: ("paddleocr", None)))
    monkeypatch.setitem(sys.modules, "paddleocr", types.SimpleNamespace(PaddleOCR=FakePaddle))

    text = ocr_mod.OcrEngine().text(str(scan))
    assert "Постановление суда" in text
    assert "2-1234/2015" in text


def test_ocr_pdf_prefers_native_text_layer(monkeypatch, tmp_path):
    """Страница с текстовым слоем не должна разбираться OCR заново.

    Текст латиницей: встроенный шрифт PyMuPDF не содержит кириллицы и
    подставляет точки, что делает проверку бессмысленной.
    """
    fitz = pytest.importorskip("fitz")
    from scan_reader.core import ocr as ocr_mod

    path = tmp_path / "text.pdf"
    doc = fitz.open()
    page = doc.new_page()
    native = "Ready-made text layer of the document, long enough to be trusted"
    page.insert_text((72, 100), native)
    doc.save(str(path))
    doc.close()

    class FakeRapid:
        calls = 0

        def __call__(self, image):
            FakeRapid.calls += 1
            return ([[[0, 0, 1, 0, 1, 1, 0, 1], "MUST NOT APPEAR", 1.0]], 0.1)

    monkeypatch.setattr(ocr_mod.OcrEngine, "_detect_backend", staticmethod(lambda: ("rapidocr", None)))
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", types.SimpleNamespace(RapidOCR=FakeRapid))

    text = ocr_mod.OcrEngine().text(str(path))
    assert "Ready-made text layer" in text
    assert "MUST NOT APPEAR" not in text
    assert FakeRapid.calls == 0, "OCR вызван для страницы с готовым текстовым слоем"


def test_ocr_engine_is_lazy_and_singleton():
    from scan_reader.core.ocr import get_ocr_engine, reset_ocr_engine

    reset_ocr_engine()
    a = get_ocr_engine()
    b = get_ocr_engine()
    assert a is b
    assert a._impl is None, "модель не должна грузиться в конструкторе"
    reset_ocr_engine()


# =========================================================================
# 9.4 Конвейер пакетной обработки
# =========================================================================
def test_batch_stage4_continues_after_document_failure(facade, tmp_path, monkeypatch):
    """Правило 1: сбой одного документа не прерывает пакет."""

    good = tmp_path / "good.jpg"
    bad = tmp_path / "bad.jpg"
    Image.new("L", (200, 200)).save(good)
    Image.new("L", (200, 200)).save(bad)

    calls = []

    def _process(path, doc_type=None):
        calls.append(os.path.basename(path))
        if os.path.basename(path) == "bad.jpg":
            raise RuntimeError("сбой обработки")
        return {
            "file_name": os.path.basename(path), "file_path": path, "doc_type": doc_type,
            "status": "COMPLETED", "quality_score_percent": 90.0,
            "zero_trust_status": "zero_trust_verified", "data": {"doc_number": "1"},
        }

    monkeypatch.setattr(facade, "process_single_document", _process)
    monkeypatch.setattr(
        "scan_reader.facade.save_checkpoint", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "scan_reader.facade.clean_checkpoint", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "scan_reader.facade.export_consolidated_registries", lambda *a, **k: {}
    )

    results, errors = facade._batch_stage4_extract({"hr_orders": [str(good), str(bad)]})

    assert len(results) == 2
    assert len(errors) == 1
    assert errors[0]["file"] == "bad.jpg"
    assert "bad.jpg" in calls and "good.jpg" in calls, "обработка не продолжилась после сбоя"


def test_batch_stage3_explicit_type_skips_classification(facade, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        facade, "classify_document",
        lambda p: (calls.append(p), ("enforcement_orders", 0.9, "heuristic_path"))[1],
    )
    docs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]

    grouped = facade._batch_stage3_classify(docs, "hr_orders")
    assert list(grouped) == ["hr_orders"]
    assert calls == [], "явный тип документа не требует классификации"

    grouped = facade._batch_stage3_classify(docs, None)
    assert list(grouped) == ["enforcement_orders"]
    assert len(calls) == 2


def test_batch_stage2_discovers_files_in_directory(tmp_path):

    sub = tmp_path / "incoming"
    sub.mkdir()
    for ext in (".jpg", ".png"):
        Image.new("L", (100, 100)).save(sub / f"doc{ext}")

    found, info = LegalDocPlatformFacade._batch_stage2_discover(str(sub))
    assert len(found) == 2
    assert "Папка" in info


def test_batch_stage2_accepts_explicit_list(tmp_path):
    docs = [str(tmp_path / "a.jpg"), str(tmp_path / "b.jpg")]
    found, info = LegalDocPlatformFacade._batch_stage2_discover(docs)
    assert found == docs
    assert "Список файлов" in info


def test_load_ground_truth_warns_and_returns_none(tmp_path, monkeypatch):
    """Повреждённый эталон обязан быть замечен, а не тихо трактоваться как пустой."""
    from scan_reader import launcher  # noqa: F401  (гарантирует загрузку логгеров)

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    broken = tmp_path / "hr_orders.json"
    broken.write_text("{ не json", encoding="utf-8")
    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", str(tmp_path))

    import io
    import logging

    from scan_reader.core.utils import SecretMaskingFilter, get_logger

    logger = get_logger("facade")
    logger.handlers = []
    handler = logging.StreamHandler(io.StringIO())
    handler.addFilter(SecretMaskingFilter())
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.WARNING)

    result = facade._load_ground_truth("hr_orders")

    assert result is None
    handler.flush()
    assert "Эталон категории" in handler.stream.getvalue()


def test_resolve_incoming_root_prefers_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SCANREADER_INCOMING_DIR", str(tmp_path))
    assert LegalDocPlatformFacade._resolve_incoming_root() == str(tmp_path)


def test_resume_from_checkpoint_skips_processed(facade, tmp_path, monkeypatch):
    """M-13: возобновление не должно переобрабатывать готовые документы."""
    done = tmp_path / "a.jpg"
    pending = tmp_path / "b.jpg"
    Image.new("L", (10, 10)).save(done)
    Image.new("L", (10, 10)).save(pending)

    monkeypatch.setattr(
        "scan_reader.facade.load_checkpoint",
        lambda p: [{"file_name": "a.jpg", "status": "COMPLETED", "doc_type": "hr_orders"}],
    )

    cat_results, results = [], []
    remaining = facade._resume_from_checkpoint(
        str(tmp_path / "ck.json"), [str(done), str(pending)], cat_results, results
    )
    assert remaining == [str(pending)]
    assert len(cat_results) == 1
    assert len(results) == 1


# =========================================================================
# 9.5 launcher: разрешение входного каталога
# =========================================================================
def test_launcher_resolve_input_root(monkeypatch, tmp_path):
    from scan_reader import launcher

    incoming = tmp_path / "incoming"
    incoming.mkdir()
    monkeypatch.setenv("SCANREADER_INCOMING_DIR", str(incoming))
    assert launcher._resolve_input_root() == str(incoming)


def test_launcher_get_version_matches_package():
    """M-01: версия в баннере должна совпадать с __version__ пакета."""
    import scan_reader
    from scan_reader import launcher

    assert launcher._get_version() == scan_reader.__version__


def test_launcher_run_benchmark_suite_creates_report(tmp_path, monkeypatch, capsys):
    """Сводный отчёт бенчмарка должен создаваться и показывать оговорки измерения."""
    from scan_reader import launcher

    gt_dir = tmp_path / "gt"
    gt_dir.mkdir()
    (gt_dir / "hr_orders.json").write_text(
        json.dumps([{"file_name": "a.pdf", "doc_date": "15.01.2023", "doc_number": "1"}],
                   ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", str(gt_dir))
    monkeypatch.setattr(launcher, "ROOT_DIR", str(tmp_path))

    facade = _NullFacade(tmp_path)
    launcher.run_benchmark_suite(facade)

    report = json.loads((tmp_path / "benchmark_metrics_summary.json").read_text(encoding="utf-8"))
    assert report["total_documents_tested"] == 0, "точность посчитана без извлечений"
    assert report["total_documents_without_extraction"] == 1
    out = capsys.readouterr().out
    assert "ИТОГОВАЯ ТОЧНОСТЬ ИЗВЛЕЧЕНИЯ: н/д" in out


class _NullFacade:
    def __init__(self, tmp_path):
        from scan_reader.type_registry import get_registry as _gr

        self.registry = _gr()
        self.results_dir = str(tmp_path)

    def benchmark_against_ground_truth(self, extracted, ground_truth, doc_type):
        return {"accuracy": 100.0, "details": {}}


def test_generate_run_summary_preserves_measurement_caveats():
    """
    Фаза 9.2: сводка запуска ТЕРЯЛА метрики измерения - оператор видел
    «среднее 100%» без сведений о том, мерилось ли что-нибудь против эталона.
    """
    from scan_reader.core.metrics_evaluator import generate_run_summary

    summary = generate_run_summary({
        "hr_orders": {
            "doc_type": "hr_orders",
            "total_documents": 2,
            "average_quality_score_percent": 100.0,
            "status_counts": {"excellent": 2, "high": 0, "satisfactory": 0, "needs_attention": 0},
            "documents_measured_in_benchmark_mode": 1,
            "ground_truth_documents_available": 1,
            "ground_truth_coverage_percent": 100.0,
            "measurement_caveats": {"low_confidence": False},
            "documents": [{"overall_score": 100.0}, {"overall_score": 100.0}],
        }
    })

    assert summary["measurement_caveats"]["documents_measured_against_ground_truth"] == 1
    assert summary["measurement_caveats"]["documents_measured_autonomously"] == 1
    assert summary["measurement_caveats"]["ground_truth_documents_available"] == 1
    assert summary["categories"]["hr_orders"]["documents_measured_in_benchmark_mode"] == 1
    assert summary["overall_accuracy_is_real"] is True


def test_generate_run_summary_flags_unmeasured_accuracy():
    from scan_reader.core.metrics_evaluator import generate_run_summary

    summary = generate_run_summary({
        "hr_orders": {
            "doc_type": "hr_orders",
            "total_documents": 3,
            "average_quality_score_percent": 100.0,
            "status_counts": {"excellent": 3, "high": 0, "satisfactory": 0, "needs_attention": 0},
            "documents_measured_in_benchmark_mode": 0,
            "ground_truth_documents_available": 2,
            "ground_truth_coverage_percent": 0.0,
            "measurement_caveats": {"low_confidence": True},
            "documents": [{"overall_score": 100.0}] * 3,
        }
    })
    assert summary["overall_accuracy_is_real"] is False
    assert summary["measurement_caveats"]["categories_without_measured_benchmark"] == ["hr_orders"]
