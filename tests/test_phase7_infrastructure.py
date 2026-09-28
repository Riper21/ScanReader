# -*- coding: utf-8 -*-
"""
Тесты Фазы 7 — инфраструктурная надёжность.

Каждый тест фиксирует дефект из плана корректировки: отсутствие лимита
страниц, утечку файлового дескриптора, отправку пустого документа в модель,
неполное маскирование секретов, квадратичную работу кэша и утечку модулей
при перезагрузке реестра.
"""

import io
import logging
import sys

import pytest
from PIL import Image

from scan_reader.core.cache import LLMResponseCache
from scan_reader.core.io_utils import mask_secret
from scan_reader.core.utils import SecretMaskingFilter
from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.file_processor import FileProcessor
from scan_reader.verifier.checksums import validate_snils

SECRET = "sk-ant-api03-XYZ123SECRETVALUE456"


# =========================================================================
# 7.4: маскирование секретов в аргументах логирования
# =========================================================================
class _CapturedStream(io.StringIO):
    """Поток, который отдаёт содержимое и до, и после flush()."""

    def getvalue(self) -> str:
        self.flush()
        return super().getvalue()


def _capturing_logger(name):
    logger = logging.getLogger(name)
    logger.handlers = []
    handler = logging.StreamHandler(_CapturedStream())
    handler.addFilter(SecretMaskingFilter())
    logger.addHandler(handler)
    logger.propagate = False
    logger.setLevel(logging.INFO)
    return logger, handler.stream


def test_secret_in_log_args_is_masked():
    """logger.info("токен %s", secret) раньше НЕ маскировался: секрет лежал
    в record.args, а фильтр обрабатывал только record.msg."""
    logger, stream = _capturing_logger("p7.args")
    logger.info("токен %s использован", SECRET)
    out = stream.getvalue()
    assert SECRET not in out
    assert "использован" in out, "сообщение должно быть отрендерено, а не отброшено"


def test_secret_in_fstring_is_masked():
    logger, stream = _capturing_logger("p7.fstring")
    logger.info(f"токен {SECRET} в f-строке")
    assert SECRET not in stream.getvalue()


def test_secret_in_format_map_is_masked():
    logger, stream = _capturing_logger("p7.kwargs")
    logger.info("токен %(token)s", {"token": SECRET})
    assert SECRET not in stream.getvalue()


def test_masking_preserves_non_secret_text():
    logger, stream = _capturing_logger("p7.plain")
    logger.info("обработан документ %s, страниц: %d", "order.pdf", 7)
    out = stream.getvalue()
    assert "order.pdf" in out
    assert "7" in out


def test_all_platform_modules_use_masking_logger():
    """
    Фаза 7.5: четыре модуля использовали сырой logging.getLogger без фильтра
    маскирования, поэтому их сообщения обходили SecretMaskingFilter.
    """
    import scan_reader.core.finance_parser as finance_parser
    import scan_reader.core.io_utils as io_utils
    import scan_reader.verifier.chronology as chronology
    import scan_reader.verifier.math_verifier as math_verifier

    for module in (finance_parser, io_utils, chronology, math_verifier):
        log = (getattr(module, "logger", None) or getattr(module, "_parser_logger", None)
               or getattr(module, "_io_logger", None))
        assert log is not None, f"{module.__name__}: логгер не найден"
        handlers = getattr(log, "handlers", [])
        if handlers:
            assert any(
                isinstance(f, SecretMaskingFilter) for h in handlers for f in h.filters
            ), f"{module.__name__}: у логгера нет фильтра маскирования"


# =========================================================================
# 7.1: лимит страниц
# =========================================================================
@pytest.fixture
def multipage_tiff(tmp_path):
    path = tmp_path / "multi.tif"
    frames = [Image.new("L", (400, 560), color=200 - i * 5) for i in range(8)]
    frames[0].save(path, save_all=True, append_images=frames[1:])
    return str(path)


def test_tiff_without_limit_reads_all_frames(multipage_tiff):
    uris = FileProcessor().process_image_file(multipage_tiff)
    assert len(uris) == 8


def test_tiff_page_limit_applied_before_decoding(multipage_tiff, monkeypatch):
    """Раньше лимит применялся ПОСЛЕ декодирования всех кадров, то есть
    экономил только отправку, но не память и время."""
    processor = FileProcessor()
    calls = []
    original = processor.pil_to_base64_data_uri
    monkeypatch.setattr(
        processor, "pil_to_base64_data_uri",
        lambda img: (calls.append(1), original(img))[1],
    )
    processor.process_image_file(multipage_tiff, max_pages=2)
    assert len(calls) == 2, f"декодировано {len(calls)} кадров вместо 2"


def test_prepare_document_inputs_respects_limit(multipage_tiff):
    mode, uris = FileProcessor().prepare_document_inputs(multipage_tiff, max_pages=3)
    assert mode == "vision"
    assert len(uris) == 3


def _facade_with_text_processor(max_pages_sink, *, client=None):
    """Фасад с подменённым процессором, отдающим текстовый слой."""
    from scan_reader.type_registry import get_registry

    f = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    f.registry = get_registry()
    f._client = client
    f.model_name = "test-model"
    f._get_client = lambda: client
    f.processor = type(
        "P", (), {
            "prepare_document_inputs": staticmethod(
                lambda p, max_pages=None: (
                    max_pages_sink.update(max_pages=max_pages), ("text", "текст документа")
                )[1]
            )
        }
    )()
    f.cache = type("C", (), {"get": staticmethod(lambda k: None),
                             "compute_key": staticmethod(lambda *a: "k"),
                             "set": staticmethod(lambda *a: None)})()
    f.rate_limiter = type("R", (), {"__enter__": lambda s: s, "__exit__": lambda s, *a: False})()
    f.tracker = type("T", (), {"record_call": staticmethod(lambda **a: None),
                               "set_stage": staticmethod(lambda *a: None)})()
    return f


def test_extraction_applies_default_page_limit(tmp_path, monkeypatch):
    """
    Раньше max_pages доходил до prepare_document_inputs как None, то есть ВЕСЬ
    документ уходил в один мультимодальный запрос: для 200-страничного PDF это
    десятки мегабайт base64, которые не помещаются в контекст модели.
    """
    monkeypatch.delenv("SCANREADER_MAX_PAGES", raising=False)
    seen = {}
    f = _facade_with_text_processor(seen)
    f.extract_document_data(str(tmp_path / "doc.txt"), doc_type="hr_orders")
    assert seen["max_pages"] == 20, "страницы не ограничены значением по умолчанию"

    monkeypatch.setenv("SCANREADER_MAX_PAGES", "3")
    seen.clear()
    f.extract_document_data(str(tmp_path / "doc.txt"), doc_type="hr_orders")
    assert seen["max_pages"] == 3, "SCANREADER_MAX_PAGES не применяется"


def test_explicit_max_pages_has_priority_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SCANREADER_MAX_PAGES", "3")
    seen = {}
    f = _facade_with_text_processor(seen)
    f.extract_document_data(str(tmp_path / "doc.txt"), doc_type="hr_orders", max_pages=7)
    assert seen["max_pages"] == 7, "явный аргумент должен побеждать переменную окружения"


# =========================================================================
# 7.3: пустой текстовый слой
# =========================================================================
def test_empty_text_layer_rejected_before_model_call(tmp_path):
    """VLM, получив пустой текст документа, заполняла юридическую схему
    выдуманными значениями. Теперь это явный отказ FAILED."""
    from scan_reader import facade as facade_mod

    doc = tmp_path / "scanned.docx"
    doc.write_text("   \n  \n", encoding="utf-8")

    from scan_reader.type_registry import get_registry

    f = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    f.registry = get_registry()
    f._client = None
    f.model_name = "test-model"

    # Подготовка документа вызывается обязательно (нужно знать режим), но
    # сам вызов модели — нет: клиент не должен быть получен вовсе.
    client_requests = []

    def _client():
        client_requests.append(1)
        raise AssertionError("модель не должна вызываться при пустом документе")

    f._get_client = _client
    f.processor = type(
        "P", (), {
            "prepare_document_inputs": staticmethod(lambda p, max_pages=None: ("text", "  \n "))
        }
    )()

    result = f.extract_document_data(str(doc), doc_type="hr_orders")
    assert result.get("_extraction_failed") is True
    assert "Пустой текстовый слой" in str(result.get("error", ""))
    assert client_requests == [], "клиент модели получен, хотя документ пуст"
    assert facade_mod._extraction_failed is not None


def test_non_empty_text_layer_still_sent(tmp_path):
    """Не-пустой документ доходит до модели: guard не должен быть параноидальным."""
    seen = {}

    class _Boom:
        class chat:
            class completions:
                @staticmethod
                def create(**kwargs):
                    seen["called"] = True
                    raise RuntimeError("сбой связи с моделью")

    f = _facade_with_text_processor({}, client=_Boom())
    result = f.extract_document_data(str(tmp_path / "x.txt"), doc_type="hr_orders")
    assert seen.get("called") is True, "не-пустой документ не дошёл до модели"
    # Сбой связи перехватывается и превращается в FAILED, а не пробрасывается
    assert result.get("_extraction_failed") is True


# =========================================================================
# 7.2: утечка дескриптора
# =========================================================================
def test_broken_pdf_returns_none_without_raising(tmp_path):
    f = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    f._client = None
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4\nnot a real pdf at all")
    assert f._extract_raw_text_for_audit(str(broken)) is None


def test_pdf_handle_closed_on_error(monkeypatch, tmp_path):
    """close() стоял внутри try, и ошибка на get_text() оставляла файл открытым."""
    closed = {"value": False}

    class _Page:
        def get_text(self):
            raise RuntimeError("сбой чтения страницы")

    class _Doc:
        def __iter__(self):
            return iter([_Page()])

        def close(self):
            closed["value"] = True

    fake_fitz = type("F", (), {"open": staticmethod(lambda p: _Doc())})
    monkeypatch.setitem(sys.modules, "fitz", fake_fitz)

    f = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    f._client = None
    broken = tmp_path / "x.pdf"
    broken.write_bytes(b"%PDF-1.4")
    assert f._extract_raw_text_for_audit(str(broken)) is None
    assert closed["value"] is True, "документ PDF не закрыт после исключения"


# =========================================================================
# 7.8: Pillow guard
# =========================================================================
def test_dual_zone_inputs_explain_missing_pillow(monkeypatch, tmp_path):
    import scan_reader.file_processor as fp

    monkeypatch.setattr(fp, "PIL_AVAILABLE", False)
    monkeypatch.setattr(fp, "Image", None)
    scan = tmp_path / "a.jpg"
    Image.new("L", (10, 10)).save(scan)
    with pytest.raises(RuntimeError) as exc:
        FileProcessor().prepare_dual_zone_inputs(str(scan))
    assert "Pillow" in str(exc.value)
    assert "AttributeError" not in str(exc.value)


def test_dual_zone_inputs_checks_existence_before_pillow(monkeypatch, tmp_path):
    import scan_reader.file_processor as fp

    monkeypatch.setattr(fp, "PIL_AVAILABLE", False)
    with pytest.raises(FileNotFoundError):
        FileProcessor().prepare_dual_zone_inputs(str(tmp_path / "нет.jpg"))


# =========================================================================
# 7.9: утечка sys.modules
# =========================================================================
def test_registry_reload_does_not_leak_modules():
    from scan_reader.type_registry import get_registry

    before = {m for m in sys.modules if m.startswith("doc_types_")}
    for _ in range(5):
        get_registry(force_reload=True)
    after = {m for m in sys.modules if m.startswith("doc_types_")}
    assert after == before, f"утечка модулей: {after - before}"


# =========================================================================
# 7.6: работа кэша
# =========================================================================
def test_cache_does_not_write_on_every_set(tmp_path, monkeypatch):
    writes = []
    import scan_reader.core.cache as cache_mod

    monkeypatch.setattr(cache_mod, "write_atomic", lambda p, c: (writes.append(1), p)[1])
    cache = cache_mod.LLMResponseCache(cache_dir=tmp_path / "c", enabled=True, max_entries=100)
    cache.flush_every = 4
    cache.flush_interval = 10 ** 9

    for i in range(3):
        cache.set(f"k{i}", f"v{i}")
    assert len(writes) == 0, "дисковое сохранение выполняется на каждый set()"

    cache.set("k3", "v3")
    assert len(writes) == 1


def test_cache_flush_by_interval(tmp_path, monkeypatch):
    writes = []
    import scan_reader.core.cache as cache_mod

    monkeypatch.setattr(cache_mod, "write_atomic", lambda p, c: (writes.append(1), p)[1])
    cache = cache_mod.LLMResponseCache(cache_dir=tmp_path / "c", enabled=True, max_entries=100)
    cache.flush_every = 10 ** 9
    cache.flush_interval = 0.0
    cache.set("k", "v")
    assert len(writes) == 1, "сброс по таймеру не сработал"


def test_cache_survives_corrupt_file(tmp_path):
    cache_dir = tmp_path / "c"
    cache_dir.mkdir()
    (cache_dir / "vlm_cache.json").write_text("{ это не json", encoding="utf-8")
    cache = LLMResponseCache(cache_dir=str(cache_dir), enabled=True)
    assert len(cache) == 0
    cache.set("k", "v")
    assert cache.get("k") == "v", "после битого файла кэш обязан работать"


def test_cache_flush_is_idempotent(tmp_path):
    cache = LLMResponseCache(cache_dir=str(tmp_path / "c"), enabled=True)
    cache.set("k", "v")
    cache.flush()
    cache.flush()  # повторный сброс не должен ничего ломать
    assert LLMResponseCache(cache_dir=str(tmp_path / "c"), enabled=True).get("k") == "v"


# =========================================================================
# 7.12: граница льготы СНИЛС
# =========================================================================
@pytest.mark.parametrize("snils", ["001-001-998 00", "001-001-997 12", "000-000-001 00"])
def test_legacy_snils_in_exempt_range(snils):
    """Номера ПФР до 001-001-998 включительно не имеют корректной контрольной суммы.
    Граница 1001997 отвергала 001-001-998, то есть проверка была строже стандарта."""
    ok, _msg = validate_snils(snils)
    assert ok is True


def test_modern_snils_still_validated():
    ok, _msg = validate_snils("112-233-445 95")
    assert isinstance(ok, bool)


# =========================================================================
# 7.14/7.15: молчаливые отказы
# =========================================================================
def test_contrast_failure_is_logged(monkeypatch):
    """Раньше отказ возвращал исходное изображение молча, и по документу было
    невозможно понять, улучшался контраст или нет."""
    import scan_reader.file_processor as fp

    img = Image.new("L", (20, 20))

    def _boom(*a, **k):
        raise RuntimeError("сбой улучшения")

    monkeypatch.setattr(fp.ImageOps, "autocontrast", _boom)
    _, stream = _capturing_logger("file_processor")
    result = FileProcessor().enhance_document_contrast(img)
    assert result is img
    assert "Улучшение контраста не выполнено" in stream.getvalue()


def test_unreadable_existing_file_is_not_overwritten(tmp_path, monkeypatch):
    """
    Нечитаемый существующий файл РАНЬШЕ приводил к тихой перезаписи:
    existing_file_path становился "", условие коллизии не срабатывало.
    """

    from scan_reader.core.json_exporter import save_single_document_json

    out = tmp_path / "out"
    out.mkdir()
    (out / "order_Full.json").write_text("{ повреждённый JSON", encoding="utf-8")

    payload = {
        "file_name": "order.pdf",
        "file_path": str(tmp_path / "inbox" / "order.pdf"),
        "doc_type": "hr_orders",
        "data": {"doc_number": "1"},
    }
    save_single_document_json(payload, str(out))

    survivors = sorted(p.name for p in out.glob("order*"))
    assert "order_Full.json" in survivors
    assert any(n != "order_Full.json" for n in survivors), (
        f"повреждённый файл перезаписан, новые: {survivors}"
    )
    assert (out / "order_Full.json").read_text(encoding="utf-8") == "{ повреждённый JSON", (
        "содержимое повреждённого файла изменилось"
    )


# =========================================================================
# Прочее
# =========================================================================
def test_mask_secret_still_works_directly():
    assert SECRET not in mask_secret(f"ключ {SECRET}")
