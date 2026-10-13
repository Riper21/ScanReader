# -*- coding: utf-8 -*-
"""
0.9.2: честность конвейера — счётчики сбоев, флаг ручной проверки,
обнаружение пропущенных файлов.

Кейс холодного прогона: 13 документов провалились из-за недоступного VLM,
а этап 5 доложил «Успешно обработано: 13 | Сбоев: 0», каждый FAILED
помечался [OK], .gif молча исчез из пакета, а флаг
requires_human_review оставался false.
"""

from unittest.mock import patch

from scan_reader.core.verification_export import (
    normalize_doc_data,
    requires_human_review,
)
from scan_reader.file_processor import scan_directory_for_documents


# =========================================================================
# Флаг ручной проверки для провальной экстракции
# =========================================================================
def test_failed_status_requires_human_review():
    assert requires_human_review({"status": "FAILED"}) is True
    # даже с совещательным статусом верификации данных нет — человек нужен
    assert requires_human_review(
        {"status": "FAILED", "zero_trust_status": "vlm_unverified"}
    ) is True


def test_lowercase_failed_status_requires_human_review():
    assert requires_human_review({"status": "failed"}) is True


def test_advisory_status_without_failure_still_does_not_block():
    """Совещательные статусы при успешной экстракции флаг не поднимают."""
    assert requires_human_review(
        {"status": "COMPLETED", "zero_trust_status": "vlm_unverified"}
    ) is False


def test_normalize_carries_review_flag_for_failed_document():
    rec = {
        "file_name": "x.pdf",
        "status": "FAILED",
        "data": {},
        "zero_trust_status": "vlm_unverified",
        "zero_trust": {"status": "vlm_unverified", "is_valid": False, "issues": []},
    }
    out = normalize_doc_data(rec)
    assert out["requires_human_review"] is True


# =========================================================================
# Обнаружение документов: gif и предупреждение о пропущенных
# =========================================================================
def test_scan_directory_discovers_gif(tmp_path):
    (tmp_path / "anim.gif").write_bytes(b"GIF89a")
    found = scan_directory_for_documents(str(tmp_path))
    assert any(f.endswith("anim.gif") for f in found)


def test_scan_directory_warns_about_skipped_files(tmp_path):
    import io
    import logging as _logging

    from scan_reader.core.utils import get_logger

    (tmp_path / "doc.pdf").write_bytes(b"x")
    (tmp_path / "junk.xyz").write_text("x")
    (tmp_path / ".gitkeep").write_text("")

    # Хендлер get_logger держит исходный sys.stderr (H-05), capsys его не
    # видит: записываем во временный хендлер того же логгера.
    gate_logger = get_logger("file_processor")
    stream = io.StringIO()
    probe = _logging.StreamHandler(stream)
    gate_logger.addHandler(probe)
    try:
        found = scan_directory_for_documents(str(tmp_path))
    finally:
        gate_logger.removeHandler(probe)

    assert len(found) == 1 and found[0].endswith("doc.pdf")
    logged = stream.getvalue()
    assert "Пропущено 1 неподдерживаемых файлов" in logged
    assert ".xyz: 1 шт." in logged


# =========================================================================
# Этап 4/5: сбои видны в сводке
# =========================================================================
def test_stage4_marks_failed_documents(tmp_path, capsys):
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade.results_dir = str(tmp_path)
    src = tmp_path / "a.pdf"
    src.write_text("x")
    failed = {
        "file_name": "a.pdf",
        "file_path": str(src),
        "doc_type": "salary_deductions",
        "status": "FAILED",
        "zero_trust_status": "vlm_unverified",
        "quality_score_percent": 0.0,
    }
    with patch.object(facade, "process_single_document", return_value=failed):
        results, errors = facade._batch_stage4_extract({"salary_deductions": [str(src)]})
    out = capsys.readouterr().out
    assert "[ОШИБКА" in out
    assert "[OK" not in out
    assert results[0]["status"] == "FAILED"
    assert errors == []


def test_stage5_counts_failed_documents_not_as_processed(tmp_path, capsys):
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade.results_dir = str(tmp_path)
    results = [{"doc_type": "salary_deductions", "status": "FAILED"} for _ in range(2)]
    facade._batch_stage5_metrics({"salary_deductions": ["a", "b"]}, results)
    out = capsys.readouterr().out
    # P1.7: единый источник счёта — status=="FAILED". Раньше к failed
    # прибавлялся error_count того же прогона, и один сбой задваивался.
    assert "Успешно обработано: 0 | Сбоев: 2" in out


def test_stage4_and_stage5_agree_on_failure_count(tmp_path, capsys):
    """P1.7: сбой-исключение считается один раз, а не дважды.

    Один документ из трёх падает исключением: он попадает в results со
    статусом FAILED и в errors, но сводка обязана назвать «Сбоев: 1».
    """
    from scan_reader.facade import LegalDocPlatformFacade
    from scan_reader.type_registry import get_registry

    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade.results_dir = str(tmp_path)
    files = []
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        p = tmp_path / name
        p.write_text("x")
        files.append(str(p))
    ok = {"doc_type": "hr_orders", "status": "COMPLETED", "file_name": "ok"}
    with patch.object(
        facade,
        "process_single_document",
        side_effect=[ok, RuntimeError("сбой модели"), ok],
    ):
        results, errors = facade._batch_stage4_extract({"hr_orders": files})

    assert len(errors) == 1
    assert sum(1 for r in results if r.get("status") == "FAILED") == 1

    facade._batch_stage5_metrics({"hr_orders": files}, results)
    out = capsys.readouterr().out
    assert "Успешно обработано: 2 | Сбоев: 1" in out
