# -*- coding: utf-8 -*-
"""
0.9.2: `scan-reader run` принимает каталог и честно агрегирует коды возврата.

Раньше handle_run требовал is_file() и отвергал каталог вопреки тексту помощи
«Путь к файлу скана или каталогу», а провальная экстракция возвращала код 0.
"""

from unittest.mock import MagicMock, patch

from scan_reader.cli import (
    EXIT_DISCREPANCY,
    EXIT_ERROR,
    EXIT_FALLBACK,
    EXIT_OK,
    _batch_exit_code,
    _result_exit_code,
    handle_run,
)


def _args(scan_path, output_dir, json_mode=False):
    args = MagicMock()
    args.scan_path = str(scan_path)
    args.output_dir = str(output_dir)
    args.type = "auto"
    args.format = "default"
    args.json_mode = json_mode
    args.verbose = False
    return args


def _result(status="COMPLETED", zt="zero_trust_verified"):
    return {"status": status, "zero_trust_status": zt}


# =========================================================================
# Одиночный документ: провал виден в коде возврата
# =========================================================================
def test_failed_extraction_returns_error_code(tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_text("x")
    with patch("scan_reader.cli.LegalDocPlatformFacade") as cls:
        cls.return_value.process_single_document.return_value = _result(
            status="FAILED", zt="vlm_unverified"
        )
        assert handle_run(_args(fake, tmp_path)) == EXIT_ERROR


def test_verified_extraction_returns_ok(tmp_path):
    fake = tmp_path / "doc.pdf"
    fake.write_text("x")
    with patch("scan_reader.cli.LegalDocPlatformFacade") as cls:
        cls.return_value.process_single_document.return_value = _result()
        assert handle_run(_args(fake, tmp_path)) == EXIT_OK


# =========================================================================
# Каталог: диспетчеризация в process_batch
# =========================================================================
def test_run_directory_dispatches_batch(tmp_path, capsys):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_text("x")
    with patch("scan_reader.cli.LegalDocPlatformFacade") as cls:
        inst = cls.return_value
        inst.process_batch.return_value = [_result(), _result()]
        code = handle_run(_args(inbox, tmp_path))
        assert code == EXIT_OK
        inst.process_batch.assert_called_once_with(str(inbox), organize_subfolders=False)
        out = capsys.readouterr().out
        assert "Обработано документов: 2 (сбоев: 0)" in out


def test_run_directory_json_emits_list(tmp_path, capsys):
    import json

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.pdf").write_text("x")
    with patch("scan_reader.cli.LegalDocPlatformFacade") as cls:
        cls.return_value.process_batch.return_value = [_result()]
        code = handle_run(_args(inbox, tmp_path, json_mode=True))
        assert code == EXIT_OK
        payload = json.loads(capsys.readouterr().out)
        assert isinstance(payload, list) and payload[0]["status"] == "COMPLETED"


# =========================================================================
# Агрегация кодов возврата пакета
# =========================================================================
def test_batch_any_failure_dominates():
    assert _batch_exit_code([_result(), _result(status="FAILED")]) == EXIT_ERROR
    # сбой важнее расхождения в завершённых документах
    assert _batch_exit_code(
        [_result(zt="discrepancy_detected"), _result(status="FAILED")]
    ) == EXIT_ERROR


def test_batch_discrepancy_and_gate_not_executed():
    assert _batch_exit_code([_result(zt="discrepancy_detected")]) == EXIT_DISCREPANCY
    assert _batch_exit_code([_result(zt="gate_not_executed")]) == EXIT_DISCREPANCY
    # расхождение важнее эвристики
    assert _batch_exit_code(
        [_result(zt="heuristic_fallback"), _result(zt="discrepancy_detected")]
    ) == EXIT_DISCREPANCY


def test_batch_fallback_and_ok():
    assert _batch_exit_code([_result(zt="heuristic_fallback")]) == EXIT_FALLBACK
    assert _batch_exit_code([_result(), _result()]) == EXIT_OK


def test_batch_empty_results_is_error():
    assert _batch_exit_code([]) == EXIT_ERROR


def test_result_exit_code_mapping():
    assert _result_exit_code({"status": "FAILED"}) == EXIT_ERROR
    assert _result_exit_code({"status": "COMPLETED", "zero_trust_status": "discrepancy_detected"}) == EXIT_DISCREPANCY
    assert _result_exit_code({"status": "COMPLETED", "zero_trust_status": "gate_not_executed"}) == EXIT_DISCREPANCY
    assert _result_exit_code({"status": "COMPLETED", "zero_trust_status": "heuristic_fallback"}) == EXIT_FALLBACK
    assert _result_exit_code({"status": "COMPLETED", "zero_trust_status": "zero_trust_verified"}) == EXIT_OK
