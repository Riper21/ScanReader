# -*- coding: utf-8 -*-
"""
OCR-слой для кросс-модальной верификации.

Зачем он нужен. Проект называется ScanReader и принимает на вход сканы
(.jpg/.png/.tiff), но до появления этого модуля в нём не было ни одного OCR-
движка. Эталонный текст для cross-modal гейта извлекался только из
текстового слоя DOCX/TXT/PDF, поэтому ДЛЯ СКАНОВ ГЕЙТ НЕ ВЫПОЛНЯЛСЯ ВОВСЕ.
S-13 закрыл это вторым проходом VLM («точная транскрипция»), но это не OCR:
ошибки распознавания в нём неотличимы от ошибок извлечения, и обе сравниваются
с одной и той же выжимкой.

Настоящий OCR даёт независимый канал: реквизит, подтверждённый и VLM, и OCR,
подтверждён именно на изображении.

Установка (движок опционален, ScanReader работает без него):
    pip install ".[ocr]"

По умолчанию предлагается rapidocr-onnxruntime: тот же движок, что и в
PaddleOCR, но в виде ONNX — CPU-only, ~15 МБ моделей, без 2-ГБ paddle
зависимости. Для максимального качества и GPU доступен PaddleOCR.

Обе реализации дают независимый от VLM текст, поэтому engine выбирается
через SCANREADER_OCR_ENGINE.
"""

from __future__ import annotations

import os
import threading
from typing import Any, List, Optional, Tuple

from .utils import get_logger

logger = get_logger("core.ocr")

DEFAULT_ENGINE = os.getenv("SCANREADER_OCR_ENGINE", "auto").strip().lower()
MAX_PAGES = int(os.getenv("SCANREADER_OCR_MAX_PAGES", "5") or 5)
MIN_TEXT_LENGTH = int(os.getenv("SCANREADER_OCR_MIN_TEXT_LENGTH", "50") or 50)


class OcrUnavailable(RuntimeError):
    """OCR-движок не установлен или не инициализирован."""


class OcrEngine:
    """
    Ленивая обёртка над OCR-движком.

    Экземпляр НЕ создаёт модель в конструкторе: загрузка занимает секунды и
    десятки мегабайт, а во многих запусках (батч по текстовым PDF, проверка
    эталонов, модульные тесты) OCR не нужен вовсе. Модель поднимается при
    первом реальном обращении к text() и кэшируется.
    """

    def __init__(self, engine: Optional[str] = None, lang: str = "ru") -> None:
        self.engine_name = (engine or DEFAULT_ENGINE or "auto").lower()
        self.lang = lang
        self._impl: Optional[Any] = None
        self._impl_name: Optional[str] = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None

    # ------------------------------------------------------------------
    # Доступность
    # ------------------------------------------------------------------
    @staticmethod
    def _detect_backend() -> Tuple[Optional[str], Optional[str]]:
        """
        Определяет доступный бэкенд: rapidocr (ONNX) или paddleocr.

        :returns: (имя, описание ошибки импорта) либо (имя, None).
        """
        try:
            import rapidocr_onnxruntime  # noqa: F401

            return "rapidocr", None
        except ImportError:
            pass
        try:
            import paddleocr  # noqa: F401

            return "paddleocr", None
        except ImportError:
            pass
        return None, (
            "OCR-движок не установлен. Установите дополнительный пакет: "
            'pip install "scan-reader[ocr]" (быстрый CPU-вариант) '
            'или pip install "scan-reader[ocr-full]" (PaddleOCR с GPU).'
        )

    def available(self) -> bool:
        """Доступен ли хотя бы один движок. Не загружает модель."""
        if self._impl is not None:
            return True
        name, _err = self._detect_backend()
        return name is not None

    def backend_name(self) -> Optional[str]:
        """Имя активного бэкенда (или доступного, если модель ещё не поднята)."""
        if self._impl_name:
            return self._impl_name
        name, _err = self._detect_backend()
        return name

    def diagnostics(self) -> dict:
        """Состояние OCR для раздела doctor."""
        name, err = self._detect_backend()
        return {
            "installed": name is not None,
            "backend": self._impl_name or name,
            "requested_engine": self.engine_name,
            "lang": self.lang,
            "initialized": self._impl is not None,
            "max_pages": MAX_PAGES,
            "min_text_length": MIN_TEXT_LENGTH,
            "error": None if name else err,
            "install_hint": 'pip install "scan-reader[ocr]"',
        }

    # ------------------------------------------------------------------
    # Инициализация
    # ------------------------------------------------------------------
    def _ensure_impl(self) -> Any:
        if self._impl is not None:
            return self._impl
        with self._lock:
            if self._impl is not None:
                return self._impl

            available, err = self._detect_backend()
            if available is None:
                self._load_error = err
                raise OcrUnavailable(err or "OCR недоступен")

            try:
                if available == "rapidocr":
                    from rapidocr_onnxruntime import RapidOCR

                    # lang не поддерживается rapidocr: модели мультиязычные
                    self._impl = RapidOCR()
                else:
                    from paddleocr import PaddleOCR

                    self._impl = PaddleOCR(lang=self.lang, show_log=False, use_angle_cls=True)

                self._impl_name = available
                self._load_error = None
                logger.info(f"OCR-движок инициализирован: {available} (lang={self.lang})")
                return self._impl
            except Exception as e:
                self._load_error = f"Не удалось инициализировать OCR ({available}): {e}"
                logger.warning(self._load_error)
                raise OcrUnavailable(self._load_error) from e

    # ------------------------------------------------------------------
    # Распознавание
    # ------------------------------------------------------------------
    def _run(self, image: Any) -> str:
        """Прогоняет одно изображение через бэкенд и склеивает строки."""
        impl = self._ensure_impl()
        if self._impl_name == "rapidocr":
            # Возвращает (result, elapse); result = [[box, text, score], ...] либо None
            result = impl(image)
            lines = result[0] if isinstance(result, tuple) else result
            if not lines:
                return ""
            parts = []
            for item in lines:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    parts.append(str(item[1]))
            return "\n".join(parts)

        # PaddleOCR: predict возвращает список результатов с ключом rec_texts
        out = []
        try:
            predictions = impl.predict(input=image)
        except TypeError:
            predictions = impl.ocr(image, cls=True)
            if not predictions:
                return ""
            for page in predictions:
                for line in page or []:
                    if line and len(line) >= 2 and line[1]:
                        out.append(str(line[1][0]))
            return "\n".join(out)

        for page in predictions or []:
            data = getattr(page, "json", None)
            rec_texts = None
            if callable(data):
                try:
                    payload = data()
                    rec = payload.get("res", payload) if isinstance(payload, dict) else {}
                    rec_texts = rec.get("rec_texts")
                except Exception as e:  # pragma: no cover - зависит от версии
                    logger.debug(f"Не удалось прочитать результат PaddleOCR: {e}")
            if not rec_texts:
                rec_texts = getattr(page, "rec_texts", None)
            if rec_texts:
                out.extend(str(t) for t in rec_texts)
        return "\n".join(out)

    def image_to_text(self, image: Any) -> str:
        """
        Распознаёт одно изображение PIL.

        :param image: PIL.Image.Image
        :returns: текст; пустая строка, если на изображении нет распознанного текста
        :raises OcrUnavailable: движок не установлен или не поднялся
        """
        return self._run(image)

    def file_to_text(self, file_path: str, max_pages: Optional[int] = None) -> str:
        """
        Распознаёт файл-скан или PDF.

        :param max_pages: сколько страниц разбирать (по умолчанию SCANREADER_OCR_MAX_PAGES).
            Для многостраничных исполнительных листов обычно достаточно первых
            страниц; полный разбор 200-страничного PDF неоправданно дорог.
        """
        from PIL import Image, ImageOps

        limit = MAX_PAGES if max_pages is None else max_pages
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            return self._pdf_to_text(file_path, limit)

        if ext in (".tif", ".tiff"):
            pages: List[Any] = []
            with Image.open(file_path) as im:
                count = getattr(im, "n_frames", 1)
                for idx in range(min(count, limit)):
                    im.seek(idx)
                    pages.append(ImageOps.exif_transpose(im.copy()).convert("RGB"))
            chunks = [self._run(p) for p in pages]
            return "\n".join(c for c in chunks if c).strip()

        with Image.open(file_path) as im:
            prepared = ImageOps.exif_transpose(im).convert("RGB")
            return self._run(prepared).strip()

    def _pdf_to_text(self, file_path: str, limit: int) -> str:
        """Постраничный OCR PDF. Предпочитает текстовый слой, если он есть."""
        try:
            import fitz
        except ImportError:
            raise OcrUnavailable("PyMuPDF (pymupdf) обязателен для OCR PDF") from None

        from PIL import Image

        chunks: List[str] = []
        try:
            doc = fitz.open(file_path)
        except Exception as e:
            raise OcrUnavailable(f"Не удалось открыть PDF '{file_path}': {e}") from e

        try:
            for index in range(min(doc.page_count, limit)):
                # Если на странице есть нормальный текстовый слой - берём его:
                # OCR там не нужен и точнее.
                try:
                    native = doc.load_page(index).get_text().strip()
                except Exception:
                    native = ""
                if len(native) >= MIN_TEXT_LENGTH:
                    chunks.append(native)
                    continue
                pix = doc.load_page(index).get_pixmap(dpi=200)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                chunks.append(self._run(img))
        finally:
            doc.close()

        return "\n".join(c for c in chunks if c).strip()

    def text(self, file_path: str, max_pages: Optional[int] = None) -> Optional[str]:
        """
        Обёртка для вызывающего кода: вернёт None вместо исключения.

        Поведение по контракту:
          None            — OCR недоступен или отработал с ошибкой (гейт не выполнен);
          ""              — OCR выполнен, но текста нет;
          непустая строка — эталон для кросс-модального гейта.
        """
        try:
            recognized = self.file_to_text(file_path, max_pages=max_pages)
        except OcrUnavailable as e:
            logger.debug(f"OCR недоступен для '{file_path}': {e}")
            return None
        except Exception as e:
            logger.warning(f"Сбой OCR для '{file_path}': {e}")
            return None
        return recognized or ""


_ENGINE_SINGLETON: Optional[OcrEngine] = None
_SINGLETON_LOCK = threading.Lock()


def get_ocr_engine(lang: Optional[str] = None) -> OcrEngine:
    """Общий экземпляр движка: модель тяжёлая, держать её в разных объектах
    бессмысленно."""
    global _ENGINE_SINGLETON
    if _ENGINE_SINGLETON is None:
        resolved_lang = lang if lang else (os.getenv("SCANREADER_OCR_LANG") or "ru")
        with _SINGLETON_LOCK:
            if _ENGINE_SINGLETON is None:
                _ENGINE_SINGLETON = OcrEngine(lang=resolved_lang)
    return _ENGINE_SINGLETON


def reset_ocr_engine() -> None:
    """Сброс синглтона (используется в тестах для изоляции окружения)."""
    global _ENGINE_SINGLETON
    with _SINGLETON_LOCK:
        _ENGINE_SINGLETON = None
