"""
ScanReader: AI legal document recognition, Zero-Trust verification, and 1C/Excel registry orchestration.
"""

from __future__ import annotations

# Фаза 8.10: окружение читается здесь, до любых подмодулей пакета.
# Модули пакета создают логгеры с LOG_LEVEL и читают пути на уровне модуля,
# поэтому .env обязан быть загружен до них. Раньше загрузка стояла в середине
# facade.py, и pyproject.toml подавлял E402 для всего файла.
from . import config as _config

_config.load_environment()

__version__ = "0.9.2"

from .facade import LegalDocPlatformFacade  # noqa: E402
from .file_processor import FileProcessor  # noqa: E402
from .type_registry import DocumentTypeRegistry, get_registry  # noqa: E402
from .excel_exporter import LegalExcelExporter  # noqa: E402

__all__ = [
    "__version__",
    "LegalDocPlatformFacade",
    "FileProcessor",
    "DocumentTypeRegistry",
    "get_registry",
    "LegalExcelExporter",
]
