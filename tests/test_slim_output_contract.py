# -*- coding: utf-8 -*-
"""
0.9.3: контракт тонкого вывода.

Прогон раньше оставлял 56 файлов на 13 документов: карточка писалась
четырежды (Full, raw, {type}, 1C_Импорт), а рядом с реестром лежали его
дубли (Registry_Flat, all_documents_registry, реестры по типам, 1С-карточки,
xlsx-сводка метрик). Теперь на документ — строго Full и Flat, на пакет —
Registry_Full.json и Excel-блок.
"""

import json
import os

from scan_reader.core.json_exporter import (
    export_consolidated_registries,
    save_single_document_json,
)


def _doc(file_name="doc.pdf", doc_type="executive_documents"):
    return {
        "file_name": file_name,
        "file_path": f"/inbox/{file_name}",
        "doc_type": doc_type,
        "status": "COMPLETED",
        "zero_trust_status": "zero_trust_verified",
        "data": {"case_number": "А40-1/2021", "finances": {"total_rub": 1000.0}},
    }


# =========================================================================
# Карточка документа: ровно два файла
# =========================================================================
def test_single_document_writes_exactly_two_files(tmp_path):
    out = str(tmp_path)
    save_single_document_json(_doc(), out)
    assert set(os.listdir(out)) == {"doc_Full.json", "doc_Flat.json"}


def test_no_legacy_copies_and_1c_import_folder(tmp_path):
    out = str(tmp_path)
    save_single_document_json(_doc("order.pdf", "salary_deductions"), out)
    assert set(os.listdir(out)) == {"order_Full.json", "order_Flat.json"}
    assert not os.path.exists(os.path.join(out, "1C_Импорт"))


# =========================================================================
# Пакет: ровно один реестр
# =========================================================================
def test_batch_writes_only_registry_full(tmp_path):
    out = str(tmp_path)
    saved = export_consolidated_registries([_doc(), _doc("two.pdf")], out)
    assert set(saved) == {"Registry_Full.json"}
    assert set(os.listdir(out)) == {"Registry_Full.json"}
    with open(saved["Registry_Full.json"], encoding="utf-8") as fh:
        assert len(json.load(fh)) == 2


def test_registry_full_keeps_raw_records_with_verification(tmp_path):
    out = str(tmp_path)
    export_consolidated_registries([_doc()], out, merge=True)
    with open(os.path.join(out, "Registry_Full.json"), encoding="utf-8") as fh:
        rec = json.load(fh)[0]
    # исходная result-запись: данные и верификация на месте
    assert rec["data"]["finances"]["total_rub"] == 1000.0
    assert rec["zero_trust_status"] == "zero_trust_verified"


# =========================================================================
# Удалённые сущности не возвращаются
# =========================================================================
def test_run_summary_excel_exporter_removed():
    """xlsx-сводка метрик удалена: Excel-блок один — Сводный_реестр_документов."""
    import scan_reader.core.metrics_evaluator as me

    assert not hasattr(me, "export_run_summary_excel")


def test_stage5_does_not_write_metrics_xlsx(tmp_path, capsys):
    """run_metrics_summary.json/.md остаются, xlsx-дубликат не пишется."""
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade.results_dir = str(tmp_path)

    doc = _doc("d1.pdf", "salary_deductions")
    doc["doc_type"] = "salary_deductions"
    # Стадия 5 считает метрики только по завершённым документам
    facade._batch_stage5_metrics({"salary_deductions": ["d1.pdf"]}, [doc], 0)
    files = set(os.listdir(tmp_path))
    assert "run_metrics_summary.json" in files
    assert "run_metrics_summary.md" in files
    assert "run_metrics_summary.xlsx" not in files


# =========================================================================
# MCP export_results: документ грузится один раз
# =========================================================================
def test_mcp_export_results_no_double_count(tmp_path):
    """Раньше _Full/_raw/карточка грузились как три разных документа."""
    from scan_reader.mcp.server import ScanReaderMCPServer

    out = str(tmp_path)
    save_single_document_json(_doc(), out)
    export_consolidated_registries([_doc()], out)

    server = ScanReaderMCPServer()
    result = server.call_tool("export_results", {"results_dir": out, "target_format": "1c"})
    assert result["records_count"] == 1
    assert set(result["exported_files"]) == {"Registry_Full.json"}
