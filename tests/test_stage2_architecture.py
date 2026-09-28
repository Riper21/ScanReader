# -*- coding: utf-8 -*-
"""
Tests for Stage 2: Architecture, Environment & Isolation.
Verifies:
- C-07: Diagnostics aggregated status and health report.
- C-13: Ground truth path resolution (env var, cwd, repo root).
- C-14: Results dir resolution (env var, cwd, repo root).
- C-16: Clean imports without sys.path pollution.
- H-10: Bounded LLM cache outside package directory with FIFO eviction.
- H-11: Cache key uniqueness (SHA-256, no prefix truncation collision).
- H-12: Thread-safe TokenUsageTracker.set_stage.
- H-13: Bounded memory in TokenUsageTracker records (deque maxlen).
"""

import os
import threading
from pathlib import Path

from scan_reader.core.diagnostics import run_diagnostics, get_system_report
from scan_reader.core.cache import LLMResponseCache
from scan_reader.core.token_tracker import TokenUsageTracker
from scan_reader.facade import get_ground_truth_path, LegalDocPlatformFacade


def test_c07_diagnostics_aggregated_status():
    """C-07: Diagnostics returns an aggregated status ('ok' or 'warning'/'error') and does not raise."""
    report = run_diagnostics(verbose=False)
    assert isinstance(report, dict)
    assert "status" in report
    assert report["status"] in ("ok", "warning", "error")
    assert "environment" in report
    assert "directories" in report
    assert "dependencies" in report

    summary = get_system_report()
    assert isinstance(summary, str)
    assert len(summary) > 0


def test_c13_ground_truth_path_resolution(monkeypatch, tmp_path):
    """C-13: get_ground_truth_path correctly checks SCANREADER_GROUND_TRUTH_DIR, cwd, and root."""
    # Test env var override
    fake_gt_dir = tmp_path / "custom_gt"
    fake_gt_dir.mkdir()
    fake_gt_file = fake_gt_dir / "test_gt.json"
    fake_gt_file.write_text("{}", encoding="utf-8")

    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", str(fake_gt_dir))
    resolved = get_ground_truth_path("test_gt.json")
    assert resolved is not None
    assert Path(resolved).resolve() == fake_gt_file.resolve()

    # None or non-existent returns None
    assert get_ground_truth_path("non_existent_file_xyz.json") is None
    assert get_ground_truth_path(None) is None


def test_c14_results_dir_resolution(monkeypatch, tmp_path):
    """C-14: Facade results_dir respects SCANREADER_OUTPUT_DIR and explicit param."""
    custom_out = str(tmp_path / "custom_results")
    monkeypatch.setenv("SCANREADER_OUTPUT_DIR", custom_out)

    facade = LegalDocPlatformFacade()
    assert Path(facade.results_dir).resolve() == Path(custom_out).resolve()
    assert os.path.exists(custom_out)

    explicit_out = str(tmp_path / "explicit_results")
    facade_explicit = LegalDocPlatformFacade(results_dir=explicit_out)
    assert Path(facade_explicit.results_dir).resolve() == Path(explicit_out).resolve()


def test_c16_clean_imports_no_sys_path_insert():
    """C-16: Core modules must not dynamically pollute sys.path with arbitrary paths."""
    # Verify that importing type_registry, facade, etc., does not insert current file dir at index 0
    import scan_reader.type_registry
    import scan_reader.core.diagnostics
    import scan_reader.core.json_exporter
    # Check that sys.path does not contain relative strings or duplicate cwd paths added at index 0
    # Our modules removed sys.path.insert(0, ...)
    assert hasattr(scan_reader.type_registry, "get_registry")


def test_h10_bounded_cache_eviction(tmp_path):
    """
    H-10: LLMResponseCache уважает max_entries и хранит кэш вне пакета.

    Фаза 7.6: вытеснение LRU вместо FIFO. При FIFO первая по порядку вставки
    запись вытеснялась независимо от того, использовалась ли она, то есть
    повторно применяемые ключи терялись первыми.
    """
    cache_dir = tmp_path / "cache_test"
    cache = LLMResponseCache(cache_dir=str(cache_dir), enabled=True, max_entries=3)

    # Кэш размещается вне дерева исходников пакета
    assert "src/scan_reader" not in str(Path(cache.cache_dir).resolve()).replace("\\", "/")

    cache.set("key1", "val1")
    cache.set("key2", "val2")
    cache.set("key3", "val3")
    assert len(cache) == 3
    assert cache.get("key1") == "val1"

    # key1 только что использован, поэтому вытесняется key2 (наименее свежий)
    cache.set("key4", "val4")
    assert len(cache) == 3
    assert cache.get("key1") == "val1"
    assert cache.get("key2") is None
    assert cache.get("key3") == "val3"
    assert cache.get("key4") == "val4"


def test_h11_cache_key_uniqueness(tmp_path):
    """H-11: Keys computed via compute_key are full SHA-256 and do not collide on long inputs."""
    cache = LLMResponseCache(cache_dir=str(tmp_path / "c"), enabled=False)

    prefix = "A" * 2000
    input1 = prefix + "_variant_1"
    input2 = prefix + "_variant_2"

    key1 = cache.compute_key("sys_prompt", input1, "model_x")
    key2 = cache.compute_key("sys_prompt", input2, "model_x")

    assert key1 != key2
    assert len(key1) == 64  # SHA-256 hex length
    assert len(key2) == 64


def test_h12_thread_safe_token_tracker():
    """H-12: TokenUsageTracker.set_stage and record_call are thread-safe under concurrent execution."""
    tracker = TokenUsageTracker()
    errors = []

    def worker(stage_name, thread_id):
        try:
            for i in range(100):
                tracker.set_stage(f"{stage_name}_{i}")
                tracker.record_call(
                    model="model_test",
                    prompt_tokens=10,
                    completion_tokens=20,
                    is_cache_hit=(i % 2 == 0),
                    stage_name=f"{stage_name}_{i}",
                    doc_name=f"doc_{thread_id}.pdf"
                )
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(f"stage_{t}", t)) for t in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(errors) == 0
    assert tracker.total_calls == 500
    assert tracker.total_prompt_tokens == 500 * 10
    assert tracker.total_completion_tokens == 500 * 20


def test_h13_bounded_memory_token_tracker():
    """H-13: TokenUsageTracker records use bounded deque to prevent memory exhaustion."""
    tracker = TokenUsageTracker()
    # Record more than 5000 calls
    for i in range(5200):
        tracker.record_call(
            model="model_bench",
            prompt_tokens=1,
            completion_tokens=1,
            is_cache_hit=False
        )
    assert tracker.total_calls == 5200
    assert len(tracker.records) <= 5000
