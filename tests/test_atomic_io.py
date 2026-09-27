# -*- coding: utf-8 -*-
"""
Tests for atomic I/O, limits, and secret masking.
"""

import pytest
from scan_reader.core.io_utils import (
    InputFileError,
    OutputPathError,
    mask_secret,
    read_file_safe,
    write_atomic,
)


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
    assert out_path.read_text(encoding="utf-8") == "new content"


def test_read_file_safe_limits(tmp_path):
    f = tmp_path / "small.txt"
    f.write_text("hello", encoding="utf-8")
    data = read_file_safe(f, max_bytes=100)
    assert data == b"hello"

    with pytest.raises(InputFileError):
        read_file_safe(f, max_bytes=2)


def test_mask_secret():
    secret_str = "OpenAI key sk-12345678abcdef123456 and bearer Bearer abcdefgh12345678"
    masked = mask_secret(secret_str)
    assert "sk-12345678***REDACTED***" in masked
    assert "Bearer abcdefgh***REDACTED***" in masked
