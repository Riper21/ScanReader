# -*- coding: utf-8 -*-
"""
Реестр типов документов (плагинная архитектура).

Каждый поддерживаемый тип документа описывается самодостаточным пакетом в папке
`doc_types/<type_id>/`:

    manifest.json          — метаданные (id, название, папки, имена файлов, порядок)
    schema.py              — Pydantic-схема документа (класс указан в manifest.schema_class)
    prompt.md              — системный промпт для VLM-экстракции
    classifier_rules.json  — правила эвристической классификации (маркеры имени/пути/текста)
    flat_columns.json      — маппинг «путь в модели → колонка таблицы»
    benchmark.json         — конфиг бенчмарка против ground truth (метрика + вес поля)
    autonomous.json        — DSL автономных Guardrails-проверок
    verification.json      — декларация верификации: реквизиты, сверка денег,
                              хронология, поля кросс-модального гейта

Ядро системы (конвейер, классификатор, верификатор, экспорт, метрики) НЕ
содержит жестко зашитых категорий: оно читает реестр через функции этого модуля.
Добавление нового типа = создание новой папки плагина (см. new_doctype.py).
Ни verifier/auditor.py, ни core/ не знают ни одного имени поля конкретного типа
документа — всё приходит из конфигов плагина.
"""

import os
import sys
import json
import importlib.util
from typing import Dict, Any, List, Optional, Type

from .core.utils import get_logger

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DOC_TYPES_DIR = os.path.join(SCRIPT_DIR, "doc_types")

logger = get_logger("type_registry")

UNKNOWN_CATEGORY = "unknown"
UNKNOWN_FOLDER_NAME = "_Требует_ручной_проверки"

REQUIRED_MANIFEST_KEYS = ("id", "title", "folder", "registry_base_name", "js_filename", "js_var", "gt_file", "schema_class")

# M-12: 7 обязательных файлов каждого плагина (Правило 3 AGENTS.md)
REQUIRED_PLUGIN_FILES = (
    "manifest.json",
    "schema.py",
    "prompt.md",
    "classifier_rules.json",
    "flat_columns.json",
    "benchmark.json",
    "autonomous.json",
)

# Фаза 6: verification.json — восьмой файл контракта. Необязателен: плагин без
# него проходит только общие проверки, и это позволяет добавлять новый тип
# документа простым созданием папки.
OPTIONAL_PLUGIN_FILES = ("verification.json",)


class PluginSpec:
    """Полностью загруженное описание одного типа документа."""

    def __init__(self, folder_path: str):
        self.folder_path = folder_path
        self.folder_name = os.path.basename(folder_path)

        # M-12: проверка 7 обязательных файлов плагина до загрузки
        missing_files = [f for f in REQUIRED_PLUGIN_FILES if not os.path.exists(os.path.join(folder_path, f))]
        if missing_files:
            raise ValueError(
                f"Плагин '{self.folder_name}' не содержит обязательных файлов: {missing_files}"
            )

        manifest_path = os.path.join(folder_path, "manifest.json")
        with open(manifest_path, "r", encoding="utf-8") as f:
            self.manifest: Dict[str, Any] = json.load(f)

        missing = [k for k in REQUIRED_MANIFEST_KEYS if k not in self.manifest]
        if missing:
            raise ValueError(f"manifest.json плагина '{self.folder_name}' не содержит ключей: {missing}")

        self.id: str = self.manifest["id"]
        if self.id != self.folder_name:
            raise ValueError(f"manifest.id '{self.id}' не совпадает с именем папки '{self.folder_name}'")

        self.title: str = self.manifest["title"]
        self.short_title: str = self.manifest.get("short_title", self.title)
        self.version: str = self.manifest.get("version", "1.0.0")
        self.enabled: bool = bool(self.manifest.get("enabled", True))
        self.order: int = int(self.manifest.get("order", 100))
        self.tie_priority: int = int(self.manifest.get("tie_priority", 0))
        self.folder: str = self.manifest["folder"]
        self.registry_base_name: str = self.manifest["registry_base_name"]
        self.js_filename: str = self.manifest["js_filename"]
        self.js_var: str = self.manifest["js_var"]
        self.gt_file: str = self.manifest["gt_file"]
        self.schema_class_name: str = self.manifest["schema_class"]

        self.classifier: Dict[str, Any] = self.manifest.get("classifier", {})
        self.dashboard: Dict[str, Any] = self.manifest.get("dashboard", {})

        # --- Схема ---
        self.schema_cls: Type = self._load_schema()

        # --- Промпт ---
        prompt_path = os.path.join(folder_path, "prompt.md")
        with open(prompt_path, "r", encoding="utf-8") as f:
            self.prompt_text: str = f.read().strip()

        # --- Конфиги ---
        self.classifier_rules: Dict[str, Any] = self._load_json("classifier_rules.json", default={})
        self.flat_columns: List[Dict[str, Any]] = self._load_json("flat_columns.json", default=[])
        self.benchmark_config: Dict[str, Any] = self._load_json("benchmark.json", default={"fields": []})
        self.autonomous_config: Dict[str, Any] = self._load_json("autonomous.json", default={"fields": []})

        # --- Декларация верификации (Фаза 6, Правило 3) ---
        # Ядро верификатора не знает ни одного имени поля этого типа документа:
        # они приходят отсюда.
        from .verifier.spec import load_verification_spec

        self.verification_spec = load_verification_spec(folder_path)
        spec_problems = self.verification_spec.validate(self.folder_name)
        if spec_problems:
            raise ValueError(
                f"verification.json плагина '{self.folder_name}' некорректен: {'; '.join(spec_problems)}"
            )

        # Нормализация и валидация benchmark.json (C-03)
        # C-06: тип поля обязан быть из известного словаряря. Раньше проверялся
        # только непустой path, поэтому «number» проходил загрузку, а оценщик его
        # не понимал и сравнивал суммы как строки: ошибка в 10 раз давала 98.32%.
        from .core.metrics_evaluator import KNOWN_FIELD_TYPES

        bench_fields = self.benchmark_config.get("fields", [])
        if isinstance(bench_fields, list):
            for idx, item in enumerate(bench_fields):
                if isinstance(item, dict):
                    if "path" not in item and "name" in item:
                        item["path"] = item["name"]
                    if not item.get("path"):
                        raise ValueError(
                            f"Элемент #{idx} в benchmark.json плагина '{self.folder_name}' имеет пустой path"
                        )
                    declared = str(item.get("type", "fuzzy")).strip().lower()
                    if declared not in KNOWN_FIELD_TYPES:
                        raise ValueError(
                            f"Элемент #{idx} в benchmark.json плагина '{self.folder_name}': "
                            f"неизвестный тип поля '{declared}' для '{item['path']}'. "
                            f"Допустимо: {', '.join(sorted(KNOWN_FIELD_TYPES))}"
                        )
                    try:
                        float(item.get("weight", 1.0))
                    except (TypeError, ValueError):
                        raise ValueError(
                            f"Элемент #{idx} в benchmark.json плагина '{self.folder_name}': "
                            f"нечисловой weight {item.get('weight')!r} для '{item['path']}'"
                        ) from None

        # Нормализация и валидация flat_columns.json (C-04)
        if isinstance(self.flat_columns, list):
            for idx, col in enumerate(self.flat_columns):
                if isinstance(col, dict):
                    if "path" not in col and "field" in col:
                        col["path"] = col["field"]
                    if "label" not in col and "header" in col:
                        col["label"] = col["header"]
                    if "kind" not in col and "type" in col:
                        col["kind"] = col["type"]
                    k = str(col.get("kind", "text")).lower()
                    if k in ("str", "string"):
                        col["kind"] = "text"
                    elif k in ("currency", "float", "int"):
                        col["kind"] = "number"
                    elif k in ("bool",):
                        col["kind"] = "boolean"
                    if not col.get("path") or not col.get("label"):
                        raise ValueError(
                            f"Колонка #{idx} в flat_columns.json плагина '{self.folder_name}' должна содержать path и label"
                        )

        # Валидация структуры autonomous.json (защита от B-02)
        auto_fields = self.autonomous_config.get("fields", [])
        if isinstance(auto_fields, list):
            for idx, item in enumerate(auto_fields):
                if not isinstance(item, dict):
                    raise ValueError(
                        f"Элемент #{idx} в autonomous.json плагина '{self.folder_name}' должен быть dict, получено: {type(item).__name__}"
                    )
                if "field" not in item or "rule" not in item:
                    raise ValueError(
                        f"Элемент #{idx} в autonomous.json плагина '{self.folder_name}' не содержит обязательных ключей 'field' или 'rule'"
                    )

        # --- Опциональные примечания для MD-отчета эталонного реестра ---
        notes_path = os.path.join(folder_path, "report_notes.md")
        self.report_notes: Optional[str] = None
        if os.path.exists(notes_path):
            with open(notes_path, "r", encoding="utf-8") as f:
                self.report_notes = f.read()

    def _load_json(self, filename: str, default: Any) -> Any:
        path = os.path.join(self.folder_path, filename)
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _load_schema(self) -> Type:
        """
        Загружает Pydantic-модель плагина из schema.py.

        Фаза 7.9: модуль НЕ регистрируется в sys.modules. Раньше он оставался
        там навсегда, и каждый get_registry(force_reload=True) добавлял новый
        объект на каждый плагин, то есть память росла пропорционально числу
        перезагрузок. Регистрация нужна была для разрешения внутренних ссылок
        модуля, но это делает сам спецификатор загрузчика.
        """
        schema_path = os.path.join(self.folder_path, "schema.py")
        module_name = f"doc_types_{self.id}_schema"
        spec = importlib.util.spec_from_file_location(module_name, schema_path)
        if spec is None or spec.loader is None:
            raise ValueError(f"Не удалось загрузить схему плагина '{self.folder_name}' из {schema_path}")
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception:
            sys.modules.pop(module_name, None)
            raise
        cls = getattr(module, self.schema_class_name, None)
        if cls is None:
            raise ValueError(f"В {schema_path} нет класса '{self.schema_class_name}'")
        return cls

    @property
    def registry_json_name(self) -> str:
        return f"{self.registry_base_name}.json"

    def dashboard_meta(self) -> Dict[str, Any]:
        """Метаданные для универсального веб-дашборда (попадают в *_META js и doc_types_meta.js)."""
        meta = {
            "id": self.id,
            "title": self.title,
            "short_title": self.short_title,
            "js_var": self.js_var,
            "registry_base_name": self.registry_base_name,
            "icon": self.dashboard.get("icon", "📄"),
            "color": self.dashboard.get("color", "blue"),
            "badge": self.dashboard.get("badge", self.short_title),
            "columns": [
                {"label": c.get("label", ""), "kind": c.get("kind", "text")}
                for c in self.flat_columns
            ],
            "paths": self.dashboard.get("paths", {}),
        }
        return meta


class _Registry:
    """Ленивый синглтон-реестр всех плагинов из doc_types/."""

    def __init__(self):
        self.plugins: Dict[str, PluginSpec] = {}
        self.load_errors: Dict[str, str] = {}
        self._enabled_cache: Optional[Dict[str, PluginSpec]] = None
        self.reload()

    def reload(self) -> None:
        self.plugins.clear()
        self.load_errors.clear()
        self._enabled_cache = None
        from .verifier.spec import clear_spec_cache

        clear_spec_cache()
        if not os.path.isdir(DOC_TYPES_DIR):
            return
        for entry in sorted(os.listdir(DOC_TYPES_DIR)):
            plugin_dir = os.path.join(DOC_TYPES_DIR, entry)
            if entry.startswith(("_", ".")) or not os.path.isdir(plugin_dir):
                continue  # _template и служебные папки игнорируются
            if not os.path.exists(os.path.join(plugin_dir, "manifest.json")):
                logger.warning(f"Плагин '{entry}' пропущен: отсутствует manifest.json")
                continue
            try:
                spec = PluginSpec(plugin_dir)
                self.plugins[spec.id] = spec
            except Exception as e:
                self.load_errors[entry] = str(e)
                logger.warning(f"Плагин '{entry}' пропущен: {e}")

    def enabled(self) -> Dict[str, PluginSpec]:
        """Включенные плагины, отсортированные по полю order (кэшируется, M-10)."""
        if self._enabled_cache is None:
            self._enabled_cache = {
                pid: p
                for pid, p in sorted(self.plugins.items(), key=lambda kv: (kv[1].order, kv[0]))
                if p.enabled
            }
        return self._enabled_cache

    def enabled_by_tie_priority(self) -> List[PluginSpec]:
        """Плагины в порядке приоритета при классификации (более специфичные первыми)."""
        return sorted(
            (p for p in self.enabled().values()),
            key=lambda p: (-p.tie_priority, p.order),
        )

    def get(self, type_id: str) -> Optional[PluginSpec]:
        return self.enabled().get(type_id)

    def ids(self) -> List[str]:
        return list(self.enabled().keys())


DocumentTypeRegistry = _Registry

_REGISTRY_SINGLETON: Optional[_Registry] = None


def get_registry(force_reload: bool = False) -> _Registry:
    global _REGISTRY_SINGLETON
    if _REGISTRY_SINGLETON is None or force_reload:
        _REGISTRY_SINGLETON = _Registry()
        # M-11: предупреждения реестра идут через логгер в stderr, stdout чист для 1С
        for folder, err in _REGISTRY_SINGLETON.load_errors.items():
            logger.warning(f"Плагин '{folder}' не загружен: {err}")
    return _REGISTRY_SINGLETON


def get_enabled_plugins() -> Dict[str, PluginSpec]:
    return get_registry().enabled()


def get_plugin(type_id: str) -> PluginSpec:
    plugin = get_registry().get(type_id)
    if plugin is None:
        available = ", ".join(get_enabled_plugins().keys()) or "<нет>"
        raise KeyError(f"Неизвестный тип документа '{type_id}'. Доступные: {available}")
    return plugin


def build_document_schemas() -> Dict[str, Type]:
    """Совместимость: {id: Pydantic-класс} по всем включенным плагинам."""
    return {pid: p.schema_cls for pid, p in get_enabled_plugins().items()}


def build_system_prompts() -> Dict[str, str]:
    return {pid: p.prompt_text for pid, p in get_enabled_plugins().items()}


def build_category_names() -> Dict[str, str]:
    names = {pid: p.short_title for pid, p in get_enabled_plugins().items()}
    names[UNKNOWN_CATEGORY] = "Неопределенная категория"
    return names


def build_category_folders() -> Dict[str, str]:
    folders = {pid: p.folder for pid, p in get_enabled_plugins().items()}
    folders[UNKNOWN_CATEGORY] = UNKNOWN_FOLDER_NAME
    return folders


if __name__ == "__main__":
    reg = get_registry()
    print(f"Загружено плагинов: {len(reg.plugins)} (включено: {len(reg.enabled())})")
    for pid, p in reg.enabled().items():
        print(f"  [{pid}] {p.title} | схема={p.schema_cls.__name__} | колонок={len(p.flat_columns)} "
              f"| bench полей={len(p.benchmark_config.get('fields', []))} | auto проверок={len(p.autonomous_config.get('fields', []))}")
