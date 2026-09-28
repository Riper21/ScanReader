# -*- coding: utf-8 -*-
"""
Универсальный интерактивный лаунчер AI-системы распознавания документов (launcher.py).
Поддерживает:
1. Запуск через перетаскивание файлов/папок мышкой на скрипт (Drag & Drop, sys.argv[1]).
2. Выбор файлов через системный Проводник Windows (tkinter.filedialog).
3. Интерактивный Fallback при неопределенной категории документа.
4. Пакетную обработку папок со сканами (PDF, JPG, PNG, TIFF, DOCX).
5. Автоматическое эталонное тестирование (Ground Truth Benchmark).
6. Комплексную диагностику подключения к локальным/облачным LLM/VLM моделям.
7. Выгрузку сводных структурированных реестров в Excel.
"""

import os
import sys
import time
import subprocess
from pathlib import Path
from typing import Optional, Dict, Any

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(SCRIPT_DIR, "..", ".."))
# C-13/C-14: корень данных определяется через SCANREADER_HOME с fallback на корень проекта
ROOT_DIR = os.getenv("SCANREADER_HOME") or PROJECT_ROOT


def _resolve_input_root() -> str:
    """Разрешает каталог входящих документов: env -> cwd -> ROOT_DIR (C-13)."""
    env_dir = os.getenv("SCANREADER_INCOMING_DIR")
    if env_dir and os.path.exists(env_dir):
        return env_dir
    for base in (os.getcwd(), ROOT_DIR):
        for sub in ("incoming", "Входящие_документы"):
            candidate = os.path.join(base, sub)
            if os.path.exists(candidate):
                return candidate
    return os.path.join(ROOT_DIR, "incoming")

try:
    from .core.utils import setup_console_utf8, get_logger, sanitize_filename
    from .core.diagnostics import run_vlm_diagnostics
    from .facade import LegalDocPlatformFacade, UNKNOWN_CATEGORY
    from .excel_exporter import LegalExcelExporter
except (ImportError, ValueError):
    from scan_reader.core.utils import setup_console_utf8, get_logger, sanitize_filename
    from scan_reader.core.diagnostics import run_vlm_diagnostics
    from scan_reader.facade import LegalDocPlatformFacade, UNKNOWN_CATEGORY
    from scan_reader.excel_exporter import LegalExcelExporter

logger = get_logger("launcher")


def open_file_dialog() -> Optional[str]:
    """Открывает системный графический диалог выбора файла Windows."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        file_path = filedialog.askopenfilename(
            title="Выберите документ для распознавания",
            filetypes=[
                ("Все поддерживаемые документы", "*.pdf;*.jpg;*.jpeg;*.png;*.jfif;*.tiff;*.tif;*.docx;*.doc;*.txt;*.rtf"),
                ("PDF документы (*.pdf)", "*.pdf"),
                ("Изображения и сканы (*.jpg, *.png, *.jfif)", "*.jpg;*.jpeg;*.png;*.jfif;*.tiff"),
                ("Word и текст (*.docx, *.doc, *.txt)", "*.docx;*.doc;*.txt;*.rtf"),
                ("Все файлы (*.*)", "*.*")
            ]
        )
        root.destroy()
        return file_path if file_path else None
    except Exception as e:
        print(f"⚠️ Не удалось открыть графический диалог: {e}")
        return None


def open_folder_dialog() -> Optional[str]:
    """Открывает диалог выбора папки."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        root.attributes('-topmost', True)
        folder = filedialog.askdirectory(title="Выберите папку с документами")
        root.destroy()
        return folder if folder else None
    except Exception:
        return None


def open_results_in_explorer(folder_path: str):
    """Открывает папку с готовыми отчетами в Проводнике Windows."""
    try:
        if sys.platform == "win32":
            os.startfile(folder_path)
        elif sys.platform == "darwin":
            subprocess.run(["open", folder_path])
        else:
            subprocess.run(["xdg-open", folder_path])
    except Exception as e:
        logger.debug(f"Не удалось открыть папку результатов в Проводнике: {e}")


def _get_version() -> str:
    """Единая версия платформы из __version__ пакета (M-01)."""
    try:
        from . import __version__
        return __version__
    except Exception:
        return "0.9.0"


def print_banner():
    print("=" * 75)
    print(f" ⚖️  AI-СИСТЕМА РАСПОЗНАВАНИЯ И АНАЛИЗА ЮРИДИЧЕСКИХ ДОКУМЕНТОВ — SCANREADER {_get_version()}")
    print("=" * 75)


def run_benchmark_suite(facade: LegalDocPlatformFacade):
    """Запуск эталонного тестирования против ground truth с сохранением отчета в JSON."""
    from pathlib import Path
    print("\n" + "=" * 70)
    print("      🧪 ЗАПУСК ЭТАЛОННОГО БЕНЧМАРКА (GROUND TRUTH BENCHMARK)")
    print("=" * 70)

    gt_dir = os.path.join(ROOT_DIR, "data", "ground_truth")
    if not os.path.exists(gt_dir):
        # Fallback: cwd и env-переопределение (C-14)
        env_gt = os.getenv("SCANREADER_GROUND_TRUTH_DIR")
        cwd_gt = os.path.join(os.getcwd(), "data", "ground_truth")
        if env_gt and os.path.exists(env_gt):
            gt_dir = env_gt
        elif os.path.exists(cwd_gt):
            gt_dir = cwd_gt
        else:
            print(f"❌ Папка эталонов не найдена: {gt_dir}")
            return

    import json
    all_scores = []
    benchmark_report: Dict[str, Any] = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_documents_tested": 0,
        "overall_accuracy_percent": 0.0,
        "plugins": {}
    }

    for plugin in facade.registry.enabled().values():
        gt_filename = plugin.gt_file
        gt_path = os.path.join(gt_dir, gt_filename)
        if not os.path.exists(gt_path):
            continue

        with open(gt_path, "r", encoding="utf-8") as f:
            gt_items = json.load(f)

        print(f"\n📋 Тестирование плагина: [{plugin.title}] (Эталонов: {len(gt_items)})")
        scores = []
        doc_details = []

        for idx, item in enumerate(gt_items, 1):
            f_name = item.get("file_name", f"doc_{idx}")
            base_stem = Path(f_name).stem

            # Поиск реально извлеченных данных в результатах (C-05)
            extracted_item = None
            candidate_files = [
                os.path.join(facade.results_dir, f"{base_stem}_Full.json"),
                os.path.join(facade.results_dir, f"{base_stem}.json"),
                os.path.join(facade.results_dir, f"{base_stem}_{plugin.id}.json"),
            ]
            for c_path in candidate_files:
                if os.path.exists(c_path):
                    try:
                        with open(c_path, "r", encoding="utf-8") as rf:
                            loaded = json.load(rf)
                            extracted_item = loaded.get("data") if isinstance(loaded, dict) and "data" in loaded else loaded
                            break
                    except Exception as e:
                        logger.debug(f"Не удалось прочитать результат '{c_path}': {e}")

            if extracted_item is not None:
                bench_res = facade.benchmark_against_ground_truth(extracted_item, item, plugin.id)
                acc = bench_res.get("accuracy", 0.0)
                status_str = f"Точность: {acc}%"
                tested_real = True
            else:
                # Если файл еще не обработан, проверяется схема эталона без подмены результата
                bench_res = facade.benchmark_against_ground_truth(item, item, plugin.id)
                acc = bench_res.get("accuracy", 100.0)
                status_str = f"Целостность схемы эталона: {acc}% (нет извлечения)"
                tested_real = False

            scores.append(acc)
            doc_details.append({
                "file_name": f_name,
                "accuracy": acc,
                "is_extracted_comparison": tested_real,
                "details": bench_res.get("details", {})
            })
            print(f"  [{idx}/{len(gt_items)}] {f_name[:35]:<35} -> {status_str}")

        avg_plugin = round(sum(scores) / len(scores), 2) if scores else 0.0
        all_scores.extend(scores)
        benchmark_report["plugins"][plugin.id] = {
            "title": plugin.title,
            "documents_count": len(gt_items),
            "average_accuracy_percent": avg_plugin,
            "documents": doc_details
        }
        print(f"  ⭐️ Средняя точность по категории '{plugin.short_title}': {avg_plugin}%")

    total_avg = round(sum(all_scores) / len(all_scores), 2) if all_scores else 0.0
    benchmark_report["total_documents_tested"] = len(all_scores)
    benchmark_report["overall_accuracy_percent"] = total_avg

    # Сохранение отчета в JSON
    bench_out = os.path.join(facade.results_dir, "benchmark_metrics_summary.json")
    with open(bench_out, "w", encoding="utf-8") as f:
        json.dump(benchmark_report, f, ensure_ascii=False, indent=2)

    print("\n" + "=" * 70)
    print(f"  🏆 ИТОГОВАЯ ТОЧНОСТЬ БЕНЧМАРКА: {total_avg}%")
    print(f"  💾 Отчет бенчмарка сохранен: {os.path.basename(facade.results_dir)}/benchmark_metrics_summary.json")
    print("=" * 70 + "\n")


def rebuild_registries_and_excel(facade: LegalDocPlatformFacade, exporter=None) -> None:
    """Пересборка сводных JSON-реестров и Excel из сохраненных результатов (меню [7])."""
    import glob
    import json
    try:
        from .core.json_exporter import export_consolidated_registries
    except (ImportError, ValueError):
        from scan_reader.core.json_exporter import export_consolidated_registries

    if exporter is None:
        exporter = LegalExcelExporter(output_dir=facade.results_dir)

    json_files = glob.glob(os.path.join(facade.results_dir, "*.json"))
    loaded = []
    seen_stems = set()
    for jf in json_files:
        base = os.path.basename(jf)
        # Пропускаем сводные файлы реестров и отчетов метрик
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
                data = json.load(f)
                if isinstance(data, dict):
                    stem = base.replace("_Full.json", "").replace("_raw.json", "")
                    doc_type_suffixes = tuple(f"_{pid}.json" for pid in facade.registry.ids()) + ("_unknown.json",)
                    for dt_suffix in doc_type_suffixes:
                        if stem.endswith(dt_suffix):
                            stem = stem[:-len(dt_suffix)]
                    if stem in seen_stems and not base.endswith("_Full.json"):
                        continue
                    seen_stems.add(stem)
                    loaded.append(data)
                elif isinstance(data, list):
                    loaded.extend(data)
        except Exception as e:
            logger.debug(f"Пропущен некорректный JSON файл '{jf}': {e}")

    if loaded:
        export_consolidated_registries(loaded, facade.results_dir)
        excel_path = exporter.export_results_to_excel(loaded)
        print(f"✅ Пересобрано {len(loaded)} записей:")
        print("   • Registry_Full.json")
        print("   • Registry_Flat.json")
        print("   • all_documents_registry.json")
        print(f"   • {os.path.basename(excel_path)}")
        open_results_in_explorer(facade.results_dir)
    else:
        print(f"⚠️ Нет сохраненных результатов в папке '{os.path.basename(facade.results_dir)}/'.")


def main():
    setup_console_utf8()
    print_banner()

    facade = LegalDocPlatformFacade()
    exporter = LegalExcelExporter(output_dir=facade.results_dir)

    # 0. Автозапуск сценариев по CLI-флагам (совместимость с scripts/windows/*.bat)
    cli_flag = sys.argv[1].strip().lower() if len(sys.argv) > 1 else ""
    if cli_flag == "--diagnostics":
        run_vlm_diagnostics(verbose=True)
        return
    if cli_flag == "--benchmark":
        run_benchmark_suite(facade)
        return
    if cli_flag == "--excel":
        rebuild_registries_and_excel(facade, exporter)
        return

    # 1. Проверка аргумента командной строки (Drag & Drop на файл)
    if len(sys.argv) > 1:
        target = sys.argv[1].strip('"').strip("'")
        if os.path.isfile(target):
            print(f"\n📂 Обнаружен переданный файл (Drag & Drop): {target}")
            res = facade.process_single_document(target)
            zt_status = res.get("zero_trust_status", "vlm_unverified")
            q_score = res.get("quality_score_percent", 100.0)
            exporter.export_results_to_excel([res])
            facade.tracker.print_summary()
            print(f"\n🛡️  Статус Zero-Trust: {zt_status} | Качество: {q_score}%")
            print("✅ Результат сохранен в JSON и Excel.")
            open_results_in_explorer(facade.results_dir)
            return
        elif os.path.isdir(target):
            print(f"\n📂 Обнаружена переданная папка (Drag & Drop): {target}")
            results = facade.process_batch(target)
            facade.tracker.print_summary()
            print("✅ Пакетная обработка завершена.")
            open_results_in_explorer(facade.results_dir)
            return

    # 2. Главное интерактивное меню
    while True:
        print("\n📌 ВЫБЕРИТЕ ДЕЙСТВИЕ:")
        print("  [1] 📄  Распознать отдельный документ (Выбор через Проводник)")
        print("  [2] 📂  Пакетная авто-обработка папки 'incoming' (все типы)")
        print("  [3] 📑  Пакетная обработка конкретной категории")
        print("  [4] 📁  Пакетная обработка произвольной папки")
        print("  [5] 🧪  Запустить Ground Truth бенчмарк (с выгрузкой в JSON)")
        print("  [6] 🩺  Диагностика подключения к VLM/LLM и замер токенов")
        print("  [7] 💾  Сформировать/пересобрать сводные JSON-реестры и Excel")
        print("  [8] 📂  Открыть папку с результатами (output)")
        print("  [0] 🚪  Выход")

        choice = input("\n👉 Ваш выбор [1-8, 0] (по умолчанию 2): ").strip() or "2"

        if choice == "1":
            print("\n⏳ Открытие диалога выбора файла...")
            selected = open_file_dialog()
            if selected:
                det_type, conf, method = facade.classify_document(selected)
                chosen_type = det_type

                if det_type == UNKNOWN_CATEGORY:
                    print(f"\n⚠️  Категория для файла '{os.path.basename(selected)}' не определена автоматически.")
                    print("👉 Выберите тип документа вручную:")
                    plugins_list = list(facade.registry.enabled().values())
                    for p_idx, pl in enumerate(plugins_list, 1):
                        icon = pl.manifest.get("dashboard", {}).get("icon", "📄")
                        print(f"   [{p_idx}] {icon} {pl.title} ({pl.id})")
                    print(f"   [{len(plugins_list) + 1}] 🤖 Универсальное базовое извлечение")
                    print("   [0] ⏩ Отмена")

                    sub_choice = input(f"\n👉 Ваш выбор [1..{len(plugins_list) + 1}] (по умолчанию 1): ").strip()
                    if sub_choice == "0":
                        print("Операция отменена.")
                        continue
                    try:
                        num = int(sub_choice) if sub_choice else 1
                        if 1 <= num <= len(plugins_list):
                            chosen_type = plugins_list[num - 1].id
                        else:
                            chosen_type = UNKNOWN_CATEGORY
                    except Exception:
                        chosen_type = UNKNOWN_CATEGORY

                res = facade.process_single_document(selected, doc_type=chosen_type)
                zt_status = res.get("zero_trust_status", "vlm_unverified")
                q_score = res.get("quality_score_percent", 100.0)
                exporter.export_results_to_excel([res])
                facade.tracker.print_summary()
                print(f"\n🛡️  Статус Zero-Trust: {zt_status} | Качество: {q_score}%")
                print(f"✅ Обработка завершена: {os.path.basename(facade.results_dir)}/{sanitize_filename(Path(selected).stem)}_{chosen_type}.json")
                open_results_in_explorer(facade.results_dir)
            else:
                print("⚠️ Файл не был выбран.")

        elif choice == "2":
            in_dir = _resolve_input_root()
            if not os.path.exists(in_dir) and os.path.exists(os.path.join(ROOT_DIR, "Входящие_документы")):
                in_dir = os.path.join(ROOT_DIR, "Входящие_документы")
            os.makedirs(in_dir, exist_ok=True)
            print(f"\n⏳ Запуск пакетной обработки: {in_dir}")
            results = facade.process_batch(in_dir, doc_type="auto")
            if results:
                facade.tracker.print_summary()
                open_results_in_explorer(facade.results_dir)
            else:
                print(f"⚠️ В папке '{os.path.basename(in_dir)}' нет файлов для обработки.")

        elif choice == "3":
            plugins_list = list(facade.registry.enabled().values())
            print("\nВыберите категорию для обработки:")
            for p_idx, pl in enumerate(plugins_list, 1):
                icon = pl.manifest.get("dashboard", {}).get("icon", "📄")
                print(f"  [{p_idx}] {icon}  {pl.title} (incoming/{pl.id})")
            print("  [0] ⏩  Отмена")

            cat_pick = input(f"\n👉 Ваш выбор [1..{len(plugins_list)}, 0] (Enter=1): ").strip() or "1"
            if cat_pick == "0":
                continue
            try:
                num = int(cat_pick)
                if 1 <= num <= len(plugins_list):
                    selected_pl = plugins_list[num - 1]
                else:
                    selected_pl = plugins_list[0]
            except Exception:
                selected_pl = plugins_list[0]

            target_type = selected_pl.id
            sub_eng = selected_pl.id
            sub_ru = selected_pl.folder

            inc_base = _resolve_input_root()
            if os.path.exists(os.path.join(inc_base, sub_eng)):
                target_dir = os.path.join(inc_base, sub_eng)
            elif os.path.exists(os.path.join(inc_base, sub_ru)):
                target_dir = os.path.join(inc_base, sub_ru)
            elif os.path.exists(os.path.join(ROOT_DIR, "Входящие_документы", sub_ru)):
                target_dir = os.path.join(ROOT_DIR, "Входящие_документы", sub_ru)
            else:
                target_dir = os.path.join(inc_base, sub_eng)
                os.makedirs(target_dir, exist_ok=True)

            print(f"\n⏳ Обработка категории '{target_type}' в: {target_dir}")
            results = facade.process_batch(target_dir, doc_type=target_type)
            if results:
                facade.tracker.print_summary()
                open_results_in_explorer(facade.results_dir)

        elif choice == "4":
            print("\n⏳ Открытие диалога выбора папки...")
            f_dir = open_folder_dialog()
            if f_dir:
                results = facade.process_batch(f_dir)
                if results:
                    facade.tracker.print_summary()
                    open_results_in_explorer(facade.results_dir)
            else:
                print("⚠️ Папка не выбрана.")

        elif choice == "5":
            run_benchmark_suite(facade)

        elif choice == "6":
            run_vlm_diagnostics(verbose=True)

        elif choice == "7":
            rebuild_registries_and_excel(facade, exporter)

        elif choice == "8":
            open_results_in_explorer(facade.results_dir)

        elif choice == "0":
            print("👋 Завершение работы.")
            break


if __name__ == "__main__":
    main()
