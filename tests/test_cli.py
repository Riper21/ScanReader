# -*- coding: utf-8 -*-
"""
Тесты консольной утилиты scan_reader.cli для тихой интеграции с 1С.
"""

import os
import sys
import json
import pytest
from unittest.mock import patch

from scan_reader import cli


def test_cli_help(capsys):
    """Проверка работы флага --help."""
    test_args = ["cli.py", "--help"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code == 0
    captured = capsys.readouterr()
    assert "ScanReader CLI" in captured.out


def test_cli_file_not_found(capsys):
    """Проверка реакции на несуществующий файл."""
    test_args = ["cli.py", "non_existent_file_xyz.pdf"]
    with patch.object(sys, "argv", test_args):
        with pytest.raises(SystemExit) as exc_info:
            cli.main()
        assert exc_info.value.code == 1
    captured = capsys.readouterr()
    assert "[ОШИБКА] Входной файл не найден" in captured.err
    assert captured.out == ""


def test_cli_mocked_success(tmp_path, capsys):
    """Проверка корректного вывода пути к JSON в stdout при успешной обработке.

    0.9.3: вывод всегда указывает на канонические файлы карточки —
    {stem}_Flat.json (форматы 1c/flat/default/stdout) или {stem}_Full.json
    (форматы full/raw). Папка 1C_Импорт и карточка типа больше не пишутся.
    """
    # Создаем временный входной файл
    dummy_scan = tmp_path / "test_order.pdf"
    dummy_scan.write_text("dummy content", encoding="utf-8")

    # Создаем фиктивные выходные файлы
    out_dir = tmp_path / "Результаты"
    out_dir.mkdir(parents=True, exist_ok=True)

    full_json = out_dir / "test_order_Full.json"
    full_json.write_text(json.dumps({"status": "COMPLETED"}), encoding="utf-8")

    flat_json = out_dir / "test_order_Flat.json"
    flat_json.write_text(json.dumps({"Bik": "041203001", "ResolutionDate": "2026-08-28"}), encoding="utf-8")

    fake_result = {
        "file_name": "test_order.pdf",
        "doc_type": "salary_deductions",
        "status": "COMPLETED",
        "data": {"Bik": "041203001"}
    }

    with patch("scan_reader.facade.LegalDocPlatformFacade.process_single_document", return_value=fake_result):
        # 1. Формат 1c (по умолчанию): путь к плоской записи для 1С
        test_args = ["cli.py", str(dummy_scan), "-o", str(out_dir)]
        with patch.object(sys, "argv", test_args):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0

        captured = capsys.readouterr()
        returned_path = captured.out.strip()
        assert os.path.exists(returned_path)
        assert returned_path.endswith("test_order_Flat.json")
        assert captured.err == ""

        # 2. Формат stdout: тело плоской записи
        test_args_stdout = ["cli.py", str(dummy_scan), "-o", str(out_dir), "-f", "stdout"]
        with patch.object(sys, "argv", test_args_stdout):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0

        captured_stdout = capsys.readouterr()
        json_obj = json.loads(captured_stdout.out.strip())
        assert json_obj["Bik"] == "041203001"
        assert json_obj["ResolutionDate"] == "2026-08-28"

        # 3. Формат default: путь к плоской записи
        test_args_def = ["cli.py", str(dummy_scan), "-o", str(out_dir), "-f", "default"]
        with patch.object(sys, "argv", test_args_def):
            with pytest.raises(SystemExit) as exc_info:
                cli.main()
            assert exc_info.value.code == 0

        captured_def = capsys.readouterr()
        assert captured_def.out.strip().endswith("test_order_Flat.json")

        # 4. Форматы full и raw: оба указывают на {stem}_Full.json
        for fmt in ("full", "raw"):
            test_args_fmt = ["cli.py", str(dummy_scan), "-o", str(out_dir), "-f", fmt]
            with patch.object(sys, "argv", test_args_fmt):
                with pytest.raises(SystemExit) as exc_info:
                    cli.main()
                assert exc_info.value.code == 0
            captured_fmt = capsys.readouterr()
            assert captured_fmt.out.strip().endswith("test_order_Full.json")
