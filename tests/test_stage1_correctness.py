# -*- coding: utf-8 -*-
"""
Tests for Stage 1 findings:
- C-01: parse_russian_currency 'руб + коп' scale error
- C-02: non-currency numbers / INN / negative amounts rejection
- C-03: benchmark.json canonical 'path' keys across all plugins
- C-04: flat_columns.json canonical 'path'/'label'/'kind' keys across all plugins
- C-06: metrics_evaluator evaluates true plugin schemas instead of executive_documents
- C-08: evaluate_dataset supports directories and MCP signature
- C-09: MCP scan_document passes result['data'] and raw_text to ZeroTrustAuditor
- C-11 & H-14: RateLimiter raises RateLimitTimeoutError, uses monotonic time and BoundedSemaphore
- C-12: json_exporter prevents silent overwrite on stem collision
"""

import os
import json
import tempfile
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from scan_reader.core.finance_parser import parse_russian_currency
from scan_reader.type_registry import get_registry
from scan_reader.core.metrics_evaluator import (
    evaluate_dataset,
    generate_run_summary
)
from scan_reader.core.rate_limiter import RateLimiter, RateLimitTimeoutError
from scan_reader.core.json_exporter import save_single_document_json
from scan_reader.mcp.server import ScanReaderMCPServer


def test_c01_currency_rub_kop_scale():
    """C-01: '1 234 руб. 50 коп.' must be 1234.50, not 123450.50."""
    res1 = parse_russian_currency("1 234 руб. 50 коп.")
    assert res1 == 1234.50

    res2 = parse_russian_currency("500 рублей 25 копеек")
    assert res2 == 500.25

    res3 = parse_russian_currency("1 000 000 руб. 00 коп.")
    assert res3 == 1000000.0


def test_c02_currency_rejects_inn_and_negative():
    """C-02: ИНН и голые длинные числа не должны разбираться как суммы."""
    # Голый ИНН с префиксом и без
    assert parse_russian_currency("ИНН 7707083893") is None
    assert parse_russian_currency("7707083893") is None
    assert parse_russian_currency("КПП 770701001") is None
    # Фаза 6.2: знак минус СОХРАНЯЕТСЯ, чтобы ограничение ge=0 схемы сработало.
    # Прежде значение превращалось в None, и отрицательная сумма была
    # неотличима от незаполненного поля, то есть терялась молча.
    assert parse_russian_currency("-500") == -500.0
    assert parse_russian_currency("-100.50 руб.") == -100.5
    # Действительные суммы разбираются
    assert parse_russian_currency("5000 руб.") == 5000.0
    assert parse_russian_currency("250.75 ₽") == 250.75


def test_c02_negative_amount_is_rejected_by_schema_not_dropped():
    """Отрицательная сумма обязана быть отвергнута с понятным сообщением."""
    from scan_reader.type_registry import get_registry

    plugin = get_registry().get("salary_deductions")
    finances = plugin.schema_cls.model_fields["finances"].annotation
    model = finances if hasattr(finances, "model_fields") else None
    if model is None:
        import typing

        model = typing.get_args(finances)[0]
    with pytest.raises(ValueError):
        model(debt_amount_rub=-500.0)


def test_c03_all_plugins_benchmark_config_paths():
    """C-03: All 9 plugins must have non-empty 'path' in all benchmark.json fields (no 'name' key)."""
    registry = get_registry()
    for pid, plugin in registry.enabled().items():
        bench_fields = plugin.benchmark_config.get("fields", [])
        assert len(bench_fields) > 0, f"Plugin {pid} has no benchmark fields"
        for idx, f in enumerate(bench_fields):
            assert "path" in f, f"Plugin {pid} field #{idx} is missing 'path'"
            assert f["path"], f"Plugin {pid} field #{idx} has empty 'path'"
            assert "name" not in f, f"Plugin {pid} field #{idx} still uses deprecated 'name'"


def test_c04_all_plugins_flat_columns_canonical_keys():
    """C-04: All 9 plugins must use canonical 'path'/'label'/'kind' in flat_columns.json."""
    registry = get_registry()
    for pid, plugin in registry.enabled().items():
        cols = plugin.flat_columns
        assert len(cols) > 0, f"Plugin {pid} has no flat columns"
        for idx, col in enumerate(cols):
            assert "path" in col, f"Plugin {pid} col #{idx} is missing 'path'"
            assert "label" in col, f"Plugin {pid} col #{idx} is missing 'label'"
            assert "kind" in col, f"Plugin {pid} col #{idx} is missing 'kind'"
            assert "field" not in col, f"Plugin {pid} col #{idx} still uses 'field'"
            assert "header" not in col, f"Plugin {pid} col #{idx} still uses 'header'"


def test_c06_generic_metrics_evaluator_uses_target_plugin():
    """C-06: Non-executive plugins (e.g. powers_of_attorney, hr_orders) are evaluated by their own schema."""
    poa_pred = {
        "file_name": "poa_test.pdf",
        "doc_type": "powers_of_attorney",
        "principal": {"name": "ООО Ромашка", "inn": "7707083893"},
        "representative": {"full_name": "Иванов И.И."},
        "valid_until": "31.12.2026"
    }
    poa_gt = {
        "file_name": "poa_test.pdf",
        "doc_type": "powers_of_attorney",
        "principal": {"name": "ООО Ромашка", "inn": "7707083893"},
        "representative": {"full_name": "Иванов И.И."},
        "valid_until": "31.12.2026"
    }

    # Benchmark evaluation
    eval_res = evaluate_dataset([poa_pred], [poa_gt], doc_type="powers_of_attorney")
    assert eval_res["mode"] == "benchmark"
    assert eval_res["average_quality_score_percent"] > 0
    doc_res = eval_res["documents"][0]
    # Check that it did NOT check executive fields like "Суд (Наименование)" or "Номер дела"
    assert "Суд (Наименование)" not in doc_res["field_scores"]
    assert "Номер дела" not in doc_res["field_scores"]

    # Autonomous evaluation
    auto_res = evaluate_dataset([poa_pred], ground_truth_docs=None, doc_type="powers_of_attorney")
    assert auto_res["mode"] == "autonomous"
    assert auto_res["average_quality_score_percent"] > 0
    auto_doc = auto_res["documents"][0]
    assert "Суд (Наименование)" not in auto_doc["field_scores"]


def test_c08_evaluate_dataset_mcp_directory_support():
    """C-08: evaluate_dataset handles directory paths as passed by MCP and CLI."""
    with tempfile.TemporaryDirectory() as temp_dir:
        res_dir = Path(temp_dir) / "output"
        gt_dir = Path(temp_dir) / "ground_truth"
        res_dir.mkdir()
        gt_dir.mkdir()

        # Create dummy extracted result
        doc = {
            "file_name": "test.pdf",
            "doc_type": "powers_of_attorney",
            "data": {"principal": {"name": "ООО Тест"}}
        }
        with open(res_dir / "test_Full.json", "w", encoding="utf-8") as f:
            json.dump(doc, f)

        # Call with directory paths and registry (as in MCP server)
        registry = get_registry()
        metrics = evaluate_dataset(res_dir, gt_dir, registry)
        assert isinstance(metrics, dict)
        summary = generate_run_summary(metrics)
        assert "overall_quality_score_percent" in summary
        assert "categories" in summary


def test_c09_mcp_scan_document_audits_extracted_data(monkeypatch, tmp_path):
    """C-09: MCP scan_document passes result['data'] and raw_text to ZeroTrustAuditor."""
    # H-20: временный каталог должен быть в whitelist разрешенных корней
    monkeypatch.setenv("SCANREADER_ALLOWED_DIRS", str(tmp_path))
    facade_mock = MagicMock()
    fake_result = {
        "file_name": "doc.pdf",
        "doc_type": "invoices_upd",
        "data": {
            "seller": {"name": "ООО Продавец", "inn": "7707083893"},
            "total_amount_rub": 1000.0
        }
    }
    facade_mock.process_single_document.return_value = fake_result
    facade_mock._extract_raw_text_for_audit.return_value = "Текст документа"

    server = ScanReaderMCPServer(facade=facade_mock)

    dummy_pdf = tmp_path / "doc.pdf"
    dummy_pdf.write_bytes(b"%PDF-dummy")

    try:
        with patch("scan_reader.verifier.auditor.ZeroTrustAuditor.audit_document") as mock_audit:
            mock_report = MagicMock()
            mock_report.to_dict.return_value = {"status": "verified"}
            mock_audit.return_value = mock_report

            server.call_tool("scan_document", {"file_path": str(dummy_pdf), "verify_zero_trust": True})

            mock_audit.assert_called_once()
            call_kwargs = mock_audit.call_args[1]
            assert call_kwargs["data"] == fake_result["data"]
            assert call_kwargs["raw_ocr_text"] == "Текст документа"
    finally:
        if dummy_pdf.exists():
            dummy_pdf.unlink()


def test_h20_mcp_path_whitelist(monkeypatch, tmp_path):
    """H-20: MCP отклоняет пути вне разрешенных корней."""
    monkeypatch.delenv("SCANREADER_ALLOWED_DIRS", raising=False)
    facade_mock = MagicMock()
    server = ScanReaderMCPServer(facade=facade_mock)

    outside_file = tmp_path / "outside_secret.txt"
    outside_file.write_text("secret", encoding="utf-8")

    result = server.call_tool("classify_document", {"file_path": str(outside_file)})
    assert result.get("isError") is True
    assert "denied" in result.get("error", "").lower()


def test_c11_h14_rate_limiter_timeout_and_bounded_semaphore():
    """C-11 & H-14: RateLimiter raises RateLimitTimeoutError and maintains semaphore invariant."""
    limiter = RateLimiter(rpm_limit=60, max_concurrency=1, enabled=True, timeout=0.01)

    # First acquisition succeeds
    assert limiter.acquire(timeout=0.1) is True
    assert limiter._semaphore._value == 0

    # Second acquisition with timeout=0.01 fails
    assert limiter.acquire(timeout=0.01) is False
    # Invariant: semaphore must NOT have grown
    assert limiter._semaphore._value == 0

    # Context manager raises RateLimitTimeoutError when saturated
    with pytest.raises(RateLimitTimeoutError):
        with limiter:
            pass

    # Invariant: semaphore must still be 0, not grown above max_concurrency
    assert limiter._semaphore._value == 0

    # Releasing the first acquired slot
    limiter.release()
    assert limiter._semaphore._value == 1

    # Over-release does not exceed max_concurrency
    limiter.release()
    assert limiter._semaphore._value == 1


def test_c12_json_exporter_stem_collision_prevention():
    """C-12: Two files with identical stem from different paths do not overwrite each other."""
    with tempfile.TemporaryDirectory() as temp_dir:
        doc1 = {
            "file_name": "akt.pdf",
            "file_path": "/folder_2024/akt.pdf",
            "doc_type": "acceptance_certificates",
            "data": {"contract_number": "2024-01"}
        }
        doc2 = {
            "file_name": "akt.pdf",
            "file_path": "/folder_2025/akt.pdf",
            "doc_type": "acceptance_certificates",
            "data": {"contract_number": "2025-01"}
        }

        path1 = save_single_document_json(doc1, temp_dir)
        path2 = save_single_document_json(doc2, temp_dir)

        assert path1 != path2
        assert os.path.exists(path1)
        assert os.path.exists(path2)

        with open(path1, "r", encoding="utf-8") as f1:
            data1 = json.load(f1)
        with open(path2, "r", encoding="utf-8") as f2:
            data2 = json.load(f2)

        assert data1["file_path"] == "/folder_2024/akt.pdf"
        assert data2["file_path"] == "/folder_2025/akt.pdf"
