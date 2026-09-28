# -*- coding: utf-8 -*-
"""
Тесты Фазы 9 — покрытие непроверенных путей.

Приоритеты выбраны по покрытию на момент начала фазы:
  launcher.py            22 %  (260 непокрытых строк)
  core/ocr.py            39 %
  mcp/server.py          45 %  (5 инструментов не покрыты)
  file_processor.py      49 %  (рендер PDF/TIFF, Dual-Zone)
  core/diagnostics.py    48 %
  cli.py                 50 %  (ни один код возврата)
  facade.py              50 %  (оба вызова LLM)
  doc_types/enforcement_orders/schema.py   0 %  (модуль не импортировался ни разу)
"""

import json
import os
from pathlib import Path

import pytest
from PIL import Image

from scan_reader.type_registry import get_registry


# =========================================================================
# enforcement_orders/schema.py — 0 % покрытия
# =========================================================================
def test_enforcement_order_schema_module_is_importable():
    """Модуль схемы не импортировался ни одним тестом: целый файл в 0 %."""
    import importlib.util

    path = (
        Path(__file__).resolve().parent.parent
        / "src" / "scan_reader" / "doc_types" / "enforcement_orders" / "schema.py"
    )
    spec = importlib.util.spec_from_file_location("probe_eo_schema", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "EnforcementOrderDoc")


def test_enforcement_order_schema_builds_from_minimal_payload():
    cls = get_registry().get("enforcement_orders").schema_cls
    doc = cls(
        doc_date="17.06.2015",
        ip_number="98765/23/50026-ИП",
        claim_subject="Сформировать земельный участок",
        claimant={"name": "ООО Ромашка", "inn": "7707083893"},
        debtor={"name": "Иванов Иван Иванович", "inn": "7802312751"},
        finances={"main_debt_rub": 50000.0, "court_costs_rub": 6000.0, "total_rub": 56000.0},
        court={"name": "Химкинский городской суд", "case_number": "2-1234/2015",
               "act_date": "10.03.2015"},
        fssp={"name": "Химкинский РОСП", "officer": "Смирнов В.П."},
    )
    assert doc.finances.total_rub == 56000.0
    assert doc.debtor.inn == "7802312751"
    assert doc.doc_type


def test_enforcement_order_schema_rejects_invalid_inn():
    """Схема обязана отвергать невалидный ИНН, а не принимать его (Фаза 6.2)."""
    cls = get_registry().get("enforcement_orders").schema_cls
    with pytest.raises(ValueError):
        cls(debtor={"name": "Иванов", "inn": "7801234567"})


def test_enforcement_order_schema_rejects_negative_amount():
    cls = get_registry().get("enforcement_orders").schema_cls
    with pytest.raises(ValueError):
        cls(finances={"total_rub": -100.0})


def test_enforcement_order_normalises_formatted_amounts():
    cls = get_registry().get("enforcement_orders").schema_cls
    doc = cls(finances={"main_debt_rub": "50 000,50", "total_rub": "50 000,50"})
    assert doc.finances.main_debt_rub == 50000.5


# =========================================================================
# MCP-сервер: 5 инструментов
# =========================================================================
@pytest.fixture
def mcp_server(tmp_path, monkeypatch):
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.mcp.server import ScanReaderMCPServer

    monkeypatch.setenv("SCANREADER_ALLOWED_DIRS", str(tmp_path))
    facade = LegalDocPlatformFacade()
    facade.results_dir = str(tmp_path / "out")
    os.makedirs(facade.results_dir, exist_ok=True)
    return ScanReaderMCPServer(facade=facade)


def test_mcp_tool_list_declares_all_tools(mcp_server):
    """Список инструментов объявляется через JSON-RPC tools/list."""
    from scan_reader.mcp.server import handle_jsonrpc

    resp = handle_jsonrpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}, mcp_server)
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {
        "scan_document", "classify_document", "verify_legal_data",
        "run_benchmark", "export_results",
    }


def test_mcp_scan_document_end_to_end(mcp_server, tmp_path, monkeypatch):
    """Полный путь: файл -> обработка -> Zero-Trust-отчёт."""
    scan = tmp_path / "order.jpg"
    Image.new("L", (800, 1100), color=210).save(scan)

    facade = mcp_server.facade
    monkeypatch.setattr(
        facade, "classify_document", lambda *a, **k: ("enforcement_orders", 1.0, "user_specified")
    )
    monkeypatch.setattr(facade, "extract_document_data", lambda *a, **k: {
        "debtor": {"inn": "7707083893", "name": "Иванов"},
        "finances": {"main_debt_rub": 50000.0, "court_costs_rub": 6000.0, "total_rub": 56000.0},
    })
    monkeypatch.setattr(facade, "_reference_text_for_gate", lambda p: ("текст", "text_layer"))
    monkeypatch.setattr(facade, "_get_client", lambda: None)

    result = mcp_server.call_tool("scan_document", {"file_path": str(scan)})
    assert result["status"] in ("COMPLETED", "NEEDS_REVIEW")
    assert "zero_trust_report" in result
    assert result["zero_trust_report"]["status"]


def test_mcp_scan_document_without_verification(mcp_server, tmp_path, monkeypatch):
    scan = tmp_path / "o2.jpg"
    Image.new("L", (400, 500), color=200).save(scan)
    facade = mcp_server.facade
    monkeypatch.setattr(facade, "classify_document", lambda *a, **k: ("hr_orders", 1.0, "user_specified"))
    monkeypatch.setattr(facade, "extract_document_data", lambda *a, **k: {"doc_number": "1"})
    monkeypatch.setattr(facade, "_reference_text_for_gate", lambda p: (None, "none"))
    monkeypatch.setattr(facade, "_get_client", lambda: None)

    result = mcp_server.call_tool(
        "scan_document", {"file_path": str(scan), "verify_zero_trust": False}
    )
    assert "zero_trust_report" not in result


def test_mcp_scan_document_rejects_missing_file(mcp_server):
    result = mcp_server.call_tool("scan_document", {"file_path": "/нет/такого/файла.jpg"})
    assert result.get("isError") is True
    assert "not found" in result["error"].lower()


def test_mcp_scan_document_enforces_allowed_dirs(mcp_server, tmp_path, monkeypatch):
    """H-20: доступ ограничен корнями из SCANREADER_ALLOWED_DIRS."""
    outside = tmp_path.parent / "вне-допустимых-корней.jpg"
    Image.new("L", (10, 10)).save(outside)
    try:
        result = mcp_server.call_tool("scan_document", {"file_path": str(outside)})
        assert result.get("isError") is True
        assert "Access denied" in result["error"]
    finally:
        outside.unlink(missing_ok=True)


def test_mcp_classify_document(mcp_server, tmp_path, monkeypatch):
    scan = tmp_path / "c.jpg"
    Image.new("L", (100, 100)).save(scan)
    monkeypatch.setattr(
        mcp_server.facade, "classify_document", lambda *a, **k: ("hr_orders", 0.95, "heuristic_path")
    )
    result = mcp_server.call_tool("classify_document", {"file_path": str(scan)})
    assert result == {
        "file_path": str(scan), "doc_type": "hr_orders",
        "confidence": 0.95, "method": "heuristic_path",
    }


def test_mcp_classify_document_missing_file(mcp_server):
    result = mcp_server.call_tool("classify_document", {"file_path": "/нет/файла.jpg"})
    assert result.get("isError") is True


def test_mcp_verify_legal_data(mcp_server):
    result = mcp_server.call_tool("verify_legal_data", {
        "data": {
            "payment_details": {"recipient_inn": "7707083893"},
            "finances": {"debt_amount_rub": 1000.0, "fee_penalty_rub": 70.0,
                         "total_deduction_rub": 1070.0},
        },
        "doc_type": "salary_deductions",
    })
    assert result["is_valid"] is True
    assert result["status"] == "zero_trust_verified"


def test_mcp_verify_legal_data_accepts_raw_text(mcp_server):
    """Передача эталона переключает гейт: без него статус иной."""
    result = mcp_server.call_tool("verify_legal_data", {
        "data": {"debtor": {"inn": "7707083893", "name": "Иванов Иван Иванович"}},
        "doc_type": "enforcement_orders",
        "raw_ocr_text": "Постановление суда. Иванов Иван Иванович, ИНН 7707083893, взыскать 1 рубль.",
    })
    assert result["details"]["gate_executed"] is True
    assert not [i for i in result["issues"] if i["code"] == "HALLUCINATION_RISK"]


def test_mcp_run_benchmark_reports_missing_ground_truth_dir(mcp_server, tmp_path):
    """Без каталога эталонов инструмент обязан сообщить об этом, а не молчать."""
    result = mcp_server.call_tool(
        "run_benchmark", {"ground_truth_path": str(tmp_path / "нет-эталонов")}
    )
    assert result.get("isError") is True
    assert "Ground truth directory not found" in result["error"]


def test_mcp_run_benchmark_with_real_ground_truth(mcp_server, tmp_path):
    """evaluate_dataset принимает каталоги (C-08) и возвращает сводку запуска."""
    gt_dir = tmp_path / "ground_truth"
    gt_dir.mkdir()
    (gt_dir / "hr_orders.json").write_text(
        json.dumps([{"file_name": "a.pdf", "doc_date": "15.01.2023",
                     "doc_number": "1", "organization_inn": "7707083893"}], ensure_ascii=False),
        encoding="utf-8",
    )
    results_dir = Path(mcp_server.facade.results_dir)
    (results_dir / "a_Full.json").write_text(
        json.dumps({
            "file_name": "a.pdf", "doc_type": "hr_orders", "status": "COMPLETED",
            "data": {"doc_number": "1", "doc_date": "15.01.2023",
                     "organization_inn": "7707083893"},
        }, ensure_ascii=False),
        encoding="utf-8",
    )

    summary = mcp_server.call_tool(
        "run_benchmark", {"ground_truth_path": str(gt_dir)}
    )
    assert "overall_quality_score_percent" in summary
    assert "categories" in summary
    assert "hr_orders" in summary["categories"]
    assert summary["categories"]["hr_orders"]["documents_measured_in_benchmark_mode"] == 1


def test_mcp_export_results(mcp_server, tmp_path):
    scan = tmp_path / "e.jpg"
    Image.new("L", (100, 100)).save(scan)
    facade = mcp_server.facade
    facade.classify_document = lambda *a, **k: ("hr_orders", 1.0, "user_specified")
    facade.extract_document_data = lambda *a, **k: {"doc_number": "1"}
    facade._reference_text_for_gate = lambda p: (None, "none")
    facade._get_client = lambda: None

    mcp_server.call_tool("scan_document", {"file_path": str(scan)})
    export = mcp_server.call_tool("export_results", {"results_dir": facade.results_dir})
    assert isinstance(export, dict)


def test_mcp_unknown_tool(mcp_server):
    result = mcp_server.call_tool("no_such_tool", {})
    assert result.get("isError") is True
