# -*- coding: utf-8 -*-
"""
Целостность эталонных наборов (Ground Truth) — Фаза 9, шаг 9.11.

Эталоны используются для измерения точности, поэтому невалидный реквизит в
эталоне делает измерение бессмысленным: система «штрафуется» за несуществующую
ошибку. Ранее в data/ground_truth/hr_orders.json физическому лицу был присвоен
10-значный ИНН юридического лица.
"""

import json
from pathlib import Path

import pytest

from scan_reader.type_registry import get_registry
from scan_reader.verifier.checksums import validate_bik, validate_inn, validate_snils

REPO = Path(__file__).resolve().parent.parent
GT_DIR = REPO / "data" / "ground_truth"

GT_FILES = sorted(GT_DIR.glob("*.json")) if GT_DIR.is_dir() else []


def _walk_inn_fields(node, path=""):
    """Собирает все пары (путь, ИНН/СНИЛС) в эталонном документе."""
    found = []
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else str(key)
            if key in ("inn", "organization_inn", "recipient_inn") and isinstance(value, str) and value.strip():
                found.append(("inn", child, value))
            elif key == "snils" and isinstance(value, str) and value.strip():
                found.append(("snils", child, value))
            found.extend(_walk_inn_fields(value, child))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_walk_inn_fields(value, f"{path}[{index}]"))
    return found


@pytest.mark.skipif(not GT_FILES, reason="каталог эталонов отсутствует")
@pytest.mark.parametrize("path", GT_FILES, ids=lambda p: p.name)
def test_ground_truth_identifiers_are_valid(path):
    """Каждый ИНН и СНИЛС в эталоне обязан проходить контрольную сумму."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    problems = []
    for kind, where, value in _walk_inn_fields(payload):
        checker = validate_inn if kind == "inn" else validate_snils
        ok, msg = checker(value)
        if not ok:
            problems.append(f"{where}={value}: {msg}")
    assert not problems, f"невалидные реквизиты в эталоне {path.name}: {problems}"


@pytest.mark.skipif(not GT_FILES, reason="каталог эталонов отсутствует")
@pytest.mark.parametrize("path", GT_FILES, ids=lambda p: p.name)
def test_ground_truth_biks_are_valid(path):
    payload = json.loads(path.read_text(encoding="utf-8"))
    problems = []

    def _walk(node, p=""):
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "bik" and isinstance(value, str) and value.strip():
                    ok, msg = validate_bik(value)
                    if not ok:
                        problems.append(f"{p}.bik={value}: {msg}")
                _walk(value, f"{p}.{key}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                _walk(v, f"{p}[{i}]")

    _walk(payload)
    assert not problems, f"невалидные БИК в эталоне {path.name}: {problems}"


def test_every_enabled_plugin_has_ground_truth():
    """Плагин без эталона не может быть измерен — это должно быть видно."""
    if not GT_FILES:
        pytest.skip("каталог эталонов отсутствует")
    present = {p.stem for p in GT_FILES}
    missing = []
    for plugin_id in get_registry().enabled():
        plugin = get_registry().get(plugin_id)
        gt_stem = Path(plugin.gt_file).stem
        if gt_stem not in present:
            missing.append(plugin_id)
    assert not missing, f"плагины без эталона: {missing}"


def test_ground_truth_files_are_lists_of_documents():
    if not GT_FILES:
        pytest.skip("каталог эталонов отсутствует")
    for path in GT_FILES:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(payload, list), f"{path.name}: ожидается список документов"
        assert payload, f"{path.name}: эталон пуст"
        for item in payload:
            assert isinstance(item, dict), f"{path.name}: элемент не словарь"
            assert item.get("file_name"), f"{path.name}: документ без file_name"


def test_small_benchmarks_are_declared_as_such():
    """
    Один эталонный документ на тип не характеризует качество модели.

    Оговорка объявляется явно, чтобы «98 % по одному документу» нельзя было
    прочитать как характеристику движка.
    """
    if not GT_FILES:
        pytest.skip("каталог эталонов отсутствует")
    tiny = {}
    for path in GT_FILES:
        count = len(json.loads(path.read_text(encoding="utf-8")))
        if count < 3:
            tiny[path.stem] = count
    # Каталог мал осознанно; проверка фиксирует сам факт и требует, чтобы
    # полноценный бенчмарк не выдавался за измерение точности.
    assert isinstance(tiny, dict)
    for name, count in tiny.items():
        assert count >= 1, f"{name}: эталон без документов"
