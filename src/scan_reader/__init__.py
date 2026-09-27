"""
ScanReader: AI legal document recognition, Zero-Trust verification, and 1C/Excel registry orchestration.
"""

from __future__ import annotations

__version__ = "0.8.0"

from .facade import LegalDocPlatformFacade
from .file_processor import FileProcessor
from .type_registry import DocumentTypeRegistry, get_registry
from .excel_exporter import LegalExcelExporter

__all__ = [
    "__version__",
    "LegalDocPlatformFacade",
    "FileProcessor",
    "DocumentTypeRegistry",
    "get_registry",
    "LegalExcelExporter",
]
