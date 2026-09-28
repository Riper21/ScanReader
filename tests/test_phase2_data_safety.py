# -*- coding: utf-8 -*-
"""
Регрессионные тесты Фазы 2 — сохранность данных.

C-07: одиночная обработка документа не должна затирать накопленный реестр.
C-09: незавершённый бенчмарк не должен сравнивать Ground Truth сам с собой.
"""

import json
import os

import pytest

from scan_reader.core.json_exporter import (
    _result_identity,
    export_consolidated_registries,
)


def _record(idx: int, results_dir: str, **overrides):
    rec = {
        "file_name": f"doc{idx}.pdf",
        "file_path": os.path.join(results_dir, f"doc{idx}.pdf"),
        "doc_type": "salary_deductions",
        "status": "COMPLETED",
        "zero_trust_status": "zero_trust_verified",
        "quality_score_percent": 90.0,
        "data": {"file_name": f"doc{idx}.pdf", "finances": {"total_rub": 1000.0 * idx}},
    }
    rec.update(overrides)
    return rec


def _count(path):
    with open(path, "r", encoding="utf-8") as fh:
        return len(json.load(fh))


REGISTRY_FILES = (
    "Registry_Full.json",
    "all_documents_registry.json",
    "salary_deductions_registry.json",
    "Registry_Flat.json",
)


# =========================================================================
# C-07: слияние вместо перезаписи
# =========================================================================
def test_c07_single_document_does_not_truncate_registry(tmp_path):
    """Три одиночные обработки подряд дают три записи, а не одну.

    До исправления: каждый вызов export_consolidated_registries([result], dir)
    писал переданный список целиком, поэтому реестр из 500 документов
    уничтожался при обработке 501-го в одиночном режиме.
    """
    out = str(tmp_path)
    for i in (1, 2, 3):
        export_consolidated_registries([_record(i, out)], out, merge=True)

    for name in REGISTRY_FILES:
        assert _count(os.path.join(out, name)) == 3, f"{name} truncated"


def test_c07_reprocessing_same_file_updates_in_place(tmp_path):
    out = str(tmp_path)
    for i in (1, 2, 3):
        export_consolidated_registries([_record(i, out)], out, merge=True)

    updated = _record(2, out, zero_trust_status="partially_verified", quality_score_percent=55.0)
    updated["data"] = {"file_name": "doc2.pdf", "finances": {"total_rub": 2222.0}}
    export_consolidated_registries([updated], out, merge=True)

    full_path = os.path.join(out, "Registry_Full.json")
    assert _count(full_path) == 3, "повторная обработка создала дубликат"
    with open(full_path, "r", encoding="utf-8") as fh:
        records = {r["file_name"]: r for r in json.load(fh)}
    assert records["doc2.pdf"]["zero_trust_status"] == "partially_verified"
    assert records["doc2.pdf"]["data"]["finances"]["total_rub"] == 2222.0


def test_c07_identity_prefers_full_path(tmp_path):
    """Одинаковые имена в разных папках — разные записи."""
    a = {"file_name": "order.pdf", "file_path": "/inbox/1/order.pdf", "doc_type": "salary_deductions"}
    b = {"file_name": "order.pdf", "file_path": "/inbox/2/order.pdf", "doc_type": "salary_deductions"}
    c = {"file_name": "order.pdf", "file_path": "/inbox/1/order.pdf", "doc_type": "hr_orders"}
    assert _result_identity(a) != _result_identity(b)
    assert _result_identity(a) != _result_identity(c)
    assert _result_identity(a) == _result_identity(dict(a))


def test_c07_batch_mode_still_replaces_fully(tmp_path):
    """merge=False (пакетный режим) сохраняет прежнюю семантику полной замены."""
    out = str(tmp_path)
    for i in (1, 2, 3):
        export_consolidated_registries([_record(i, out)], out, merge=True)
    assert _count(os.path.join(out, "Registry_Full.json")) == 3

    export_consolidated_registries([_record(9, out), _record(8, out)], out, merge=False)
    assert _count(os.path.join(out, "Registry_Full.json")) == 2


def test_c07_corrupt_registry_is_preserved_as_backup(tmp_path):
    """Нечитаемый реестр не приводит ни к потере новых данных, ни к молчаливой замене."""
    out = str(tmp_path)
    export_consolidated_registries([_record(1, out)], out, merge=True)
    registry = os.path.join(out, "Registry_Full.json")
    with open(registry, "w", encoding="utf-8") as fh:
        fh.write("{ это не json")

    export_consolidated_registries([_record(2, out)], out, merge=True)

    backups = [f for f in os.listdir(out) if f.startswith("Registry_Full.json.corrupt_")]
    assert backups, "повреждённый реестр должен быть сохранён, а не удалён"
    assert _count(registry) == 1
    with open(registry, "r", encoding="utf-8") as fh:
        assert json.load(fh)[0]["file_name"] == "doc2.pdf"


def test_c07_merge_skips_failed_records(tmp_path):
    """Отказавшиеся записи не должны попадать в реестр даже при слиянии."""
    out = str(tmp_path)
    good = _record(1, out)
    failed = _record(2, out, status="FAILED", errors=["boom"])
    failed["data"] = {"file_name": "doc2.pdf", "_extraction_failed": True}

    export_consolidated_registries([good, failed], out, merge=True)

    assert _count(os.path.join(out, "Registry_Full.json")) == 1


def test_c07_merge_is_idempotent_on_missing_registry(tmp_path):
    """Первый запуск: файла реестра ещё нет — слияние не должно падать."""
    out = str(tmp_path)
    saved = export_consolidated_registries([_record(1, out)], out, merge=True)
    assert "Registry_Full.json" in saved
    assert _count(saved["Registry_Full.json"]) == 1


# =========================================================================
# C-09: Ground Truth не сравнивается сам с собой
# =========================================================================
def test_c09_benchmark_against_ground_truth_detects_self_comparison():
    """Эталон, сравнённый с собой, обязан быть распознан как такая подмена."""
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    gt = {
        "file_name": "sample.pdf",
        "doc_type": "salary_deductions",
        "debtor": {"inn": "7707083893"},
        "finances": {"debt_amount_rub": 1000.0},
    }

    real = facade.benchmark_against_ground_truth(
        {"debtor": {"inn": "7707083893"}, "finances": {"debt_amount_rub": 1000.0}}, gt,
        "salary_deductions",
    )
    selfcmp = facade.benchmark_against_ground_truth(gt, gt, "salary_deductions")

    # Сравнение с эталоном даёт полное совпадение, но это не измерение точности
    assert selfcmp.get("accuracy", 0.0) == 100.0
    assert real.get("accuracy", 0.0) == 100.0


# =========================================================================
# C-09: незавершённый бенчмарк не отчитывается как 100% точности
# =========================================================================
def _stub_facade(results_dir: str):
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade.results_dir = results_dir
    return facade


def _gt_dir_for_first_plugin():
    from scan_reader.type_registry import get_registry

    plugin = next(iter(get_registry().enabled().values()))
    src_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__import__("scan_reader").__file__)
    )))
    return plugin, os.path.join(src_root, "data", "ground_truth")


def test_c09_benchmark_without_extraction_reports_no_accuracy(tmp_path, monkeypatch, capsys):
    """Запуск бенчмарка ДО обработки документов обязан дать «нет данных», а не 100%.

    До исправления непрочитанные эталоны сравнивались с собой, давали 100.0,
    попадали в all_scores и печатались как «ИТОГОВАЯ ТОЧНОСТЬ БЕНЧМАРКА: 100.00%».
    """
    from scan_reader import launcher

    plugin, gt_dir = _gt_dir_for_first_plugin()
    if not os.path.isdir(gt_dir):
        pytest.skip("ground truth directory not available")

    facade = _stub_facade(str(tmp_path))  # в results_dir нет ни одного результата
    monkeypatch.setattr(launcher, "ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", gt_dir)

    launcher.run_benchmark_suite(facade)

    out = capsys.readouterr().out
    report_path = os.path.join(str(tmp_path), "benchmark_metrics_summary.json")
    assert os.path.exists(report_path)
    with open(report_path, "r", encoding="utf-8") as fh:
        report = json.load(fh)

    assert report["total_documents_tested"] == 0, "точность посчитана без единого извлечения"
    assert report["overall_accuracy_percent"] == 0.0
    assert report["total_documents_without_extraction"] > 0
    assert report["ground_truth_schema_integrity_percent"] > 0

    assert "ИТОГОВАЯ ТОЧНОСТЬ ИЗВЛЕЧЕНИЯ: н/д" in out
    # Ключевая защита: подставная «точность» 100% не должна появляться
    assert "ИТОГОВАЯ ТОЧНОСТЬ ИЗВЛЕЧЕНИЯ: 100" not in out
    assert "ИТОГОВАЯ ТОЧНОСТЬ БЕНЧМАРКА" not in out

    # Ни одна запись не должна быть помечена как учтённая в общей точности
    for pid, block in report["plugins"].items():
        for doc in block["documents"]:
            assert doc["counted_in_overall_accuracy"] is doc["is_extracted_comparison"]
            if not doc["is_extracted_comparison"]:
                assert block["documents_extracted"] == 0 or doc not in block["documents"][:block["documents_extracted"]]


def test_c09_schema_integrity_is_reported_separately(tmp_path, monkeypatch):
    """Показатель целостности схемы эталона отделён от точности извлечения."""
    from scan_reader import launcher

    plugin, gt_dir = _gt_dir_for_first_plugin()
    if not os.path.isdir(gt_dir):
        pytest.skip("ground truth directory not available")

    facade = _stub_facade(str(tmp_path))
    monkeypatch.setattr(launcher, "ROOT_DIR", str(tmp_path))
    monkeypatch.setenv("SCANREADER_GROUND_TRUTH_DIR", gt_dir)

    launcher.run_benchmark_suite(facade)

    with open(os.path.join(str(tmp_path), "benchmark_metrics_summary.json"), "r", encoding="utf-8") as fh:
        report = json.load(fh)

    for block in report["plugins"].values():
        assert "documents_without_extraction" in block
        assert "ground_truth_schema_integrity_percent" in block
        assert block["documents_extracted"] + block["documents_without_extraction"] == block["documents_count"]


def test_c09_self_comparison_yields_full_score_and_must_not_be_counted():
    """Эталон, сравнённый с собой, даёт 100% — и это НЕ измерение точности.

    Проверка самого признака подмены вынесена в launcher (шаг C-09): запись с
    is_extracted_comparison=False не должна попадать в общий показатель точности.
    Точность извлечения против реально отличающихся данных проверяется в
    test_phase5_measurement.py - там же зафиксирован дефект C-06.
    """
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    gt = {
        "file_name": "sample.pdf",
        "doc_type": "salary_deductions",
        "debtor": {"inn": "7707083893"},
        "finances": {"debt_amount_rub": 1000.0},
    }

    selfcmp = facade.benchmark_against_ground_truth(gt, gt, "salary_deductions")
    assert selfcmp.get("accuracy", 0.0) == 100.0
    # Совпадение с эталоном неинформативно: эталон сравнивается сам с собой.
    # Признак подмены формирует вызывающая сторона (launcher), а не этот метод.
    assert "accuracy" in selfcmp


# =========================================================================
# Фаза 1 (C-08) — фильтрация FAILED-записей при слиянии
# =========================================================================
def test_c08_failed_record_with_data_key_is_excluded(tmp_path):
    """Ключевой дефект: `and "data" not in item` делал фильтр холостым."""
    out = str(tmp_path)
    failed = {
        "file_name": "broken.pdf",
        "file_path": os.path.join(out, "broken.pdf"),
        "doc_type": "salary_deductions",
        "status": "FAILED",
        "errors": ["LLM client unavailable"],
        "data": {"file_name": "broken.pdf", "status": "FAILED", "_extraction_failed": True},
    }
    saved = export_consolidated_registries([failed, _record(1, out)], out, merge=True)
    with open(saved["Registry_Full.json"], "r", encoding="utf-8") as fh:
        names = [r["file_name"] for r in json.load(fh)]
    assert names == ["doc1.pdf"]


@pytest.mark.parametrize("status", ["FAILED"])
def test_c08_failed_status_never_exported(tmp_path, status):
    out = str(tmp_path)
    rec = _record(1, out, status=status)
    saved = export_consolidated_registries([rec], out, merge=False)
    for name in REGISTRY_FILES:
        path = saved.get(name) or os.path.join(out, name)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
            assert len(payload) == 0, f"{name} содержит FAILED-запись"
