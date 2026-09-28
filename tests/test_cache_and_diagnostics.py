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

        # Фаза 7.6: сброс на диск отложен (иначе каждый set() сериализовал бы
        # весь кэш под замком, то есть O(n^2) за прогон). Долговечность
        # обеспечивается явным flush(), контекстным менеджером или close().
        cache.flush()
        cache2 = LLMResponseCache(cache_dir=cache_path, enabled=True)
        assert cache2.get(key) == '{"result": "success"}'

        cache2.clear()
        assert cache2.get(key) is None


def test_cache_context_manager_flushes_on_exit():
    """Контекстный менеджер гарантирует запись кэша при выходе."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        key_holder = {}

        with LLMResponseCache(cache_dir=Path(tmp_dir), enabled=True) as cache:
            key = cache.compute_key("p", "c", "m")
            key_holder["k"] = key
            cache.set(key, "persisted")

        reloaded = LLMResponseCache(cache_dir=Path(tmp_dir), enabled=True)
        assert reloaded.get(key_holder["k"]) == "persisted"


def test_cache_evicts_least_recently_used():
    """Фаза 7.6: LRU вместо FIFO.

    При FIFO вытеснялась первая ПО ПОРЯДКУ ВСТАВКИ запись, то есть давно
    не использовавшиеся ключи вытеснялись наравне с полезными, а часто
    используемые терялись первыми.
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        cache = LLMResponseCache(cache_dir=Path(tmp_dir), enabled=True, max_entries=3)
        cache.set("k1", "v1")
        cache.set("k2", "v2")
        cache.set("k3", "v3")

        # k1 используется и становится «свежим», порядок приоритета: k2, k3, k1
        assert cache.get("k1") == "v1"

        cache.set("k4", "v4")
        assert len(cache) == 3
        assert cache.get("k2") is None, "вытеснен наименее недавно использованный"
        assert cache.get("k1") == "v1"
        assert cache.get("k3") == "v3"
        assert cache.get("k4") == "v4"


def test_cache_batches_disk_writes(tmp_path, monkeypatch):
    """Регрессия Фазы 7.6: запись на диск не должна выполняться на каждый set()."""
    writes = []
    import scan_reader.core.cache as cache_mod

    def _spy(path, content):
        writes.append(1)
        return path

    monkeypatch.setattr(cache_mod, "write_atomic", _spy)
    monkeypatch.setenv("SCANREADER_CACHE_FLUSH_EVERY", "3")
    cache = cache_mod.LLMResponseCache(cache_dir=tmp_path / "c", enabled=True, max_entries=100)

    cache.set("a", "1")
    cache.set("b", "2")
    assert len(writes) == 0, "запись на диск не должна идти на каждый set()"

    cache.set("c", "3")
    assert len(writes) == 1, "сброс должен происходить по достижении пачки"


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
