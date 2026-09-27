"""
I/O utilities for resilient, atomic file handling, stream configuration, and secret masking.
Adapted from architectural patterns in TestCraft and Mermaid-Guard.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional, Union

_io_logger = logging.getLogger("core.io_utils")

MAX_INPUT_BYTES: int = 50 * 1024 * 1024  # 50 MB safeguard


class OutputPathError(ValueError):
    """Raised when an output path already exists and overwrite is not authorized."""


class InputFileError(ValueError):
    """Raised when an input file is missing, exceeds limits, or cannot be safely read."""


def configure_streams() -> None:
    """Safely configure stdout and stderr to UTF-8 on Windows and POSIX."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError) as e:
                _io_logger.debug(f"Не удалось переконфигурировать поток в UTF-8: {e}")


def read_file_safe(path: Union[str, Path], max_bytes: int = MAX_INPUT_BYTES) -> bytes:
    """Read a file safely checking existence and size limits."""
    file_path = Path(path).resolve()
    if not file_path.is_file():
        raise InputFileError(f"file not found: {file_path}")
    size = file_path.stat().st_size
    if size > max_bytes:
        raise InputFileError(f"input file exceeds size limit ({size} > {max_bytes} bytes): {file_path}")
    return file_path.read_bytes()


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
