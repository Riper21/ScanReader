# -*- coding: utf-8 -*-
"""
Tests for modern ScanReader CLI subcommands:
classify, verify, doctor, export, and --json output mode.
"""

import os
import json
import pytest
from unittest.mock import patch
from scan_reader.cli import build_parser, main


def test_cli_parser_subcommands():
    parser = build_parser()
    for cmd in ["run", "classify", "verify", "export", "benchmark", "doctor", "mcp"]:
        if cmd == "run":
            args = parser.parse_args(["run", "dummy.pdf"])
        elif cmd in ("classify", "verify"):
            args = parser.parse_args([cmd, "dummy.txt"])
        else:
            args = parser.parse_args([cmd])
        assert args.command == cmd


def test_cli_verify_subcommand(tmp_path, capsys):
    json_file = tmp_path / "doc.json"
    # Реквизиты взяты из verification.json плагина salary_deductions: у этого типа
    # поля «debtor» нет (используется плоское debtor_name), а ИНН получателя
    # платежа лежит в payment_details.recipient_inn.
    content = {
        "doc_type": "salary_deductions",
        "payment_details": {"recipient_inn": "7707083893"},
        "finances": {"debt_amount_rub": 1000.0, "fee_penalty_rub": 70.0, "total_deduction_rub": 1070.0},
    }
    json_file.write_text(json.dumps(content), encoding="utf-8")

    with patch("sys.argv", ["scan-reader", "verify", str(json_file), "--json"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    captured = capsys.readouterr()
    report = json.loads(captured.out.strip())
    assert report["is_valid"] is True
    assert report["status"] == "zero_trust_verified"
    assert report["details"]["checksums_verified_ok"] >= 1
    assert report["details"]["math_verified_ok"] is True


def test_cli_doctor_subcommand(capsys):
    """M-28: doctor-тест работает на заmocked диагностике без реальных сетевых вызовов."""
    mock_diag = {"status": "ok", "ping_ms": 12}
    with patch("scan_reader.core.diagnostics.run_vlm_diagnostics", return_value=mock_diag):
        with patch("sys.argv", ["scan-reader", "doctor", "--json"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["status"] == "ok"


def test_cli_run_format_options(tmp_path, capsys):
    """Тест поддержки форматов flat, full и both в CLI run."""
    dummy_scan = tmp_path / "order_123.pdf"
    dummy_scan.write_text("scan content", encoding="utf-8")

    out_dir = tmp_path / "Результаты"
    out_dir.mkdir(parents=True, exist_ok=True)

    full_json = out_dir / "order_123_Full.json"
    full_json.write_text(json.dumps({"file_name": "order_123.pdf", "data": {"claim_subject": "Долг"}}), encoding="utf-8")

    flat_json = out_dir / "order_123_Flat.json"
    flat_json.write_text(json.dumps({"FileName": "order_123.pdf", "ClaimSubject": "Долг"}), encoding="utf-8")

    fake_result = {
        "file_name": "order_123.pdf",
        "doc_type": "enforcement_orders",
        "status": "COMPLETED",
        "data": {"claim_subject": "Долг"}
    }

    with patch("scan_reader.facade.LegalDocPlatformFacade.process_single_document", return_value=fake_result):
        # 1. Формат flat
        with patch("sys.argv", ["scan-reader", "run", str(dummy_scan), "-o", str(out_dir), "-f", "flat"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        captured_flat = capsys.readouterr()
        assert captured_flat.out.strip().endswith("order_123_Flat.json")

        # 2. Формат full
        with patch("sys.argv", ["scan-reader", "run", str(dummy_scan), "-o", str(out_dir), "-f", "full"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        captured_full = capsys.readouterr()
        assert captured_full.out.strip().endswith("order_123_Full.json")

        # 3. Формат both
        with patch("sys.argv", ["scan-reader", "run", str(dummy_scan), "-o", str(out_dir), "-f", "both"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 0
        captured_both = capsys.readouterr()
        lines = [line.strip() for line in captured_both.out.strip().splitlines() if line.strip()]
        assert len(lines) == 2
        assert lines[0].endswith("order_123_Full.json")
        assert lines[1].endswith("order_123_Flat.json")


def test_cli_export_subcommand(tmp_path, capsys):
    """Тест команды консолидации export с формированием реестров."""
    out_dir = tmp_path / "Результаты"
    out_dir.mkdir(parents=True, exist_ok=True)

    doc_full = out_dir / "doc1_Full.json"
    doc_full.write_text(json.dumps({
        "file_name": "doc1.pdf",
        "doc_type": "salary_deductions",
        "data": {"doc_number": "100", "finances": {"total_deduction_rub": 5000.0}}
    }), encoding="utf-8")

    with patch("sys.argv", ["scan-reader", "export", str(out_dir), "--json"]):
        with pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0

    captured = capsys.readouterr()
    res = json.loads(captured.out.strip())
    assert res["records_count"] == 1
    assert "Registry_Full.json" in res["exported_files"]
    assert "Registry_Flat.json" in res["exported_files"]


def test_default_folders_incoming_and_output():
    """Проверка того, что фасад и экспортер по умолчанию используют output/."""
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.excel_exporter import LegalExcelExporter

    facade = LegalDocPlatformFacade()
    assert os.path.basename(facade.results_dir) == "output"
    assert os.path.exists(facade.results_dir)

    exporter = LegalExcelExporter()
    assert os.path.basename(exporter.output_dir) == "output"
    assert os.path.exists(exporter.output_dir)


