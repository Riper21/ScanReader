"""
Инфраструктурное ядро AI-системы распознавания документов (core).
Предоставляет утилиты парсинга, трекинга токенов, rate-limiting, кэширования, загрузки файлов и парсинга финансов.
"""

from .utils import _safe_parse_json, setup_console_utf8, sanitize_filename, get_logger
from .token_tracker import TokenUsageTracker
from .rate_limiter import RateLimiter
from .cache import LLMResponseCache
from .document_loader import load_document
from .finance_parser import parse_russian_currency, extract_amounts_from_text

__all__ = [
    "_safe_parse_json",
    "setup_console_utf8",
    "sanitize_filename",
    "get_logger",
    "TokenUsageTracker",
    "RateLimiter",
    "LLMResponseCache",
    "load_document",
    "parse_russian_currency",
    "extract_amounts_from_text",
]
