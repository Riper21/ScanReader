# -*- coding: utf-8 -*-
"""
Модуль высококачественной предобработки и подготовки входных файлов (PDF, изображения, TIFF, DOCX, TXT)
для передачи в мультимодальные (VLM) и языковые (LLM) модели.
Включает:
- Улучшение контрастности (CLAHE / AutoContrast)
- Двухзональную нарезку (Dual-Zone Crop: Header Hi-Res + Full Page Context)
- Оптимизацию токенов и Lanczos-ресэмплинг
"""

import os
import io
import base64
from typing import Any, List, Optional, Set, Tuple, Union, Dict

try:
    from PIL import Image, ImageOps, ImageSequence, ImageEnhance
    PIL_AVAILABLE = True
except ImportError:
    Image = None  # type: ignore[assignment]
    ImageOps = None  # type: ignore[assignment]
    ImageSequence = None  # type: ignore[assignment]
    ImageEnhance = None  # type: ignore[assignment]
    PIL_AVAILABLE = False

from .core.utils import get_logger
from .core.document_loader import load_document, SUPPORTED_TEXT_EXTS, SUPPORTED_WORD_EXTS
from .core.io_utils import MAX_INPUT_BYTES, InputFileError

logger = get_logger("file_processor")

SUPPORTED_IMAGE_EXTS: Set[str] = {".jpg", ".jpeg", ".jfif", ".jpe", ".png", ".webp", ".bmp", ".tiff", ".tif"}
SUPPORTED_PDF_EXTS: Set[str] = {".pdf"}
SUPPORTED_OFFICE_EXTS: Set[str] = SUPPORTED_TEXT_EXTS | SUPPORTED_WORD_EXTS
ALL_SUPPORTED_EXTS: Set[str] = SUPPORTED_IMAGE_EXTS | SUPPORTED_PDF_EXTS | SUPPORTED_OFFICE_EXTS


def check_input_file_size(file_path: str) -> None:
    """
    Проверяет размер входного файла против MAX_INPUT_BYTES (H-04).
    Файлы свыше лимита отклоняются до передачи в VLM/парсеры.
    """
    try:
        size = os.path.getsize(file_path)
    except OSError as e:
        raise InputFileError(f"Не удалось получить размер входного файла '{file_path}': {e}")
    if size > MAX_INPUT_BYTES:
        raise InputFileError(
            f"Входной файл превышает лимит размера ({size} > {MAX_INPUT_BYTES} байт): {file_path}"
        )


def _get_lanczos_resampling():
    """Совместимость: Image.Resampling.LANCZOS появился в Pillow 9.1."""
    if not PIL_AVAILABLE:
        return None
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:
        return Image.LANCZOS  # type: ignore[attr-defined]


class FileProcessor:
    """Класс для загрузки, конвертации и подготовки документов к отправке в Vision / Text LLM."""

    def __init__(
        self,
        max_dimension: int = 2048,
        image_quality: int = 88,
        pdf_dpi: int = 200,
        enable_contrast_enhancement: bool = True,
        min_dimension: int = 1500
    ):
        """
        :param max_dimension: Максимальный размер стороны изображения в пикселях.
        :param image_quality: Качество сжатия JPEG (1-100).
        :param pdf_dpi: Разрешение рендеринга PDF страниц (DPI=200).
        :param enable_contrast_enhancement: Включение автоконтраста бледных сканов.
        :param min_dimension: Апскейл сканов меньше этой стороны (S-1): VLM
            на 96 DPI читает цифры реквизитов с ошибками — апскейл 2x (LANCZOS)
            существенно повышает точность распознавания мелкого текста.
        """
        self.max_dimension = max_dimension
        self.image_quality = image_quality
        self.pdf_dpi = pdf_dpi
        self.enable_contrast_enhancement = enable_contrast_enhancement
        self.min_dimension = min_dimension

    def normalize_and_orient_image(self, img: Any) -> Any:
        """Корректирует ориентацию изображения по EXIF-тегам, приводит к RGB и улучшает контраст."""
        if not PIL_AVAILABLE:
            raise RuntimeError("Библиотека Pillow не установлена. Запустите: py -3 -m pip install Pillow")
        try:
            img = ImageOps.exif_transpose(img)
        except Exception as e:
            logger.debug(f"EXIF-коррекция ориентации не выполнена: {e}")

        if img.mode in ("RGBA", "P", "LA", "CMYK"):
            img = img.convert("RGB")

        if self.enable_contrast_enhancement:
            img = self.enhance_document_contrast(img)

        return img

    def enhance_document_contrast(self, img: Any) -> Any:
        """Адаптивное улучшение контраста (AutoContrast + Sharpness) для усиления бледных печатей и шрифтов."""
        if not PIL_AVAILABLE:
            return img
        try:
            # Автоматическая растяжка гистограммы с отсечкой шума 1%
            img_c = ImageOps.autocontrast(img, cutoff=1)
            # Небольшое усиление резкости текста
            enhancer = ImageEnhance.Sharpness(img_c)
            img_enhanced = enhancer.enhance(1.25)
            return img_enhanced
        except Exception:
            return img

    def resize_image_if_needed(self, img: Any, max_dim: Optional[int] = None) -> Any:
        """
        Пропорциональное масштабирование под лимиты VLM:
        - больше max_dim -> уменьшение;
        - меньше min_dimension (S-1) -> апскейл до 2x LANCZOS (не выше max_dim):
          мелкий текст реквизитов на сканах 96 DPI становится читаемым.
        """
        img = self.normalize_and_orient_image(img)
        limit = max_dim or self.max_dimension
        w, h = img.size
        long_side = max(w, h)
        scale = 1.0
        if long_side > limit:
            scale = limit / float(long_side)
        elif long_side < self.min_dimension:
            scale = min(2.0, limit / float(long_side))
        if scale != 1.0:
            new_w, new_h = int(w * scale), int(h * scale)
            img = img.resize((new_w, new_h), _get_lanczos_resampling())
        return img

    def pil_to_base64_data_uri(self, img: Any, max_dim: Optional[int] = None, mime_type: str = "image/jpeg") -> str:
        """Конвертирует PIL Image в Data URI строку base64 для Vision API."""
        if not PIL_AVAILABLE:
            raise RuntimeError("Библиотека Pillow не установлена. Запустите: py -3 -m pip install Pillow")
        img_prepared = self.resize_image_if_needed(img, max_dim=max_dim)
        buffer = io.BytesIO()
        img_prepared.save(buffer, format="JPEG", quality=self.image_quality, optimize=True)
        b64_str = base64.b64encode(buffer.getvalue()).decode("utf-8")
        return f"data:{mime_type};base64,{b64_str}"

    def extract_dual_zone_uris(self, img: Any) -> Dict[str, str]:
        """
        Двухзональная нарезка первой страницы:
        1. 'header_uri': верхние 38% страницы в максимальном разрешении (шапка, герб, штампы).
        2. 'full_uri': вся страница целиком (для общего визуального контекста).
        """
        if not PIL_AVAILABLE:
            raise RuntimeError("Библиотека Pillow не установлена. Запустите: py -3 -m pip install Pillow")
        img_norm = self.normalize_and_orient_image(img)
        w, h = img_norm.size

        # Зона 1: Шапка крупно (верхние 38% высоты)
        header_box = (0, 0, w, int(h * 0.38))
        header_crop = img_norm.crop(header_box)
        header_uri = self.pil_to_base64_data_uri(header_crop, max_dim=2048)

        # Зона 2: Вся страница целиком (размер 1536px для контекста)
        full_uri = self.pil_to_base64_data_uri(img_norm, max_dim=1536)

        return {"header_uri": header_uri, "full_uri": full_uri}

    def get_image_dpi(self, file_path: str) -> Optional[float]:
        """Возвращает DPI изображения из метаданных, если доступен (M-06)."""
        if not PIL_AVAILABLE:
            return None
        try:
            with Image.open(file_path) as img:
                dpi = img.info.get("dpi")
                if dpi and dpi[0] and dpi[0] > 0:
                    return float(dpi[0])
        except Exception as e:
            logger.debug(f"Не удалось прочитать DPI из '{file_path}': {e}")
        return None

    def process_image_file(self, file_path: str) -> List[str]:
        """Загружает файл изображения и возвращает список Data URI."""
        if not PIL_AVAILABLE:
            raise RuntimeError("Библиотека Pillow не установлена. Запустите: py -3 -m pip install Pillow")
        check_input_file_size(file_path)
        data_uris = []
        with Image.open(file_path) as img:
            for frame in ImageSequence.Iterator(img):
                frame_rgb = frame.copy()
                data_uris.append(self.pil_to_base64_data_uri(frame_rgb))
        return data_uris

    def process_pdf_file(self, file_path: str, max_pages: Optional[int] = None) -> List[str]:
        """Конвертирует страницы PDF в высококачественные изображения Data URI."""
        if not PIL_AVAILABLE:
            raise RuntimeError("Библиотека Pillow не установлена. Запустите: py -3 -m pip install Pillow")
        check_input_file_size(file_path)
        data_uris = []
        last_error: Optional[Exception] = None

        # 1. Попытка через PyMuPDF (fitz)
        try:
            import fitz
            doc = fitz.open(file_path)
            try:
                total_pages = len(doc)
                pages_to_render = min(total_pages, max_pages) if max_pages else total_pages
                zoom = self.pdf_dpi / 72.0
                mat = fitz.Matrix(zoom, zoom)
                for page_num in range(pages_to_render):
                    page = doc[page_num]
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
                    data_uris.append(self.pil_to_base64_data_uri(img))
            finally:
                doc.close()
            if data_uris:
                return data_uris
        except ImportError as e:
            logger.debug(f"PyMuPDF недоступен, используется fallback: {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"PyMuPDF не смог обработать PDF ({e}), попытка через pdf2image...")

        # 2. Попытка через pdf2image
        try:
            from pdf2image import convert_from_path
            first_page = 1
            last_page = max_pages if max_pages else None
            images = convert_from_path(file_path, dpi=self.pdf_dpi, first_page=first_page, last_page=last_page)
            for img in images:
                data_uris.append(self.pil_to_base64_data_uri(img))
            if data_uris:
                return data_uris
        except ImportError as e:
            logger.debug(f"pdf2image недоступен: {e}")
        except Exception as e:
            last_error = e
            logger.warning(f"pdf2image не смог обработать PDF: {e}")

        if last_error:
            raise RuntimeError(f"Не удалось обработать PDF '{file_path}': {last_error}")
        raise RuntimeError("Для обработки PDF установите PyMuPDF: `py -3 -m pip install pymupdf`")

    def prepare_dual_zone_inputs(self, file_path: str) -> Tuple[str, Any]:
        """
        Подготавливает двухзональный инжест для Роутера:
        - Для изображений/PDF: возвращает ("vision_dual_zone", {"header_uri": ..., "full_uri": ...})
        - Для DOCX/TXT: возвращает ("text", текст_шапки)
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        check_input_file_size(file_path)

        ext = os.path.splitext(file_path)[1].lower()

        if ext in SUPPORTED_IMAGE_EXTS:
            with Image.open(file_path) as img:
                return "vision_dual_zone", self.extract_dual_zone_uris(img)

        elif ext in SUPPORTED_PDF_EXTS:
            try:
                import fitz
                doc = fitz.open(file_path)
                try:
                    page = doc[0]
                    zoom = self.pdf_dpi / 72.0
                    mat = fitz.Matrix(zoom, zoom)
                    pix = page.get_pixmap(matrix=mat, alpha=False)
                    img = Image.open(io.BytesIO(pix.tobytes("jpeg")))
                    return "vision_dual_zone", self.extract_dual_zone_uris(img)
                finally:
                    doc.close()
            except Exception:
                # Fallback на первый рендер
                uris = self.process_pdf_file(file_path, max_pages=1)
                return "vision", uris

        elif ext in SUPPORTED_OFFICE_EXTS:
            _, text = load_document(file_path)
            return "text", text[:3000]

        else:
            raise ValueError(f"Неподдерживаемый формат файла: {ext}")

    def prepare_document_inputs(self, file_path: str, max_pages: Optional[int] = None) -> Tuple[str, Union[List[str], str]]:
        """
        Универсальный метод для полной экстракции данных:
        Возвращает: (mode, content), где mode == "vision" (список base64) или "text" (строка).
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Файл не найден: {file_path}")
        check_input_file_size(file_path)

        ext = os.path.splitext(file_path)[1].lower()

        if ext in SUPPORTED_IMAGE_EXTS:
            uris = self.process_image_file(file_path)
            if max_pages is not None:
                uris = uris[:max_pages]
            return "vision", uris
        elif ext in SUPPORTED_PDF_EXTS:
            uris = self.process_pdf_file(file_path, max_pages=max_pages)
            return "vision", uris
        elif ext in SUPPORTED_OFFICE_EXTS:
            _, text = load_document(file_path)
            return "text", text
        else:
            raise ValueError(
                f"Неподдерживаемый формат файла: {ext}. Поддерживаются: {sorted(list(ALL_SUPPORTED_EXTS))}"
            )


def scan_directory_for_documents(directory_path: str) -> List[str]:
    """Рекурсивно находит все поддерживаемые файлы в директории."""
    found_files = []
    for root, _, files in os.walk(directory_path):
        for f in sorted(files):
            ext = os.path.splitext(f)[1].lower()
            if ext in ALL_SUPPORTED_EXTS:
                found_files.append(os.path.join(root, f))
    return found_files
