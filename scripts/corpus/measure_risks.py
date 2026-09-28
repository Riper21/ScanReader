# -*- coding: utf-8 -*-
"""
Стенд измерения открытых рисков C.1-C.5.

Запускается:  py -3 scripts/corpus/measure_risks.py [--json out.json] [--markdown out.md]

Что измеряется (все числа — на обезличенных адверсарных вариациях эталонов,
без персональных данных):
  C.1  точность маршрутизации по «грязным» именам файлов;
  C.2  доля ЛОЖНЫХ срабатываний кросс-модального гейта в зависимости от
       доли ошибок распознавания в эталонном тексте;
  C.3  доля документов, помечаемых как требующие ручной проверки;
  C.4  поведение лимита страниц на многостраничном документе;
  C.5  объём дисковых операций при последовательной обработке N документов.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts" / "corpus"))

import adversarial  # noqa: E402

from scan_reader.type_registry import get_registry  # noqa: E402
from scan_reader.verifier import VerificationStatus  # noqa: E402
from scan_reader.verifier.auditor import ZeroTrustAuditor  # noqa: E402

RATES = (0.0, 0.01, 0.02, 0.05, 0.10)
REPEATS = 20
SEED = 20260928


def _doc_type_of(item: Dict[str, Any]) -> str:
    return str(item.get("doc_type") or "").strip().lower() or "unknown"


def _rule_based_type(name: str) -> str:
    """
    Тип, который присвоит ПЕРВАЯ ступень каскада (правила по пути/имени).

    Модель не вызывается: в этой проверке важна именно эвристика, потому что
    на реальном архиве имена задаёт бухгалтерия, а не движок.
    """
    registry = get_registry()
    low = name.lower()
    for plugin in registry.enabled_by_tie_priority():
        for pattern in plugin.classifier_rules.get("path_patterns", []):
            parts = [p for p in pattern.split("*") if p]
            if parts and all(p.lower() in low for p in parts):
                return plugin.id
        for keyword in plugin.manifest.get("classifier", {}).get("keywords", []):
            kw = keyword.strip().lower()
            if kw and kw in low:
                return plugin.id
    return ""


# =========================================================================
# C.1 — маршрутизация
# =========================================================================
def measure_routing() -> Dict[str, Any]:
    cases: List[Dict[str, str]] = []
    for plugin_id, names in adversarial.MESSY_FILENAMES.items():
        for name in names:
            cases.append({"expected": plugin_id, "name": name, "got": _rule_based_type(name)})

    hits = sum(1 for c in cases if c["got"] == c["expected"])
    misrouted = [c for c in cases if c["got"] != c["expected"]]
    to_vlm = [c for c in misrouted if not c["got"]]
    wrong_type = [c for c in misrouted if c["got"]]

    return {
        "cases_total": len(cases),
        "hits": hits,
        "accuracy_percent": round(hits / len(cases) * 100.0, 1) if cases else 0.0,
        "no_rule_falls_to_vlm": len(to_vlm),
        "wrong_type": len(wrong_type),
        "examples_wrong": wrong_type[:8],
        "examples_no_rule": to_vlm[:8],
    }


# =========================================================================
# C.2 / C.3 — гейт на адверсарных эталонах
# =========================================================================
def measure_gate_false_positives() -> Dict[str, Any]:
    """
    Чистое извлечение + испорченный эталонный текст = ложное срабатывание.

    Сценарий: модель прочитала реквизит верно, OCR-эталон прочитал с ошибкой,
    гейт увидел расхождение и вернул discrepancy_detected.
    """
    corpus = adversarial.load_ground_truth()
    audit = ZeroTrustAuditor()

    by_rate: Dict[str, Dict[str, int]] = {
        f"{rate:.2f}": {"checked": 0, "flagged": 0, "statuses": {}}
        for rate in RATES
    }
    clean_flagged = 0
    clean_checked = 0

    for plugin_id, items in corpus.items():
        for index, item in enumerate(items):
            doc_type = plugin_id
            if doc_type == "unknown":
                continue
            data = {k: v for k, v in item.items() if k not in ("file_name", "file_path")}
            clean_ref = adversarial.realistic_reference_text(
                item, doc_type=doc_type, seed=SEED + index
            )

            # Контроль: чистый эталон не должен давать расхождений
            report = audit.audit_document(
                data, doc_type=doc_type, raw_ocr_text=clean_ref, gate_expected=True
            )
            clean_checked += 1
            if report.status == VerificationStatus.DISCREPANCY_DETECTED:
                clean_flagged += 1

            for rate in RATES:
                key = f"{rate:.2f}"
                for variant in adversarial.build_ocr_variants(
                    clean_ref, rates=[rate], repeats=REPEATS, seed=SEED + index
                )[rate]:
                    rep = audit.audit_document(
                        data, doc_type=doc_type, raw_ocr_text=variant, gate_expected=True
                    )
                    cell = by_rate[key]
                    cell["checked"] += 1
                    name = rep.status.value
                    cell["statuses"][name] = cell["statuses"].get(name, 0) + 1
                    if rep.status == VerificationStatus.DISCREPANCY_DETECTED:
                        cell["flagged"] += 1

    for key, cell in by_rate.items():
        cell["false_positive_percent"] = round(
            cell["flagged"] / cell["checked"] * 100.0, 1
        ) if cell["checked"] else 0.0
        # доля документов, требующих ручной проверки
        cell["requires_review_percent"] = round(
            sum(v for k, v in cell["statuses"].items()
                if k in ("discrepancy_detected", "gate_not_executed", "heuristic_fallback"))
            / cell["checked"] * 100.0, 1
        ) if cell["checked"] else 0.0

    return {
        "clean_reference": {
            "checked": clean_checked,
            "false_positive_percent": round(clean_flagged / clean_checked * 100.0, 1)
            if clean_checked else 0.0,
        },
        "by_noise_rate": by_rate,
    }


# =========================================================================
# C.4 — лимит страниц
# =========================================================================
def measure_page_limit_list() -> List[Dict[str, Any]]:
    from scan_reader.file_processor import FileProcessor

    pages = 45
    out: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "long.pdf")
        adversarial.build_multi_page_pdf(
            path, pages,
            key_lines=["ИНН должника 7707083893", "Итого к удержанию: 53500,00 руб."],
            key_page=31,
        )
        proc = FileProcessor()
        for limit in (5, 20, None):
            mode, uris = proc.prepare_document_inputs(path, max_pages=limit)
            rendered = len(uris) if isinstance(uris, list) else 0
            out.append({
                "max_pages": limit if limit is not None else "без ограничения",
                "pages_in_document": pages,
                "key_page": 31,
                "pages_rendered": rendered,
                "key_page_reached": rendered >= 31,
            })
    return out


# =========================================================================
# C.5 — объём дисковых операций
# =========================================================================
def measure_registry_io(documents: int = 200) -> Dict[str, Any]:
    from scan_reader.core.json_exporter import export_consolidated_registries

    def _record(i: int) -> Dict[str, Any]:
        return {
            "file_name": f"doc_{i}.pdf",
            "file_path": f"/inbox/doc_{i}.pdf",
            "doc_type": "hr_orders",
            "status": "COMPLETED",
            "data": {"doc_number": f"П-{i}", "doc_date": "15.01.2023",
                     "employee": {"full_name": f"Работник {i}" * 3}},
            "zero_trust_status": "zero_trust_verified",
            "zero_trust": {"status": "zero_trust_verified", "is_valid": True,
                           "issues": [], "details": {}},
        }

    with tempfile.TemporaryDirectory() as tmp:
        # Одиночный режим: слияние на каждом документе (замер регрессии)
        single_dir = os.path.join(tmp, "single")
        os.makedirs(single_dir, exist_ok=True)
        t0 = time.perf_counter()
        for i in range(1, documents + 1):
            export_consolidated_registries([_record(i)], single_dir, merge=True)
        single_elapsed = time.perf_counter() - t0
        registry = os.path.join(single_dir, "Registry_Full.json")
        registry_bytes = os.path.getsize(registry)

        # Пакетный режим: одна запись в конце (текущее поведение)
        batch_dir = os.path.join(tmp, "batch")
        os.makedirs(batch_dir, exist_ok=True)
        records = [_record(i) for i in range(1, documents + 1)]
        t0 = time.perf_counter()
        export_consolidated_registries(records, batch_dir)
        batch_elapsed = time.perf_counter() - t0

    return {
        "documents": documents,
        "registry_size_bytes": registry_bytes,
        "single_mode_seconds": round(single_elapsed, 3),
        "batch_mode_seconds": round(batch_elapsed, 3),
        "speedup": round(single_elapsed / batch_elapsed, 1) if batch_elapsed else 0.0,
    }


# =========================================================================
# Отчёт
# =========================================================================
def _table(rows: List[List[Any]], header: List[str]) -> str:
    out = ["| " + " | ".join(str(h) for h in header) + " |",
           "|" + "|".join("---" for _ in header) + "|"]
    for row in rows:
        out.append("| " + " | ".join(str(c) for c in row) + " |")
    return "\n".join(out)


def render_markdown(results: Dict[str, Any]) -> str:
    routing = results["routing"]
    gate = results["gate"]
    pages = results["page_limit"]
    io_stats = results["registry_io"]

    lines = [
        "# Измерение открытых рисков",
        "",
        "Все числа получены на **адверсарных вариациях обезличенных эталонов**,",
        "а не на реальных сканах ФССП: такие документы содержат персональные",
        "данные и в репозиторий не попадают. Абсолютные значения здесь",
        "оптимистичны — настоящие сканы с телефона хуже. Механизм и кривая",
        "деградации измерены точно.",
        "",
        "## C.1 Маршрутизация по именам файлов",
        "",
        f"Точность первой ступени каскада: **{routing['accuracy_percent']} %** "
        f"({routing['hits']} из {routing['cases_total']}).",
        "",
        f"- ушло не в тот тип: **{routing['wrong_type']}**",
        f"- правило не сработало, документ уйдёт в VLM: **{routing['no_rule_falls_to_vlm']}**",
        "",
    ]
    if routing["examples_wrong"]:
        lines += ["Примеры неверной маршрутизации:", "",
                  _table([[e["name"], e["expected"], e["got"]] for e in routing["examples_wrong"]],
                         ["файл", "ожидался", "получено"]),
                  ""]
    if routing["examples_no_rule"]:
        lines += ["Файлы без правила (уйдут в VLM, то есть дороже):", "",
                  _table([[e["name"], e["expected"]] for e in routing["examples_no_rule"]],
                         ["файл", "ожидался"]),
                  ""]

    clean = gate["clean_reference"]
    lines += [
        "## C.2 Ложные срабатывания гейта",
        "",
        f"Контроль на чистом эталоне: **{clean['false_positive_percent']} %** "
        f"расхождений ({clean['checked']} проверок).",
        "",
        "Далее эталонный текст портится ошибками распознавания, а извлечённые",
        "данные остаются верными — это и есть ложное срабатывание.",
        "",
        f"measurement_caveats: эталонов — {clean['checked']}, повторов на уровень",
        f"шума — {REPEATS}, seed — {SEED}; шум вносится только в эталонный текст,",
        "извлечение всегда чистое. Воспроизведение:",
        "`py -3 scripts/corpus/measure_risks.py --markdown <файл>`.",
        "",
        _table(
            [[rate, cell["checked"], f"**{cell['false_positive_percent']} %**",
              f"{cell['requires_review_percent']} %"]
             for rate, cell in gate["by_noise_rate"].items()],
            ["доля ошибок OCR", "проверок", "ложных срабатываний", "требуют проверки"],
        ),
        "",
    ]

    lines += [
        "## C.4 Лимит страниц",
        "",
        _table(
            [[r["max_pages"], r["pages_in_document"], r["pages_rendered"],
              "да" if r["key_page_reached"] else "**нет**"]
             for r in pages],
            ["max_pages", "страниц в документе", "отрендерено", "страница 31 достигнута"],
        ),
        "",
        "## C.5 Объём дисковых операций",
        "",
        _table(
            [[io_stats["documents"],
              f"{io_stats['registry_size_bytes'] / 1024:.0f} КБ",
              f"{io_stats['single_mode_seconds']} с",
              f"{io_stats['batch_mode_seconds']} с",
              f"×{io_stats['speedup']}"]],
            ["документов", "размер реестра", "одиночный режим", "пакетный режим", "ускорение"],
        ),
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Измерение открытых рисков")
    parser.add_argument("--json", help="выгрузить результаты в JSON")
    parser.add_argument("--markdown", help="выгрузить отчёт в Markdown")
    args = parser.parse_args()

    print("C.1 маршрутизация ...", flush=True)
    routing = measure_routing()
    print(f"    точность {routing['accuracy_percent']} %")

    print("C.2/C.3 ложные срабатывания гейта ...", flush=True)
    gate = measure_gate_false_positives()
    for rate, cell in gate["by_noise_rate"].items():
        print(f"    шум {rate}: ложных {cell['false_positive_percent']} %")

    print("C.4 лимит страниц ...", flush=True)
    pages = measure_page_limit_list()
    print(f"    отрендерено при max_pages=20: {pages[1]['pages_rendered']} из {pages[1]['pages_in_document']}")

    print("C.5 объём дисковых операций ...", flush=True)
    io_stats = measure_registry_io()
    print(f"    одиночный {io_stats['single_mode_seconds']} с, "
          f"пакетный {io_stats['batch_mode_seconds']} с")

    results = {
        "routing": routing,
        "gate": gate,
        "page_limit": pages,
        "registry_io": io_stats,
    }

    if args.json:
        Path(args.json).write_text(
            json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"JSON: {args.json}")
    if args.markdown:
        Path(args.markdown).write_text(render_markdown(results), encoding="utf-8")
        print(f"Markdown: {args.markdown}")
    if not args.json and not args.markdown:
        sys.stdout.reconfigure(encoding="utf-8")
        print()
        print(render_markdown(results))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
