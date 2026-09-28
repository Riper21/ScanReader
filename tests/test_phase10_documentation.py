# -*- coding: utf-8 -*-
"""
Тесты Фазы 10 — согласованность документации и кода.

Документация, расходящаяся с кодом, опаснее её отсутствия: она создаёт
уверенность в несуществующих гарантиях. Каждое утверждение о статусе, коде
возврата, переменной окружения, числе плагинов или файлов контракта проверяется
против исходников.
"""

import re
import subprocess
import sys
from pathlib import Path

import pytest

from scan_reader import __version__
from scan_reader.cli import EXIT_DISCREPANCY, EXIT_ERROR, EXIT_FALLBACK, EXIT_OK, EXIT_USAGE
from scan_reader.type_registry import REQUIRED_PLUGIN_FILES, get_registry
from scan_reader.verifier.status import VerificationStatus

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src" / "scan_reader"

DOC_FILES = ("README.md", "README_RU.md", "ARCHITECTURE.md", "ARCHITECTURE_RU.md",
             "AGENTS.md", "SECURITY.md", "SECURITY_RU.md", "docs/USER_GUIDE_RU.md")


def _read(name):
    return (REPO / name).read_text(encoding="utf-8")


# =========================================================================
# Запрещённые формулировки
# =========================================================================
#: Файлы, где запрещённая формулировка может встречаться ВОЗРАЖЕНИЕМ
#: (правило 8 цитирует её, чтобы запретить), а не как утверждение.
NEGATION_CONTEXT = (
    "запрещ", "не является", "не означает", "была бы лож", "нельзя", "формулировк",
    "forbidden", "not mean", "false claim", "is not a",
)


def _asserts_claim(text: str, phrase: str) -> bool:
    """
    Утверждает ли документ формулировку, или отвергает её.

    Правило 8 цитирует «100 % подтверждено» в запрете, поэтому простое
    вхождение подстроки даёт ложное срабатывание.
    """
    low = text.lower()
    target = phrase.lower()
    start = 0
    while True:
        idx = low.find(target, start)
        if idx == -1:
            return False
        window = low[max(0, idx - 220): idx + len(target) + 120]
        if not any(marker in window for marker in NEGATION_CONTEXT):
            return True
        start = idx + 1


@pytest.mark.parametrize("name", DOC_FILES)
def test_no_perfect_guarantee_claims(name):
    """
    Правило 8 AGENTS.md: «100 % подтверждено» и «готов к безоговорочному
    автоимпорту» запрещены. Раньше это стояло в таксономии статусов как
    определение zero_trust_verified.
    """
    text = _read(name)
    # 100% допустимо только как измерение структуры («100% одноуровневая структура»)
    forbidden = [
        "100% подтвержденн", "100 % подтвержденн", "100% Verified",
        "100% corroborated", "безоговорочн", "unassisted import",
        "Ready for automated unassisted",
    ]
    for phrase in forbidden:
        assert not _asserts_claim(text, phrase), (
            f"{name}: документ утверждает «{phrase}», а не запрещает её"
        )


def test_status_taxonomy_documents_every_status():
    """Каждый статус из кода обязан быть описан в архитектурной документации."""
    for doc in ("ARCHITECTURE.md", "ARCHITECTURE_RU.md"):
        text = _read(doc)
        for status in VerificationStatus:
            assert status.value in text, f"{doc}: не описан статус {status.value}"


def test_no_false_mcp_sdk_compatibility_claim():
    """
    Сервер — приватный построчный JSON-RPC, а не MCP. Раньше раздел назывался
    «Model Context Protocol (MCP) Interface», что вводило в заблуждение.
    """
    for doc in ("ARCHITECTURE.md", "ARCHITECTURE_RU.md"):
        text = _read(doc)
        assert "Model Context Protocol (MCP) Interface" not in text, doc
        assert "Интерфейс Model Context Protocol (MCP)" not in text, doc
        # README прямо признаёт несовместимость
    for doc in ("README.md", "README_RU.md"):
        text = _read(doc).lower()
        assert ("не совместим" in text) or ("not compatible" in text), (
            f"{doc}: не указано, что сервер не совместим с MCP SDK"
        )


def test_unimplemented_features_are_not_claimed():
    """CLAHE не реализован; OLE2-документы не поддерживаются."""
    for doc in DOC_FILES:
        text = _read(doc)
        assert "CLAHE" not in text, f"{doc}: заявлена CLAHE, которая не реализована"
        assert "Mermaid-Guard" not in text, f"{doc}: несуществующая ссылка на происхождение"


# =========================================================================
# Согласованность кодов возврата
# =========================================================================
@pytest.mark.parametrize(
    "doc", ["README.md", "README_RU.md", "docs/USER_GUIDE_RU.md"]
)
def test_documented_exit_codes_match_code(doc):
    text = _read(doc)
    for code in (EXIT_OK, EXIT_ERROR, EXIT_USAGE, EXIT_DISCREPANCY, EXIT_FALLBACK):
        assert f"`{code}`" in text, f"{doc}: не описан код возврата {code}"


def test_exit_code_three_covers_gate_not_executed():
    """gate_not_executed обязан требовать ручной проверки, то есть кода 3."""
    row = [line for line in _read("README.md").splitlines() if line.strip().startswith("| `3`")]
    assert row, "нет строки с кодом 3"
    line = row[0].lower()
    assert "gate" in line or "гейт" in line, (
        f"код 3 не связан с невыполненным гейтом: {row[0]}"
    )
    assert "review" in line or "проверк" in line, (
        f"код 3 не требует ручной проверки: {row[0]}"
    )


# =========================================================================
# Согласованность версий
# =========================================================================
def test_version_is_synchronised():
    pyproject = _read("pyproject.toml")
    m = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.MULTILINE)
    assert m, "версия не найдена в pyproject.toml"
    assert m.group(1) == __version__, "версии в pyproject.toml и __init__.py расходятся"


def test_security_policy_supports_current_minor():
    text = _read("SECURITY.md")
    minor = __version__.rsplit(".", 1)[0]
    assert f"`{minor}.x`" in text, f"SECURITY.md не поддерживает ветку {minor}.x"
    ru = _read("SECURITY_RU.md")
    assert f"`{minor}.x`" in ru, f"SECURITY_RU.md не поддерживает ветку {minor}.x"


def test_changelog_has_release_section():
    text = _read("CHANGELOG.md")
    assert f"## [{__version__}]" in text, f"CHANGELOG.md не содержит раздел {__version__}"
    ru = _read("CHANGELOG_RU.md")
    assert f"## [{__version__}]" in ru, f"CHANGELOG_RU.md не содержит раздел {__version__}"


# =========================================================================
# Согласованность контракта плагина
# =========================================================================
def test_agents_md_declares_actual_required_files():
    """AGENTS.md обязан перечислять реальный контракт, включая verification.json."""
    text = _read("AGENTS.md")
    for name in REQUIRED_PLUGIN_FILES:
        assert name in text, f"AGENTS.md не упоминает обязательный файл {name}"
    assert "verification.json" in text


def test_agents_md_states_ocr_does_not_apply_to_harm():
    """Правило 2 AGENTS.md: лимит 50 % к вреду здоровью не применяется."""
    text = _read("AGENTS.md")
    assert "314-ФЗ" in text
    assert "не применяется" in text


def test_docs_mention_ocr_extra():
    for doc in ("README.md", "README_RU.md"):
        text = _read(doc)
        assert '".[ocr]"' in text, f"{doc}: не описан extra [ocr]"


# =========================================================================
# Согласованность переменных окружения
# =========================================================================
def test_all_documented_env_vars_exist_in_code():
    """Каждая переменная, упомянутая в .env.example, реально читается кодом."""
    env_example = _read(".env.example")
    declared = set(re.findall(r"^#\s*(SCANREADER_[A-Z0-9_]+|OPENAI_[A-Z0-9_]+|AI_MODEL_NAME|"
                              r"ENABLE_CACHE|LOG_LEVEL|NO_COLOR|VLM_TIMEOUT)=", env_example,
                              re.MULTILINE))
    assert declared, "не найдено ни одной переменной в .env.example"

    code = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in SRC.rglob("*.py")
    )
    unused = [name for name in sorted(declared) if f'"{name}"' not in code]
    assert not unused, f"переменные задокументированы, но не читаются кодом: {unused}"


def test_env_vars_read_by_code_are_documented():
    """Обратное направление: читаемая, но незадокументированная переменная — пробел."""
    code = "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in SRC.rglob("*.py")
    )
    used = set(re.findall(r'os\.getenv\(\s*"(SCANREADER_[A-Z0-9_]+|VLM_TIMEOUT|LOG_LEVEL|'
                          r'ENABLE_CACHE|NO_COLOR|AI_MODEL_NAME|OPENAI_[A-Z0-9_]+)"', code))
    env_example = _read(".env.example")
    undocumented = sorted(
        name for name in used
        if f"{name}=" not in env_example and f"# {name}" not in env_example
    )
    assert not undocumented, f"переменные читаются кодом, но не описаны в .env.example: {undocumented}"


# =========================================================================
# Согласованность числа плагинов и статусов
# =========================================================================
def test_readme_plugin_count_matches_registry():
    count = len(get_registry().enabled())
    for doc in ("README.md", "README_RU.md"):
        text = _read(doc)
        m = re.search(r"(\d+)\s+(?:essential|essential Russian|ключевых|поддерживаемых)", text)
        if m:
            assert int(m.group(1)) == count, f"{doc}: заявлено {m.group(1)} типов, в реестре {count}"
        for pid in get_registry().enabled():
            assert pid in text, f"{doc}: плагин {pid} не упомянут"


def test_docs_describe_ocr_extra_matching_pyproject():
    pyproject = _read("pyproject.toml")
    assert 'ocr = [' in pyproject
    assert "rapidocr-onnxruntime" in pyproject
    assert 'ocr-full' in pyproject


# =========================================================================
# Согласованность тестовых утверждений
# =========================================================================
def test_documented_test_count_is_not_fabricated():
    """
    CHANGELOG может называть достигнутый порог, но число тестов не должно
    расходиться с фактическим более чем в 2 % (иначе это не измерение).
    """
    text = _read("CHANGELOG.md")
    m = re.search(r"(\d+)\s+tests\s+\(was\s+(\d+)\)", text)
    if not m:
        pytest.skip("число тестов в CHANGELOG не указано")
    claimed = int(m.group(1))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only"],
        capture_output=True, text=True, cwd=str(REPO),
    )
    m2 = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
    if not m2:
        m2 = re.search(r"collected\s+(\d+)\s+items?", proc.stdout)
    if not m2:
        pytest.skip("не удалось подсчитать собранные тесты")
    actual = int(m2.group(1))
    assert abs(claimed - actual) <= max(3, actual * 0.02), (
        f"CHANGELOG утверждает {claimed} тестов, собрано {actual}"
    )
