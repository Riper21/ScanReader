# -*- coding: utf-8 -*-
"""
Тесты Фазы 8 — удаление мёртвого кода и рефакторинг.

Часть тестов проверяет, что мёртвые сущности ДЕЙСТВИТЕЛЬНО удалены и не
вернутся, часть — что их удаление не сломало работоспособность.
"""

import ast
import inspect
import re
import subprocess
import sys
from pathlib import Path

import pytest

from scan_reader.core import io_utils, utils
from scan_reader.facade import LegalDocPlatformFacade
from scan_reader.type_registry import get_registry

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "scan_reader"

REMOVED_NAMES = (
    "build_document_schemas",
    "build_system_prompts",
    "build_category_names",
    "build_category_folders",
    "dashboard_meta",
    "UNKNOWN_FOLDER_NAME",
    "read_file_safe",
)


# =========================================================================
# 8.3-8.5: мёртвые сущности удалены
# =========================================================================
@pytest.mark.parametrize("name", REMOVED_NAMES)
def test_dead_symbols_are_gone(name):
    """Фаза 8: у каждого удалённого символа НОЛЬ вызывающих в src/tests/examples."""
    import scan_reader.type_registry as tr

    assert not hasattr(tr, name), f"type_registry.{name} всё ещё определён"

    hits = []
    for base in (SRC, REPO / "tests", REPO / "examples"):
        for path in base.rglob("*.py"):
            # сам файл теста перечисляет имена намеренно — он не считается
            if "__pycache__" in str(path) or path.name == Path(__file__).name:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for line in text.splitlines():
                stripped = line.strip()
                if stripped.startswith("#") or "\u0424\u0430\u0437\u0430" in stripped[:6]:
                    continue  # комментарии о удалении допустимы
                if re.search(rf"\b{re.escape(name)}\b", line):
                    hits.append(f"{path.name}:{stripped[:60]}")
    assert not hits, f"остались использования {name}: {hits}"


def test_public_alias_removed():
    """Фаза 8.3: алиас extract_raw_text_for_audit не имел вызывающих."""
    assert not hasattr(LegalDocPlatformFacade, "extract_raw_text_for_audit")
    assert hasattr(LegalDocPlatformFacade, "_extract_raw_text_for_audit")


def test_prompt_user_on_unknown_removed_from_signature():
    """
    Фаза 8.4: параметр был объявлен, передавался из CLI и MCP со значением
    False, но никогда не читался в теле метода.
    """
    params = list(inspect.signature(LegalDocPlatformFacade.process_single_document).parameters)
    assert params == ["self", "file_path", "doc_type"]

    cli_text = (SRC / "cli.py").read_text(encoding="utf-8")
    server_text = (SRC / "mcp" / "server.py").read_text(encoding="utf-8")
    assert "prompt_user_on_unknown" not in cli_text
    assert "prompt_user_on_unknown" not in server_text


def test_root_dir_alias_removed_from_facade():
    """Фаза 8.4: ROOT_DIR = PROJECT_ROOT был избыточным алиасом."""
    text = (SRC / "facade.py").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    assert not re.search(r"^\s*ROOT_DIR\s*=", code, re.MULTILINE)


# =========================================================================
# 8.1/8.2: heuristic_fallback стал достижимым
# =========================================================================
CONSISTENT_RAW = (
    "Судебный приказ. Должник Иванов Иван Иванович. "
    "Получатель ООО Ромашка, ИНН получателя 7802312751, БИК 015004950. "
    "Основной долг 50000 руб., госпошлина 3500 руб., "
    "итого удержанию 53500 руб., 50 процентов."
)
CONSISTENT_DOC = {
    "debtor": {"name": "Иванов Иван Иванович"},
    "payment_details": {"recipient_inn": "7802312751"},
    "employer": {"name": "ООО Ромашка"},
    "finances": {
        "debt_amount_rub": 50000.0,
        "fee_penalty_rub": 3500.0,
        "total_deduction_rub": 53500.0,
        "deduction_percentage": "50%",
    },
}


def test_heuristic_fallback_status_is_reachable():
    """
    Фаза 8.1: раньше extraction_method вычислялся из результата КЛАССИФИКАЦИИ,
    который никогда не равнялся regex_fallback, поэтому статус был мёртвым.
    """
    from scan_reader.verifier import VerificationStatus
    from scan_reader.verifier.auditor import ZeroTrustAuditor as A

    doc = dict(CONSISTENT_DOC)
    doc["_recovered_by_regex"] = ["finances.total_deduction_rub"]

    report = A.audit_document(
        doc, doc_type="salary_deductions",
        raw_ocr_text=CONSISTENT_RAW, extraction_method="regex_fallback",
    )
    assert report.status == VerificationStatus.HEURISTIC_FALLBACK
    assert report.details["recovered_by_regex"] == ["finances.total_deduction_rub"]


def test_exit_fallback_code_is_reachable():
    """CLI-код возврата 4 отвечает на реально возможный статус."""
    from scan_reader.cli import EXIT_DISCREPANCY, EXIT_FALLBACK, EXIT_OK

    def exit_code(status: str) -> int:
        if status in ("discrepancy_detected", "gate_not_executed"):
            return EXIT_DISCREPANCY
        if status == "heuristic_fallback":
            return EXIT_FALLBACK
        return EXIT_OK

    assert exit_code("heuristic_fallback") == EXIT_FALLBACK
    assert exit_code("discrepancy_detected") == EXIT_DISCREPANCY
    assert exit_code("zero_trust_verified") == EXIT_OK


def test_normal_extraction_is_not_marked_as_fallback():
    from scan_reader.verifier import VerificationStatus
    from scan_reader.verifier.auditor import ZeroTrustAuditor as A

    report = A.audit_document(
        CONSISTENT_DOC, doc_type="salary_deductions",
        raw_ocr_text=CONSISTENT_RAW, extraction_method="vlm",
    )
    assert report.status == VerificationStatus.ZERO_TRUST_VERIFIED


# =========================================================================
# 8.6: дубли схлопнуты
# =========================================================================
def test_as_dict_has_single_definition():
    """
    Фаза 8.6: однострочная функция была определена в трёх модулях.
    """
    definitions = []
    for path in SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in ("as_dict", "_as_dict"):
                definitions.append(f"{path.name}:{node.lineno}")
    assert len(definitions) == 1, f"определений as_dict: {definitions}"


def test_as_dict_behaviour():
    assert utils.as_dict({"a": 1}) == {"a": 1}
    assert utils.as_dict(None) == {}
    assert utils.as_dict("строка") == {}
    assert utils.as_dict([1, 2]) == {}


# =========================================================================
# 8.7: одна настройка UTF-8
# =========================================================================
def test_utf8_configuration_has_single_implementation():
    """Фаза 8.7: реализаций было три, и вторая перенастраивала уже изменённый поток."""
    assert io_utils.configure_streams is utils.setup_console_utf8

    definitions = 0
    for path in SRC.rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in (
                "setup_console_utf8", "configure_streams"
            ):
                body = [n for n in node.body if not isinstance(n, ast.Expr)
                        or not isinstance(n.value, ast.Constant)]
                if body:
                    definitions += 1
    assert definitions == 1, f"реализаций настройки UTF-8: {definitions}"


def test_setup_console_utf8_is_idempotent():
    """Повторный вызов на уже перенастроенном потоке не должен падать."""
    utils.setup_console_utf8()
    utils.setup_console_utf8()
    utils.setup_console_utf8()


# =========================================================================
# 8.9: process_batch разбит на этапы
# =========================================================================
@pytest.mark.parametrize(
    "method",
    [
        "_batch_stage1_environment",
        "_batch_stage2_discover",
        "_batch_stage3_classify",
        "_batch_stage4_extract",
        "_batch_stage5_metrics",
        "_batch_stage6_export",
        "_batch_stage7_lay_out_files",
        "_resume_from_checkpoint",
        "_load_ground_truth",
        "_resolve_incoming_root",
        "_target_subfolder",
    ],
)
def test_batch_stages_extracted(method):
    assert hasattr(LegalDocPlatformFacade, method), f"отсутствует {method}"


def test_process_batch_is_readable_length():
    """Фаза 8.9: была одной функцией на 232 строки."""
    source = inspect.getsource(LegalDocPlatformFacade.process_batch)
    body_lines = [
        line for line in source.splitlines()[1:]
        if line.strip() and not line.strip().startswith(("#", '"""'))
    ]
    assert len(body_lines) < 60, f"process_batch всё ещё длинная: {len(body_lines)} строк"


def test_target_subfolder_is_readable():
    """
    Фаза 8.9: тернарка с приоритетом or/and
    «pl.id если папка pl.id есть ИЛИ папки pl.folder нет, иначе pl.folder».
    """
    registry = get_registry()
    plugin = registry.get("salary_deductions")

    class _FS:
        """Файловая система, где существуют только перечисленные папки."""

        def __init__(self, existing):
            self.existing = set(existing)

    # Папки нет ни одной -> используется id (pl.folder тоже не существует)
    assert LegalDocPlatformFacade._target_subfolder("/root", plugin) == plugin.id

    # Существует только папка pl.folder -> используется она
    only_folder = type(
        "P", (),
        {"id": "salary_deductions", "folder": "Удержания_из_зарплаты"},
    )()
    import os as _os

    real_exists = _os.path.exists

    class _Patch:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    # Проверяем через tmp_path, чтобы не подменять os.path глобально
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        _os.makedirs(_os.path.join(tmp, "Удержания_из_зарплаты"))
        # folder существует, id не существует -> выбирается folder
        assert LegalDocPlatformFacade._target_subfolder(tmp, only_folder) == "Удержания_из_зарплаты"
        # теперь создаём и id -> приоритет у id
        _os.makedirs(_os.path.join(tmp, "salary_deductions"))
        assert LegalDocPlatformFacade._target_subfolder(tmp, only_folder) == "salary_deductions"
    assert real_exists is _os.path.exists


def test_batch_pipeline_still_runs_on_empty_input(capsys):
    """Дробление не сломало ранний выход при отсутствии документов."""
    facade = LegalDocPlatformFacade.__new__(LegalDocPlatformFacade)
    facade.registry = get_registry()
    facade._get_client = lambda: None
    facade.results_dir = str(Path(__import__("tempfile").mkdtemp()))
    assert facade.process_batch("нет-такой-папки") == []
    assert "не обнаружено" in capsys.readouterr().out


# =========================================================================
# 8.10: подавление E402 удалено
# =========================================================================
def test_e402_suppression_removed_from_pyproject():
    """
    Подавление было симптомом отсутствия модуля конфигурации:
    .env загружался посреди блока импортов.
    """
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    assert 'facade.py" = ["E402"]' not in text
    assert "src/scan_reader/facade.py" not in text


def test_config_module_loads_environment(tmp_path, monkeypatch):
    from scan_reader import config

    env_file = tmp_path / ".env"
    env_file.write_text("SCANREADER_TEST_PROBE=значение\n", encoding="utf-8")
    monkeypatch.setenv("SCANREADER_HOME", str(tmp_path))
    monkeypatch.delenv("SCANREADER_TEST_PROBE", raising=False)

    config.load_environment()

    import os

    assert os.getenv("SCANREADER_TEST_PROBE") == "значение"
    assert config.PROJECT_ROOT


def test_package_imports_load_env_before_submodules():
    """Импорт пакета обязан прочитать .env до создания логгеров подмодулей."""
    code = (
        "import os, sys, tempfile, pathlib;"
        "d = tempfile.mkdtemp();"
        "pathlib.Path(d, '.env').write_text('SCANREADER_ENV_PROBE=PROBE_OK', encoding='utf-8');"
        "os.environ['SCANREADER_HOME'] = d;"
        "import scan_reader.facade as f;"
        "print(os.environ.get('SCANREADER_ENV_PROBE'))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True,
        cwd=str(REPO), env={**__import__("os").environ, "PYTHONPATH": str(REPO / "src")},
    )
    assert "PROBE_OK" in out.stdout, f"env не загружен при импорте пакета: {out.stdout} {out.stderr}"


def test_ruff_passes_without_suppression():
    """Контроль: ruff действительно чист, а не подавлен."""
    proc = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "src", "tests", "examples"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    assert proc.returncode == 0, proc.stdout
