# -*- coding: utf-8 -*-
"""
Тесты кэша core/cache.py и функций диагностики core/diagnostics.py.
"""

import tempfile
from pathlib import Path
from scan_reader.core.cache import LLMResponseCache
from scan_reader.core.diagnostics import mask_key, check_network_ping


def test_cache_set_get_and_clear():
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache_path = Path(tmp_dir)
        cache = LLMResponseCache(cache_dir=cache_path, enabled=True)

        key = cache.compute_key("sys_prompt", "user_content", "test_model")
        assert cache.get(key) is None

        cache.set(key, '{"result": "success"}')
        assert cache.get(key) == '{"result": "success"}'

        # Проверка повторной загрузки с диска
        cache2 = LLMResponseCache(cache_dir=cache_path, enabled=True)
        assert cache2.get(key) == '{"result": "success"}'

        cache2.clear()
        assert cache2.get(key) is None


def test_diagnostics_mask_key():
    assert mask_key("") == "Не задан (None)"
    assert "локальный режим" in mask_key("dummy_local_key")
    assert mask_key("12345") == "***"
    masked_long = mask_key("sk-proj-1234567890abcdef")
    assert masked_long.startswith("sk-p")
    assert masked_long.endswith("cdef")


def test_diagnostics_ping_mock():
    # Проверка вызова пинга к локальному URL
    res = check_network_ping("http://localhost:11434/v1", timeout=1.0)
    assert "status" in res
    assert "latency_ms" in res
