# -*- coding: utf-8 -*-
"""
Тесты плагинной архитектуры doc_types (type_registry.py).
"""

import pytest

from scan_reader.type_registry import get_registry, get_enabled_plugins, get_plugin


def test_registry_loads_all_plugins():
    registry = get_registry(force_reload=True)
    assert len(registry.plugins) >= 3
    assert len(registry.load_errors) == 0


def test_plugin_specs_contain_required_attributes():
    enabled = get_enabled_plugins()
    assert "enforcement_orders" in enabled
    assert "executive_documents" in enabled
    assert "salary_deductions" in enabled

    for pid, plugin in enabled.items():
        assert plugin.id == pid
        assert plugin.title
        assert plugin.schema_cls is not None
        assert plugin.prompt_text
        assert isinstance(plugin.flat_columns, list)
        assert len(plugin.flat_columns) > 0
        assert "fields" in plugin.benchmark_config
        assert "fields" in plugin.autonomous_config


def test_get_plugin_valid_and_invalid():
    p = get_plugin("executive_documents")
    assert p.id == "executive_documents"
    assert p.schema_cls.__name__ == "ExecutiveDocumentDoc"

    with pytest.raises(KeyError):
        get_plugin("non_existing_plugin")


def test_tie_priority_ordering():
    registry = get_registry()
    ordered = registry.enabled_by_tie_priority()
    # Плагины с большим tie_priority должны идти первыми
    priorities = [p.tie_priority for p in ordered]
    assert priorities == sorted(priorities, reverse=True)
