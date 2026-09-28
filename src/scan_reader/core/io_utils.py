"""
I/O utilities for resilient, atomic file handling, stream configuration, and secret masking.
"""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Optional, Union

from .utils import get_logger, setup_console_utf8

# Фаза 7.5: сырой logging.getLogger не имеет фильтра маскирования секретов.
# Импорт utils здесь безопасен: utils импортирует io_utils только внутри
# SecretMaskingFilter.filter, то есть лениво, и цикла на уровне модулей нет.
_io_logger = get_logger("core.io_utils")

MAX_INPUT_BYTES: int = 50 * 1024 * 1024  # 50 MB safeguard


class OutputPathError(ValueError):
    """Raised when an output path already exists and overwrite is not authorized."""


class InputFileError(ValueError):
    """Raised when an input file is missing, exceeds limits, or cannot be safely read."""


# Фаза 8.7: настройка UTF-8 для потоков была реализована трижды - здесь,
# в core/utils.setup_console_utf8 и инлайн в блоке __main__ json_exporter.
# Второй вызов на уже перенастроенном потоке на части сборок Python даёт
# ValueError: cannot set 'encoding' after a stream has been accessed.
# Осталась одна реализация, и она идемпотентна.
configure_streams = setup_console_utf8


# Фаза 8.5: read_file_safe удалён. Единственным его вызывающим был
# tests/test_atomic_io.py; ни document_loader, ни file_processor его не
# использовали — обе точки проверяли размер файла инлайн. Покрытая тестами,
# но не используемая функция остаётся мёртвым кодом.


def write_atomic(
    path: Union[str, Path],
    content: Union[str, bytes],
    force: bool = True,
    encoding: str = "utf-8",
) -> Path:
    """
    Atomically write content to a file using a temp file in the destination folder,
    flushing, syncing to disk via fsync, and replacing the target file.
    Prevents partial/corrupted writes on sudden process termination or power loss.
    """
    target = Path(path).resolve()
    if target.exists() and not force:
        raise OutputPathError(f"output already exists and overwrite is disabled: {target}")

    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Optional[Path] = None

    try:
        if isinstance(content, bytes):
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=str(target.parent),
                prefix=f".{target.name}.tmp-",
                suffix=".tmp",
                delete=False,
            ) as handle_b:
                temp_path = Path(handle_b.name)
                handle_b.write(content)
                handle_b.flush()
                os.fsync(handle_b.fileno())
        else:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=str(target.parent),
                prefix=f".{target.name}.tmp-",
                suffix=".tmp",
                delete=False,
                encoding=encoding,
                newline="\n",
            ) as handle_t:
                temp_path = Path(handle_t.name)
                handle_t.write(content)
                handle_t.flush()
                os.fsync(handle_t.fileno())

        os.replace(str(temp_path), str(target))
        temp_path = None
        return target
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as e:
                _io_logger.debug(f"Не удалось удалить временный файл '{temp_path}': {e}")


_SECRET_PATTERNS = [
    re.compile(r"(sk-[a-zA-Z0-9_\-]{8})[a-zA-Z0-9_\-]+"),
    re.compile(r"(Bearer\s+[a-zA-Z0-9_\-\.]{8})[a-zA-Z0-9_\-\.]+"),
    re.compile(r"(key[=:]\s*['\"]?)[a-zA-Z0-9_\-]{6}[a-zA-Z0-9_\-]+(['\"]?)"),
    re.compile(r"(\b\d{4}\s+)\d{6}(\b)"),  # Russian passport series/number
]


def mask_secret(text: str) -> str:
    """Mask sensitive tokens, API keys, and PII from log output."""
    masked = text
    for pattern in _SECRET_PATTERNS:
        masked = pattern.sub(r"\g<1>***REDACTED***", masked)
    return masked
