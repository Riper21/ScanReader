"""
Единый модуль утилит ядра (core/utils.py).
Предоставляет надежный парсинг JSON из вывода VLM/LLM с очисткой reasoning-тегов,
кроссплатформенную настройку консоли UTF-8, санитизацию имен файлов и логирование.
"""

import sys
import os
import re
import json
import logging
from typing import Any, Dict


_module_logger = logging.getLogger("core.utils")


def setup_console_utf8() -> None:
    """
    Кроссплатформенная безопасная настройка UTF-8 для вывода в консоль (Windows/Linux).
    """
    if sys.platform.startswith("win"):
        try:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            if hasattr(sys.stderr, "reconfigure"):
                sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception as e:
            _module_logger.debug(f"Не удалось переконфигурировать консольные потоки в UTF-8: {e}")


# Автоматическая инициализация при импорте
setup_console_utf8()


def _safe_parse_json(text: Any) -> Dict[str, Any]:
    """
    Вспомогательная функция безопасного парсинга JSON из вывода VLM/LLM.
    1. Очищает блоки рассуждений моделей Qwen / DeepSeek (<think>...</think>).
    2. Извлекает JSON из markdown code blocks (```json ... ``` или ``` ... ```).
    3. При необходимости ищет внешние фигурные скобки { ... }.
    """
    if not text:
        return {}
    if isinstance(text, dict):
        return text
    if not isinstance(text, str):
        text = str(text)

    # 1. Очистка рассуждений DeepSeek / Qwen / Reasoning моделей (<think>...</think>)
    clean_text = re.sub(r"<think>[\s\S]*?</think>", "", text).strip()
    if not clean_text:
        return {}

    # 2. Прямая попытка стандартного json.loads
    try:
        data = json.loads(clean_text)
        if isinstance(data, dict):
            return data
    except Exception as e:
        _module_logger.debug(f"Прямой json.loads не удался: {e}")

    # 3. Попытка извлечь JSON из markdown-блока ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", clean_text, flags=re.IGNORECASE)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except Exception as e:
            _module_logger.debug(f"JSON из markdown-блока не распознан: {e}")

    # 4. Попытка извлечь JSON по крайним фигурным скобкам { ... }
    first_brace = clean_text.find("{")
    last_brace = clean_text.rfind("}")
    if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
        json_candidate = clean_text[first_brace:last_brace + 1].strip()
        try:
            data = json.loads(json_candidate)
            if isinstance(data, dict):
                return data
        except Exception as e:
            _module_logger.debug(f"JSON по внешним скобкам не распознан: {e}")

    return {}


_RESERVED_WINDOWS_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"
}


def sanitize_filename(title: str, max_len: int = 60) -> str:
    """
    Санитизация названия документа/артефакта для безопасного сохранения в ОС (Windows/Linux).
    Удаляет спецсимволы, заменяет повторяющиеся пробелы, экранирует зарезервированные имена и ограничивает длину.
    """
    if not title:
        return "document"
    clean = re.sub(r'[\\/*?:"<>|]', '_', str(title))
    clean = re.sub(r'[\r\n\t]', ' ', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = clean[:max_len].rstrip('. ')
    if not clean:
        return "document"
    if clean.upper() in _RESERVED_WINDOWS_NAMES:
        clean = f"doc_{clean}"
    return clean


class SecretMaskingFilter(logging.Filter):
    """Фильтр логирования для автоматического маскирования конфиденциальных токенов и ПДн (H-02)."""
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            try:
                from .io_utils import mask_secret
                record.msg = mask_secret(record.msg)
            except Exception as e:
                _module_logger.debug(f"Маскирование секретов недоступно: {e}")
        return True


def get_logger(name: str) -> logging.Logger:
    """
    Получить сконфигурированный логгер для модуля платформы.
    Хендлер пишется в stderr: stdout зарезервирован для данных/JSON (1С-интеграция).
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s", datefmt="%H:%M:%S")
        )
        handler.addFilter(SecretMaskingFilter())
        logger.addHandler(handler)
        logger.propagate = False
        log_level = os.getenv("LOG_LEVEL", "INFO").upper()
        logger.setLevel(getattr(logging, log_level, logging.INFO))
    return logger


def coerce_to_str(v: Any) -> str:
    """
    Универсальное приведение любых типов (dict, list, int, float) к аккуратной строке
    для защиты схем Pydantic v2 от ValidationError при обработке гибких ответов LLM/VLM.
    """
    if v is None:
        return ""
    if isinstance(v, str):
        return v.strip()
    if isinstance(v, bool):
        return "Да" if v else "Нет"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, list):
        items = [coerce_to_str(x) for x in v if x is not None and str(x).strip()]
        return ", ".join(items)
    if isinstance(v, dict):
        parts = []
        for k, val in v.items():
            if val is not None and str(val).strip():
                val_str = coerce_to_str(val) if isinstance(val, (dict, list)) else str(val).strip()
                parts.append(f"{k}: {val_str}")
        return "; ".join(parts)
    return str(v).strip()


__all__ = [
    "setup_console_utf8",
    "_safe_parse_json",
    "sanitize_filename",
    "get_logger",
    "coerce_to_str",
]


