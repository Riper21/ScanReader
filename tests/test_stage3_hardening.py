# -*- coding: utf-8 -*-
"""
Tests for Stage 3/4 hardening:
- B-01: package resources completeness (importlib.resources — работает и в installed-режиме).
- H-04: MAX_INPUT_BYTES applied to input files.
- H-06: global flags before subcommand.
- H-07/H-08: glob-semantics path_patterns and ispol misclassification.
- H-09: unknown autonomous rule fails instead of silently passing.
- M-02/M-03: real date/sign checks in validate_document.
- M-14: RTF markup stripping.
- M-16: UTF-16 BOM detection.
- M-18: Individual recipient type.
- C-10: exit codes documentation contract.
"""

import os
import pytest
from pathlib import Path
from unittest.mock import patch

from scan_reader.cli import main, build_parser, EXIT_ERROR


# ---------------------------------------------------------------------------
# B-01: package data completeness
# ---------------------------------------------------------------------------
def test_b01_package_resources_complete():
    """B-01: каждый плагин предоставляет все 7 обязательных файлов как package data."""
    import importlib.resources as ir
    from scan_reader.type_registry import get_registry, REQUIRED_PLUGIN_FILES

    reg = get_registry()
    assert len(reg.plugins) == 9

    base = ir.files("scan_reader") / "doc_types"
    for pid in reg.enabled():
        folder = base / pid
        present = {p.name for p in folder.iterdir()}
        missing = [f for f in REQUIRED_PLUGIN_FILES if f not in present]
        assert not missing, f"Плагин {pid}: отсутствуют package-data файлы {missing}"


# ---------------------------------------------------------------------------
# H-04: input size limits
# ---------------------------------------------------------------------------
def test_h04_input_file_size_limit(tmp_path):
    """H-04: файлы больше MAX_INPUT_BYTES отклоняются до передачи в VLM."""
    from scan_reader.file_processor import FileProcessor, check_input_file_size
    from scan_reader.core.io_utils import InputFileError

    big_file = tmp_path / "big.pdf"
    big_file.write_bytes(b"0" * 64)

    processor = FileProcessor()

    with patch("scan_reader.file_processor.MAX_INPUT_BYTES", 32):
        with pytest.raises(InputFileError):
            check_input_file_size(str(big_file))
        with pytest.raises(InputFileError):
            processor.prepare_document_inputs(str(big_file))
        with pytest.raises(InputFileError):
            processor.prepare_dual_zone_inputs(str(big_file))


def test_h04_document_loader_size_limit(tmp_path):
    """M-17/H-04: load_document отклоняет файлы свыше лимита."""
    from scan_reader.core.document_loader import load_document
    from scan_reader.core.io_utils import InputFileError

    big_file = tmp_path / "big.txt"
    big_file.write_bytes(b"a" * 64)

    with patch("scan_reader.core.document_loader.MAX_INPUT_BYTES", 32):
        with pytest.raises(InputFileError):
            load_document(str(big_file))


# ---------------------------------------------------------------------------
# H-06: global flag before subcommand
# ---------------------------------------------------------------------------
def test_h06_global_verbose_flag_before_subcommand():
    """H-06: `scan-reader -v run file.pdf` разбирается без ошибки инжекции 'run'."""
    args = build_parser().parse_args(["-v", "run", "doc.pdf"])
    assert args.command == "run"
    assert args.verbose is True
    assert args.scan_path == "doc.pdf"


def test_h06_file_only_invocation_injects_run():
    """Обратная совместимость: `scan-reader f.pdf` эквивалентно `scan-reader run f.pdf`."""
    from scan_reader import cli as cli_mod

    argv = ["f.pdf"]
    # Симулируем логику инжекции через main с заглушкой обработчика
    with patch.object(cli_mod, "handle_run", return_value=0) as mock_run:
        with pytest.raises(SystemExit) as exc_info:
            main(argv)
        assert exc_info.value.code == 0
        called_argv = mock_run.call_args[0][0]
        assert called_argv.scan_path == "f.pdf"


# ---------------------------------------------------------------------------
# H-07/H-08: path pattern glob semantics
# ---------------------------------------------------------------------------
def test_h07_glob_multi_part_pattern_matches():
    """H-07: '*приказ*прием*' находит части в порядке следования."""
    from scan_reader.facade import _glob_parts_match

    assert _glob_parts_match("*приказ*прием*", "входящие/приказ_о_приеме_сотрудника.pdf")
    assert not _glob_parts_match("*приказ*прием*", "входящие/прием_приказ.pdf")  # порядок важен
    assert not _glob_parts_match("*приказ*прием*", "договор_аренды.pdf")


def test_h08_ispol_filename_routes_to_enforcement_orders():
    """H-08: имя файла о возбуждении ИП не должно классифицироваться как исполнительный лист."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)
    filename = "obrazecz_zayavleniya_o_vozbuzhdenii_ispolnitelnogo_proizvodstva_dolzhnik_yurliczo.png"
    doc_type, conf, method = facade.classify_document(filename, use_vlm_fallback=False)
    assert doc_type != "executive_documents"
    assert doc_type == "enforcement_orders"


def test_h08_ispolnitelny_list_still_matches_executive():
    """H-08: настоящий исполнительный лист по-прежнему распознается."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)
    doc_type, conf, method = facade.classify_document("ispolnitelny_list_fs09665763.pdf", use_vlm_fallback=False)
    assert doc_type == "executive_documents"


# ---------------------------------------------------------------------------
# H-09 / M-02 / M-03: autonomous rules
# ---------------------------------------------------------------------------
def test_h09_unknown_rule_fails():
    """H-09: неизвестное правило помечается как непройденное, а не молча проходит."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)
    plugin_id = "enforcement_orders"
    rules = facade.registry.get(plugin_id).autonomous_config["fields"]

    fake_data = {"doc_number": "123", "doc_date": "01.01.2024"}
    with patch.dict(facade.registry.get(plugin_id).autonomous_config, {"fields": rules + [
        {"field": "doc_number", "rule": "nonexistent_rule_xyz", "severity": "CRITICAL", "message": "X"}
    ]}):
        res = facade.validate_document(fake_data, plugin_id)
    assert res["passed"] is False
    assert any("nonexistent_rule_xyz" in i.get("message", "") for i in res["issues"])


def test_m02_invalid_date_fails_valid_date_format():
    """M-02: 'abcdefgh' и '2024-13-45' не проходят проверку даты."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)
    plugin_id = "hr_orders"

    with patch.dict(facade.registry.get(plugin_id).autonomous_config, {"fields": [
        {"field": "doc_date", "rule": "valid_date_format", "severity": "CRITICAL", "message": "дата"}
    ]}):
        res_bad = facade.validate_document({"doc_date": "abcdefgh"}, plugin_id)
        res_bad2 = facade.validate_document({"doc_date": "2024-13-45"}, plugin_id)
        res_ok = facade.validate_document({"doc_date": "15.03.2024"}, plugin_id)

    assert res_bad["passed"] is False
    assert res_bad2["passed"] is False
    assert res_ok["passed"] is True


def test_m03_negative_percentage_fails():
    """M-03: отрицательное значение не проходит positive_number_or_percentage."""
    from scan_reader.facade import LegalDocPlatformFacade

    facade = LegalDocPlatformFacade(enable_cache=False)
    plugin_id = "hr_orders"
    with patch.dict(facade.registry.get(plugin_id).autonomous_config, {"fields": [
        {"field": "x", "rule": "positive_number_or_percentage", "severity": "CRITICAL", "message": "x"}
    ]}):
        res_neg = facade.validate_document({"x": "-50%"}, plugin_id)
        res_zero = facade.validate_document({"x": "0%"}, plugin_id)
        res_ok = facade.validate_document({"x": "50%"}, plugin_id)

    assert res_neg["passed"] is False
    assert res_zero["passed"] is False
    assert res_ok["passed"] is True


# ---------------------------------------------------------------------------
# M-14 / M-16: document loader encodings and RTF
# ---------------------------------------------------------------------------
def test_m14_rtf_markup_stripped(tmp_path):
    """M-14: из RTF извлекается текст, а не сырая разметка."""
    from scan_reader.core.document_loader import load_document

    rtf = r"{\rtf1\ansi\deff0{\fonttbl{\f0 Times;}}\pard\fs24 Договор \b поставки\b0  №12\par}"
    rtf_file = tmp_path / "doc.rtf"
    rtf_file.write_text(rtf, encoding="utf-8")

    title, text = load_document(str(rtf_file))
    assert "{\\rtf" not in text
    assert "Договор" in text
    assert "поставки" in text
    assert "№12" in text


def test_m16_utf16_with_bom_detected(tmp_path):
    """M-16: UTF-16 LE с BOM читается корректно, без mojibake."""
    from scan_reader.core.document_loader import load_document

    content = "Исполнительный лист № ФС00001234 от 20.01.2016"
    f16 = tmp_path / "utf16doc.txt"
    f16.write_bytes(content.encode("utf-16"))

    title, text = load_document(str(f16))
    assert content in text


def test_m16_utf16_without_bom_detected(tmp_path):
    """M-16: UTF-16 без BOM детектируется по нулевым байтам."""
    from scan_reader.core.document_loader import load_document

    content = "Заявление о возбуждении исполнительного производства"
    f16 = tmp_path / "utf16_nobom.txt"
    f16.write_bytes(content.encode("utf-16-le"))

    title, text = load_document(str(f16))
    assert content in text


def test_m15_ole2_doc_returns_empty_not_mojibake(tmp_path):
    """M-15: бинарный OLE2 .doc не отдает mojibake в VLM."""
    from scan_reader.core.document_loader import load_document

    ole2 = tmp_path / "legacy.doc"
    payload = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + os.urandom(256)
    ole2.write_bytes(payload)

    title, text = load_document(str(ole2))
    assert text == ""


# ---------------------------------------------------------------------------
# M-18: recipient type
# ---------------------------------------------------------------------------
def test_m18_recipient_type_individual():
    """M-18: физлицо-получатель маркируется как Individual."""
    from scan_reader.core.json_exporter import determine_recipient_type

    assert determine_recipient_type("Иванов И.И.", "40817810000000000000") == "Individual"
    assert determine_recipient_type("УФК по Москве", "03212643000000000000") == "Budget"
    assert determine_recipient_type("ООО Ромашка", "40702810000000000000") == "Company"


# ---------------------------------------------------------------------------
# C-10: CLI exit code contract
# ---------------------------------------------------------------------------
def test_c10_missing_file_returns_exit_error(capsys):
    """C-10: отсутствующий входной файл возвращает код 1 (EXIT_ERROR), не 2."""
    with pytest.raises(SystemExit) as exc_info:
        main(["run", "nonexistent_file_xyz.pdf"])
    assert exc_info.value.code == EXIT_ERROR


# ---------------------------------------------------------------------------
# C-12: filename collision keeps uniqueness after sanitization
# ---------------------------------------------------------------------------
def test_c12_collision_guard_uses_suffixes(tmp_path):
    """C-12: коллизия имен разрешается суффиксами __2/__3 без перезаписи."""
    from scan_reader.core.json_exporter import save_single_document_json

    doc1 = {"file_name": "акт.pdf", "file_path": "/2024/akt.pdf", "doc_type": "acceptance_certificates",
            "data": {"doc_number": "1"}}
    doc2 = {"file_name": "акт.pdf", "file_path": "/2025/akt.pdf", "doc_type": "acceptance_certificates",
            "data": {"doc_number": "2"}}

    p1 = save_single_document_json(doc1, str(tmp_path))
    p2 = save_single_document_json(doc2, str(tmp_path))
    assert p1 != p2
    assert Path(p1).exists() and Path(p2).exists()


# ---------------------------------------------------------------------------
# M-06: OCR_LOW_CONFIDENCE status
# ---------------------------------------------------------------------------
def test_m06_low_dpi_sets_ocr_low_confidence_status():
    """M-06: скан ниже 150 DPI получает статус ocr_low_confidence с предупреждением."""
    from scan_reader.verifier.auditor import ZeroTrustAuditor
    from scan_reader.verifier.status import VerificationStatus

    report = ZeroTrustAuditor.audit_document(
        data={"debtor": {"name": "Иванов И.И."}},
        doc_type="enforcement_orders",
        scan_dpi=96.0,
    )
    assert report.status == VerificationStatus.OCR_LOW_CONFIDENCE
    assert any(i.code == "OCR_LOW_CONFIDENCE" for i in report.issues)
    assert report.details.get("scan_dpi") == 96

    # Нормальный DPI не меняет статус
    ok_report = ZeroTrustAuditor.audit_document(
        data={"debtor": {"name": "Иванов И.И."}},
        doc_type="enforcement_orders",
        scan_dpi=300.0,
    )
    assert ok_report.status != VerificationStatus.OCR_LOW_CONFIDENCE
