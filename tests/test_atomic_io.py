# -*- coding: utf-8 -*-
"""
Tests for atomic I/O, limits, and secret masking.
"""

import pytest
from scan_reader.core.io_utils import (
    MAX_INPUT_BYTES,
    InputFileError,
    OutputPathError,
    mask_secret,
    write_atomic,
)
from scan_reader.file_processor import check_input_file_size


def test_write_atomic_and_read(tmp_path):
    target = tmp_path / "subfolder" / "atomic.txt"
    content = "Legal document test content UTF-8: Тест"
    out_path = write_atomic(target, content)
    assert out_path.exists()
    assert out_path.read_text(encoding="utf-8") == content

    # Overwrite protection when force=False
    with pytest.raises(OutputPathError):
        write_atomic(target, "new content", force=False)

    # Overwrite allowed when force=True
    write_atomic(target, "new content", force=True)
    assert target.read_text(encoding="utf-8") == "new content"


def test_input_size_limit_is_enforced_in_production_path(tmp_path):
    """
    Фаза 8.5: read_file_safe была покрыта тестами, но не использовалась ни в
    одном production-модуле. Проверка лимита размера действительно выполняется
    в file_processor.check_input_file_size - она вызывается перед каждым
    чтением изображения или PDF, поэтому тест теперь проверяет её.
    """
    small = tmp_path / "small.txt"
    small.write_text("hello", encoding="utf-8")
    check_input_file_size(str(small))  # малый файл проходит

    oversized = tmp_path / "big.bin"
    oversized.write_bytes(b"0" * (MAX_INPUT_BYTES + 1))
    with pytest.raises(InputFileError):
        check_input_file_size(str(oversized))

    with pytest.raises(InputFileError):
        check_input_file_size(str(tmp_path / "нет-такого-файла.txt"))


def test_mask_secret():
    secret_str = "OpenAI key sk-12345678abcdef123456 and bearer Bearer abcdefgh12345678"
    masked = mask_secret(secret_str)
    assert "sk-12345678***REDACTED***" in masked
    assert "Bearer abcdefgh***REDACTED***" in masked
