# -*- coding: utf-8 -*-
"""
0.9.2: смоук-тест измерительного стенда scripts/corpus.

Стенд живёт вне пакета, поэтому pytest его не импортирует. Здесь проверяется,
что он импортируется, детерминирован и даёт согласованные структуры данных —
иначе заявленные в docs/measurements цифры нельзя воспроизвести.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CORPUS_DIR = REPO / "scripts" / "corpus"


def _harness():
    sys.path.insert(0, str(CORPUS_DIR))
    try:
        import measure_risks
        return measure_risks
    finally:
        sys.path.remove(str(CORPUS_DIR))


def test_harness_imports_and_routing_smoke():
    mr = _harness()
    routing = mr.measure_routing()
    assert routing["cases_total"] > 0
    assert 0.0 <= routing["accuracy_percent"] <= 100.0
    assert routing["hits"] + routing["no_rule_falls_to_vlm"] + routing["wrong_type"] == routing["cases_total"]


def test_gate_measurement_clean_reference_has_no_false_positives(monkeypatch):
    mr = _harness()
    monkeypatch.setattr(mr, "RATES", (0.0, 0.01))
    monkeypatch.setattr(mr, "REPEATS", 1)
    gate = mr.measure_gate_false_positives()
    clean = gate["clean_reference"]
    assert clean["checked"] > 0
    assert clean["false_positive_percent"] == 0.0
    assert set(gate["by_noise_rate"]) == {"0.00", "0.01"}
    for cell in gate["by_noise_rate"].values():
        assert cell["checked"] > 0
        assert 0.0 <= cell["false_positive_percent"] <= 100.0


def test_noise_injection_is_deterministic():
    sys.path.insert(0, str(CORPUS_DIR))
    try:
        import adversarial
    finally:
        sys.path.remove(str(CORPUS_DIR))
    text = "Взыскать с должника Иванова Ивана Ивановича, ИНН 7707083893."
    assert adversarial.inject_ocr_noise(text, 0.05, seed=42) == adversarial.inject_ocr_noise(
        text, 0.05, seed=42
    )
    assert adversarial.inject_ocr_noise(text, 0.0) == text


def test_reference_text_is_built_from_declared_fields():
    sys.path.insert(0, str(CORPUS_DIR))
    try:
        import adversarial
    finally:
        sys.path.remove(str(CORPUS_DIR))
    corpus = adversarial.load_ground_truth()
    assert corpus, "data/ground_truth пуст — измерять не на чем"
    plugin_id, items = next(iter(corpus.items()))
    doc = items[0]
    doc_type = plugin_id if plugin_id != "unknown" else next(iter(corpus))
    text = adversarial.realistic_reference_text(doc, doc_type=doc_type, seed=1)
    assert len(text) >= 20
    assert "99" in text  # юридический контекст-наполнитель присутствует
