"""
Modular Command-Line Interface for ScanReader.
Provides subcommands for processing, classification, Zero-Trust verification,
exporting, benchmarking, diagnostics, and MCP server launch.
Retains 100% backward compatibility for 1C silent integration.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import __version__
from .core.io_utils import (
    configure_streams,
    mask_secret,
)
from .core.utils import get_logger, sanitize_filename
from .facade import LegalDocPlatformFacade
from .type_registry import get_registry
from .verifier.auditor import ZeroTrustAuditor

_cli_logger = get_logger("cli")

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_DISCREPANCY = 3
EXIT_FALLBACK = 4


def _redirect_loggers_to_stderr(verbose: bool = False) -> None:
    """
    Перенаправляет все логгеры платформы в stderr и настраивает уровень детализации (H-05).
    Работает с уже созданными логгерами (facade, cache и др. получили хендлеры при импорте),
    поэтому недостаточно патчить utils.get_logger — конфигурируются существующие инстансы.
    """
    log_level = logging.INFO if verbose else logging.ERROR
    os.environ["LOG_LEVEL"] = "INFO" if verbose else "ERROR"

    for name in list(logging.Logger.manager.loggerDict.keys()) + ["root"]:
        lg = logging.getLogger(name)
        if not isinstance(lg, logging.Logger) or name == "root":
            continue
        # Настраиваем только логгеры платформы (scan_reader / core / facade и т.п.)
        if not (name.startswith("core.") or name in ("facade", "file_processor", "json_exporter",
                                                     "excel_exporter", "launcher", "type_registry",
                                                     "mcp") or name.startswith("scan_reader")):
            continue
        lg.setLevel(log_level)
        for h in lg.handlers:
            if isinstance(h, logging.StreamHandler) and not type(h).__module__.startswith("_pytest"):
                # Под pytest sys.stderr пересоздается на каждый тест: подмена потока
                # в хендлере оставит ссылку на закрытый файл. Перенаправляем только в проде.
                if "pytest" not in sys.modules:
                    h.setStream(sys.stderr)


def _emit_json(data: Any) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    sys.stdout.flush()


def _style(text: str, color: str = "", bold: bool = False) -> str:
    """Apply terminal ANSI styling if supported and not disabled."""
    if os.getenv("NO_COLOR") or not sys.stdout.isatty():
        return text
    codes = []
    if bold:
        codes.append("1")
    color_map = {
        "green": "32",
        "bright_green": "92",
        "red": "31",
        "bright_red": "91",
        "yellow": "33",
        "bright_yellow": "93",
        "cyan": "36",
        "bright_cyan": "96",
        "gray": "90",
    }
    if color in color_map:
        codes.append(color_map[color])
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


def _emit_error(msg: str, code: int = EXIT_ERROR, is_json: bool = False) -> int:
    clean_msg = mask_secret(msg)
    if is_json:
        _emit_json({"status": "error", "error": clean_msg, "exit_code": code})
    else:
        err_badge = _style("[ОШИБКА]", "bright_red", bold=True)
        sys.stderr.write(f"{err_badge} {clean_msg}\n")
    return code


# =============================================================================
# SUBCOMMAND HANDLERS
# =============================================================================

def _resolve_output_dir(args: argparse.Namespace) -> str:
    """Каталог результатов: явный -o, либо легаси «Результаты», либо output."""
    if args.output_dir:
        return args.output_dir
    legacy_results = os.path.join(os.getcwd(), "Результаты")
    default_output = os.path.join(os.getcwd(), "output")
    if os.path.exists(legacy_results) and not os.path.exists(default_output):
        return legacy_results
    return default_output


def _result_exit_code(result: Dict[str, Any]) -> int:
    """
    Код возврата по одному результату обработки (контракт C-10 + C-08).

    Провальная экстракция обязана давать EXIT_ERROR: код 0 при пустых данных
    говорил бы 1С, что документ обработан успешно.
    """
    if result.get("status") == "FAILED":
        return EXIT_ERROR
    zt_status = result.get("zero_trust_status")
    if zt_status in ("discrepancy_detected", "gate_not_executed"):
        # Реквизиты не подтверждены исходным текстом: автоимпорт недопустим.
        return EXIT_DISCREPANCY
    if zt_status == "heuristic_fallback":
        return EXIT_FALLBACK
    return EXIT_OK


def _batch_exit_code(results: List[Dict[str, Any]]) -> int:
    """
    Агрегированный код возврата пакета.

    Приоритет: сбой (1) важнее расхождения (3) важнее эвристики (4) —
    незавершённая обработка важнее найденных расхождений в завершённых.
    Пустой пакет — тоже ошибка: поддерживаемых документов не нашлось.
    """
    if not results:
        return EXIT_ERROR
    codes = [_result_exit_code(r) for r in results]
    if EXIT_ERROR in codes:
        return EXIT_ERROR
    if EXIT_DISCREPANCY in codes:
        return EXIT_DISCREPANCY
    if EXIT_FALLBACK in codes:
        return EXIT_FALLBACK
    return EXIT_OK


def _handle_run_directory(args: argparse.Namespace, scan_dir: Path) -> int:
    """Пакетная обработка каталога: help обещал «файл или каталог» (0.9.2)."""
    output_dir = _resolve_output_dir(args)
    facade = LegalDocPlatformFacade()
    facade.results_dir = output_dir

    try:
        results = facade.process_batch(str(scan_dir), organize_subfolders=False)
    except Exception as e:
        sys.stderr.write(f"[ОШИБКА] Сбой пакетной обработки '{scan_dir.name}': {mask_secret(str(e))}\n")
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR

    if getattr(args, "json_mode", False):
        _emit_json(results)
    elif results:
        failed = sum(1 for r in results if r.get("status") == "FAILED")
        sys.stdout.write(
            f"Обработано документов: {len(results)} (сбоев: {failed}). "
            f"Реестры: {os.path.abspath(output_dir)}\n"
        )
        registry = os.path.join(output_dir, "all_documents_registry.json")
        if os.path.exists(registry):
            sys.stdout.write(f"{os.path.abspath(registry)}\n")
        sys.stdout.flush()
    else:
        sys.stderr.write(f"[ОШИБКА] Поддерживаемых документов не найдено: {scan_dir}\n")

    return _batch_exit_code(results)


def handle_run(args: argparse.Namespace) -> int:
    """Process a single document or directory of documents."""
    scan_file = Path(args.scan_path).resolve()
    if not scan_file.exists():
        sys.stderr.write(f"[ОШИБКА] Входной файл не найден: {scan_file}\n")
        return EXIT_ERROR

    if scan_file.is_dir():
        return _handle_run_directory(args, scan_file)

    output_dir = _resolve_output_dir(args)
    facade = LegalDocPlatformFacade()
    facade.results_dir = output_dir

    doc_type_arg = None if args.type == "auto" else args.type
    try:
        result = facade.process_single_document(
            file_path=str(scan_file),
            doc_type=doc_type_arg,
        )

        detected_type = result.get("doc_type", "unknown")
        stem = sanitize_filename(scan_file.stem)

        full_json_path = os.path.join(output_dir, f"{stem}_Full.json")
        flat_json_path = os.path.join(output_dir, f"{stem}_Flat.json")
        default_json_path = os.path.join(output_dir, f"{stem}_{detected_type}.json")
        import_1c_path = os.path.join(output_dir, "1C_Импорт", f"{stem}_1c.json")
        raw_json_path = os.path.join(output_dir, f"{stem}_raw.json")

        if getattr(args, "json_mode", False):
            _emit_json(result)
            return _result_exit_code(result)

        if args.format == "stdout":
            if os.path.exists(flat_json_path):
                with open(flat_json_path, "r", encoding="utf-8") as f:
                    sys.stdout.write(f.read() + "\n")
            elif os.path.exists(import_1c_path):
                with open(import_1c_path, "r", encoding="utf-8") as f:
                    sys.stdout.write(f.read() + "\n")
            elif os.path.exists(default_json_path):
                with open(default_json_path, "r", encoding="utf-8") as f:
                    sys.stdout.write(f.read() + "\n")
            else:
                _emit_json(result)

        elif args.format == "flat":
            if os.path.exists(flat_json_path):
                sys.stdout.write(f"{os.path.abspath(flat_json_path)}\n")
            elif os.path.exists(import_1c_path):
                sys.stdout.write(f"{os.path.abspath(import_1c_path)}\n")
            elif os.path.exists(default_json_path):
                sys.stdout.write(f"{os.path.abspath(default_json_path)}\n")
            else:
                sys.stdout.write(f"{flat_json_path}\n")

        elif args.format == "full":
            target = full_json_path if os.path.exists(full_json_path) else default_json_path
            sys.stdout.write(f"{os.path.abspath(target)}\n")

        elif args.format == "both":
            f_target = full_json_path if os.path.exists(full_json_path) else default_json_path
            fl_target = flat_json_path if os.path.exists(flat_json_path) else (import_1c_path if os.path.exists(import_1c_path) else default_json_path)
            sys.stdout.write(f"{os.path.abspath(f_target)}\n{os.path.abspath(fl_target)}\n")

        elif args.format == "1c":
            if os.path.exists(import_1c_path):
                sys.stdout.write(f"{os.path.abspath(import_1c_path)}\n")
            elif os.path.exists(flat_json_path):
                sys.stdout.write(f"{os.path.abspath(flat_json_path)}\n")
            elif os.path.exists(default_json_path):
                sys.stdout.write(f"{os.path.abspath(default_json_path)}\n")
            else:
                sys.stdout.write(f"{default_json_path}\n")

        elif args.format == "raw":
            target = raw_json_path if os.path.exists(raw_json_path) else default_json_path
            sys.stdout.write(f"{os.path.abspath(target)}\n")

        else:  # default
            target = flat_json_path if os.path.exists(flat_json_path) else default_json_path
            sys.stdout.write(f"{os.path.abspath(target)}\n")

        sys.stdout.flush()
        return _result_exit_code(result)

    except Exception as e:
        sys.stderr.write(f"[ОШИБКА] Сбой при обработке документа '{scan_file.name}': {mask_secret(str(e))}\n")
        if getattr(args, "verbose", False):
            import traceback
            traceback.print_exc(file=sys.stderr)
        return EXIT_ERROR


def handle_classify(args: argparse.Namespace) -> int:
    """Classify a document via Fast-Path routing."""
    scan_file = Path(args.scan_path).resolve()
    if not scan_file.is_file():
        return _emit_error(f"Файл не найден: {scan_file}", EXIT_ERROR, args.json)

    facade = LegalDocPlatformFacade()
    doc_type, conf, method = facade.classify_document(str(scan_file))

    payload = {
        "file_name": scan_file.name,
        "file_path": str(scan_file),
        "doc_type": doc_type,
        "confidence": conf,
        "method": method,
    }

    if args.json:
        _emit_json(payload)
    else:
        type_badge = _style(doc_type, "bright_cyan", bold=True)
        conf_str = _style(f"{conf * 100:.1f}%", "bright_green" if conf >= 0.8 else "yellow")
        sys.stdout.write(f"Тип документа: {type_badge} | Достоверность: {conf_str} | Метод: {method}\n")
    return EXIT_OK


def handle_verify(args: argparse.Namespace) -> int:
    """Run Zero-Trust verification on an extracted JSON file."""
    json_path = Path(args.json_path).resolve()
    if not json_path.is_file():
        return _emit_error(f"JSON файл не найден: {json_path}", EXIT_ERROR, args.json)

    try:
        data = json.loads(json_path.read_text(encoding="utf-8-sig"))
    except Exception as e:
        return _emit_error(f"Некорректный JSON файл: {e}", EXIT_ERROR, args.json)

    if isinstance(data, list):
        data = data[0] if data else {}

    doc_data = data.get("data", data)
    doc_type = data.get("doc_type", args.type or "unknown")

    report = ZeroTrustAuditor.audit_document(data=doc_data, doc_type=doc_type)

    if args.json:
        _emit_json(report.to_dict())
    else:
        if report.is_valid:
            status_badge = _style(f"[{report.status.value.upper()}]", "bright_green", bold=True)
            status_label = _style("✔ ВЕРИФИЦИРОВАНО", "bright_green")
        else:
            status_badge = _style(f"[{report.status.value.upper()}]", "bright_red", bold=True)
            status_label = _style("✘ ОБНАРУЖЕНЫ НЕСООТВЕТСТВИЯ", "bright_red")

        sys.stdout.write(f"Статус: {status_badge} ({status_label})\n")
        if report.issues:
            sys.stdout.write(_style("Замечания аудитора:\n", bold=True))
            for i in report.issues:
                sev_color = "bright_red" if i.severity == "error" else "bright_yellow"
                badge = _style(f"[{i.severity.upper()}]", sev_color, bold=True)
                field_str = f" ({i.field_name})" if i.field_name else ""
                sys.stdout.write(f"  • {badge} {i.code}{field_str}: {i.message}\n")

    return EXIT_OK if report.is_valid else EXIT_DISCREPANCY


def handle_export(args: argparse.Namespace) -> int:
    """Consolidate JSON files into 1C and Excel registries."""
    import glob
    from .core.json_exporter import export_consolidated_registries
    from .excel_exporter import LegalExcelExporter

    results_dir = Path(args.results_dir).resolve()
    if not results_dir.is_dir():
        if args.results_dir == "output" and Path("Результаты").is_dir():
            results_dir = Path("Результаты").resolve()
        elif args.results_dir == "Результаты" and Path("output").is_dir():
            results_dir = Path("output").resolve()
        else:
            return _emit_error(f"Каталог не найден: {results_dir}", EXIT_ERROR, args.json)

    json_files = glob.glob(str(results_dir / "*.json"))
    loaded = []
    seen_stems = set()
    for jf in json_files:
        base = os.path.basename(jf)
        if (
            base.startswith("run_metrics")
            or base.startswith("benchmark_")
            or base.startswith("Registry_")
            or base.endswith("_registry.json")
            or base.endswith("_registry_1c.json")
            or base.endswith("_Flat.json")
            or base.endswith("_quality_metrics.json")
            or base == "metrics_history.json"
        ):
            continue
        try:
            with open(jf, "r", encoding="utf-8") as f:
                d = json.load(f)
                if isinstance(d, dict):
                    stem = base.replace("_Full.json", "").replace("_raw.json", "")
                    doc_type_suffixes = tuple(f"_{pid}.json" for pid in get_registry().ids()) + ("_unknown.json",)
                    for dt_suffix in doc_type_suffixes:
                        if stem.endswith(dt_suffix):
                            stem = stem[:-len(dt_suffix)]
                    if stem in seen_stems and not base.endswith("_Full.json"):
                        continue
                    seen_stems.add(stem)
                    loaded.append(d)
                elif isinstance(d, list):
                    loaded.extend(d)
        except Exception as e:
            _cli_logger.debug(f"Пропущен некорректный JSON файл '{jf}': {e}")

    if not loaded:
        return _emit_error(f"Нет файлов результатов в каталоге: {results_dir}", EXIT_ERROR, args.json)

    exported: List[str] = []
    if args.format in ("1c", "flat", "full", "both"):
        jsons = export_consolidated_registries(loaded, str(results_dir))
        exported.extend(jsons)

    if args.format in ("excel", "both"):
        exporter = LegalExcelExporter()
        out_excel = exporter.export_results_to_excel(loaded, output_dir=str(results_dir))
        exported.append(os.path.basename(out_excel))

    payload = {"records_count": len(loaded), "exported_files": exported, "results_dir": str(results_dir)}
    if args.json:
        _emit_json(payload)
    else:
        header = _style(f"✅ Экспортировано {len(loaded)} записей в {len(exported)} файлов:", "bright_green", bold=True)
        sys.stdout.write(f"{header}\n")
        for ef in exported:
            sys.stdout.write(f"  • {_style(ef, 'cyan')}\n")
    return EXIT_OK


def handle_benchmark(args: argparse.Namespace) -> int:
    """Run benchmark against ground truth."""
    import glob
    from .core.metrics_evaluator import evaluate_dataset, generate_run_summary
    from .type_registry import get_registry

    results_dir = Path(args.results_dir).resolve()
    if not results_dir.is_dir():
        if args.results_dir == "output" and Path("Результаты").is_dir():
            results_dir = Path("Результаты").resolve()
        elif args.results_dir == "Результаты" and Path("output").is_dir():
            results_dir = Path("output").resolve()
        else:
            return _emit_error(f"Каталог результатов не найден: {results_dir}", EXIT_ERROR, args.json)

    gt_dir = Path(args.ground_truth).resolve()

    if not gt_dir.is_dir():
        return _emit_error(f"Каталог эталонов не найден: {gt_dir}", EXIT_ERROR, args.json)

    results_map: Dict[str, List[Dict[str, Any]]] = {}
    for jf in glob.glob(str(results_dir / "*.json")):
        base = os.path.basename(jf)
        if base.startswith("run_metrics") or base.startswith("benchmark_") or base.endswith("_quality_metrics.json"):
            continue
        try:
            with open(jf, "r", encoding="utf-8") as f:
                d = json.load(f)
                items = d if isinstance(d, list) else [d]
                for it in items:
                    dt = it.get("doc_type", "unknown")
                    results_map.setdefault(dt, []).append(it)
        except Exception as e:
            _cli_logger.debug(f"Пропущен некорректный JSON файл '{jf}': {e}")

    registry = get_registry()
    category_metrics: Dict[str, Dict[str, Any]] = {}

    for plugin in registry.enabled().values():
        cat_id = plugin.id
        gt_path = gt_dir / plugin.gt_file
        gt_items = None
        if gt_path.is_file():
            try:
                with open(gt_path, "r", encoding="utf-8") as f:
                    gt_items = json.load(f)
            except Exception:
                gt_items = None

        pred_items = results_map.get(cat_id, [])
        if pred_items or gt_items:
            cat_metrics = evaluate_dataset(pred_items, gt_items, doc_type=cat_id)
            category_metrics[cat_id] = cat_metrics

    summary = generate_run_summary(category_metrics)

    if args.json:
        _emit_json(summary)
    else:
        avg_acc = summary.get("average_quality_score_percent", 0.0)
        total_docs = summary.get("total_documents", 0)
        header_text = _style("📊 Результаты эталонного тестирования (Ground Truth):", "bright_cyan", bold=True)
        sys.stdout.write(f"\n{header_text}\n")
        sys.stdout.write(f"  • Всего документов: {_style(str(total_docs), bold=True)}\n")
        sys.stdout.write(f"  • Средняя точность: {_style(f'{avg_acc:.2f}%', 'bright_green' if avg_acc >= 85 else 'bright_yellow', bold=True)}\n")
        for cat_k, cat_v in category_metrics.items():
            cat_score = cat_v.get("average_quality_score_percent", 0.0)
            sys.stdout.write(f"    - {cat_k}: {_style(f'{cat_score:.1f}%', 'green' if cat_score >= 85 else 'yellow')}\n")
    return EXIT_OK


def handle_doctor(args: argparse.Namespace) -> int:
    """Run self-healing diagnostics on VLM, TTFT, and GPU."""
    from .core.diagnostics import run_vlm_diagnostics
    diag = run_vlm_diagnostics(verbose=not args.json)
    if args.json:
        _emit_json(diag)
    else:
        if diag.get("status") == "ok":
            sys.stdout.write(_style("[✔] Все системы исправны и готовы к работе\n", "bright_green", bold=True))
        else:
            sys.stdout.write(_style(f"[✘] Обнаружены проблемы конфигурации: {diag.get('error', 'unknown')}\n", "bright_red", bold=True))
    return EXIT_OK if diag.get("status") == "ok" else EXIT_ERROR


def handle_mcp(args: argparse.Namespace) -> int:
    """Run Model Context Protocol (MCP) server over Stdio."""
    from .mcp.server import run_stdio_server
    run_stdio_server()
    return EXIT_OK


# =============================================================================
# CLI PARSER BUILDER
# =============================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scan-reader",
        description="ScanReader CLI: AI legal document recognition, Zero-Trust verification, and 1C/Excel orchestration.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("-v", "--verbose", action="store_true", help="Выводить расширенные логи в stderr")

    subparsers = parser.add_subparsers(dest="command")

    # Command: run (and backward-compatible file processing)
    run_parser = subparsers.add_parser("run", help="Обработать документ или пакет документов")
    run_parser.add_argument("scan_path", help="Путь к файлу скана или каталогу")
    run_parser.add_argument(
        "-f", "--format",
        choices=["1c", "flat", "full", "both", "default", "raw", "stdout"],
        default="1c",
        help="Формат вывода пути/данных в stdout (по умолчанию: 1c / flat, также: full, both, raw, stdout)"
    )
    run_parser.add_argument(
        "-t", "--type",
        default="auto",
        help="Тип документа: 'auto', 'salary_deductions', 'executive_documents', 'enforcement_orders'"
    )
    run_parser.add_argument("-o", "--output-dir", default=None, help="Каталог для результатов")
    run_parser.add_argument(
        "--json", dest="json_mode", action="store_true",
        help="Вывести полный JSON результат (для каталога — список результатов)",
    )

    # Command: classify
    class_parser = subparsers.add_parser("classify", help="Классифицировать документ по шапке и якорям")
    class_parser.add_argument("scan_path", help="Путь к файлу")
    class_parser.add_argument("--json", action="store_true", help="Машиночитаемый JSON вывод")

    # Command: verify
    ver_parser = subparsers.add_parser("verify", help="Выполнить Zero-Trust аудит извлеченного JSON")
    ver_parser.add_argument("json_path", help="Путь к JSON файлу")
    ver_parser.add_argument("-t", "--type", default="salary_deductions", help="Ожидаемый тип документа")
    ver_parser.add_argument("--json", action="store_true", help="JSON отчет аудита")

    # Command: export
    exp_parser = subparsers.add_parser("export", help="Собрать сводные реестры 1C и Excel")
    exp_parser.add_argument("results_dir", nargs="?", default="output", help="Каталог с результатами (по умолчанию: output)")
    exp_parser.add_argument("-f", "--format", choices=["1c", "flat", "full", "excel", "both"], default="both")
    exp_parser.add_argument("--json", action="store_true", help="JSON отчет экспорта")

    # Command: benchmark
    bench_parser = subparsers.add_parser("benchmark", help="Запустить бенчмарк по Ground Truth")
    bench_parser.add_argument("-r", "--results-dir", default="output", help="Каталог результатов (по умолчанию: output)")
    bench_parser.add_argument("-g", "--ground-truth", default="data/ground_truth", help="Каталог эталонов")
    bench_parser.add_argument("--json", action="store_true", help="JSON отчет бенчмарка")

    # Command: doctor
    doc_parser = subparsers.add_parser("doctor", help="Диагностика VLM, TTFT, токенов и окружения")
    doc_parser.add_argument("--json", action="store_true", help="Машиночитаемый JSON отчет")

    # Command: mcp
    subparsers.add_parser(
        "mcp",
        help="Запустить JSON-RPC сервер инструментов по Stdio (приватный протокол ScanReader)",
    )

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    configure_streams()
    raw_args = list(sys.argv[1:] if argv is None else argv)

    # Обратная совместимость: если первый аргумент — файл или опция без подкоманды,
    # инжектируем 'run' (H-06: корректная обработка глобальных флагов до подкоманды).
    subcommands = {"run", "classify", "verify", "export", "benchmark", "doctor", "mcp", "--help", "-h", "--version"}
    passthrough_flags = {"--version", "-h", "--help"}
    global_flags = {"-v", "--verbose"}
    if raw_args and raw_args[0] not in passthrough_flags:
        first_positional_idx = next(
            (i for i, t in enumerate(raw_args) if not t.startswith("-") and t != "--"),
            None,
        )
        if first_positional_idx is not None and raw_args[first_positional_idx] not in subcommands:
            leading_flags = [t for t in raw_args[:first_positional_idx] if t != "--"]
            # Глобальные флаги (-v) переносятся перед 'run'; неизвестные флаги
            # (например -o) считаются флагами самой подкоманды -> инжекция в начало.
            insert_at = first_positional_idx if all(t in global_flags for t in leading_flags) else 0
            raw_args.insert(insert_at, "run")

    parser = build_parser()
    args = parser.parse_args(raw_args)

    if not args.command:
        parser.print_help()
        return EXIT_OK

    _redirect_loggers_to_stderr(verbose=getattr(args, "verbose", False))

    handlers = {
        "run": handle_run,
        "classify": handle_classify,
        "verify": handle_verify,
        "export": handle_export,
        "benchmark": handle_benchmark,
        "doctor": handle_doctor,
        "mcp": handle_mcp,
    }

    handler = handlers.get(args.command)
    if handler:
        code = handler(args)
        sys.exit(code)

    parser.print_help()
    sys.exit(EXIT_USAGE)


if __name__ == "__main__":
    sys.exit(main())
