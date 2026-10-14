# Политика стабильности ScanReader

[English](STABILITY.md) | [Русский](STABILITY_RU.md)

Обязательства действуют, начиная с версии **1.0.0**. Версионирование —
[SemVer](https://semver.org/lang/ru/). До `2.0.0` изменения публичных
контрактов — **только additive**: новое добавляется, существующее не
удаляется и не переименовывается.

Всё, что не перечислено ниже, — внутренняя реализация (`core/`,
`verifier/`, `file_processor/`, приватные методы) и может меняться в любой
минорной версии.

## 1. Python API

Класс `scan_reader.facade.LegalDocPlatformFacade` — 7 методов:

| Метод | Сигнатура |
|---|---|
| `classify_via_vlm` | `(file_path: str) -> Tuple[str, float, str, Dict[str, Any]]` |
| `classify_document` | `(file_path: str, use_vlm_fallback: bool = True) -> Tuple[str, float, str]` |
| `extract_document_data` | `(file_path: str, doc_type: Optional[str] = None, max_pages: Optional[int] = None) -> Dict[str, Any]` |
| `validate_document` | `(data: Dict[str, Any], doc_type: str) -> Dict[str, Any]` |
| `benchmark_against_ground_truth` | `(extracted: Dict, ground_truth: Dict, doc_type: str) -> Dict[str, Any]` |
| `process_single_document` | `(file_path: str, doc_type: Optional[str] = None, update_registries: bool = True) -> Dict[str, Any]` |
| `process_batch` | `(folder_or_files: Union[str, List[str]], doc_type: Optional[str] = None, organize_subfolders: bool = False, calculate_metrics: bool = True) -> List[Dict[str, Any]]` |

Добавление новых параметров — только с значением по умолчанию.

## 2. CLI

Команды `scan-reader` / `scanreader`: `run`, `classify`, `verify`, `export`,
`benchmark`, `doctor`, `mcp`; глобальные `--version`, `-v/--verbose`;
легаси-режим «путь без подкоманды обрабатывается как `run`».

Коды возврата и их семантика:

| Код | Константа | Значение |
|---|---|---|
| `0` | `EXIT_OK` | Успех |
| `1` | `EXIT_ERROR` | Сбой обработки |
| `2` | `EXIT_USAGE` | Ошибка аргументов |
| `3` | `EXIT_DISCREPANCY` | Расхождение / невыполненный гейт — нужна ручная проверка |
| `4` | `EXIT_FALLBACK` | Эвристический результат — нельзя считать прочитанным с листа |

Флаги подкоманд (включая наборы значений `-f/--format`) заморожены.
Расширение — только новыми необязательными флагами.

## 3. JSON-RPC stdio-сервер

5 tools: `scan_document`, `classify_document`, `verify_legal_data`,
`run_benchmark`, `export_results` — имена, схемы параметров и семантика
заморожены. Каждый ответ несёт поле `protocol_version` (строка, `"1.0"` с
релиза 1.0.0). Новые tools добавляются без изменения существующих.

## 4. JSON-артефакты

`{stem}_Full.json`, `{stem}_Flat.json` (PascalCase-колонки из
`flat_columns.json` плагина), `Registry_Full.json` (ключи `file_name`,
`doc_type`, `status`, `data`, `verification`, `metrics` на уровне записи),
Excel-блок, `run_metrics_summary.{json,md}` — структура ключей верхнего
уровня заморожена; новые поля additive.

Словарь статусов верификации (8 значений): `zero_trust_verified`,
`partially_verified`, `gate_not_executed`, `vlm_unverified`,
`heuristic_fallback`, `discrepancy_detected`, `ocr_low_confidence`,
`rejected_unsupported`; статусы документов в реестре: `COMPLETED` / `FAILED`
/ `NEEDS_REVIEW`.
Добавление новых значений — additive, удаление — только в `2.0.0`.

## 5. Переменные окружения

Имена и семантика значений `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`AI_MODEL_NAME`, `VLM_HTTP_TIMEOUT`, `VLM_SLOT_TIMEOUT`,
`VLM_ROUTER_CONFIDENCE_MIN`, `VLM_RPM_LIMIT`, `VLM_MAX_CONCURRENCY`,
`ENABLE_RATE_LIMITER`, `ENABLE_CACHE`, `SCANREADER_CACHE_DIR`,
`SCANREADER_MAX_CACHE_ENTRIES`, `SCANREADER_CACHE_FLUSH_EVERY`,
`SCANREADER_CACHE_FLUSH_SECONDS`, `SCANREADER_MAX_PAGES`,
`SCANREADER_VLM_ATTEMPTS`, `SCANREADER_OUTPUT_DIR`,
`SCANREADER_INCOMING_DIR`, `SCANREADER_GROUND_TRUTH_DIR`,
`SCANREADER_ALLOWED_DIRS`, `SCANREADER_HOME`, `SCANREADER_OCR_*`,
`LOG_LEVEL`, `NO_COLOR` — заморожены. `VLM_TIMEOUT` — deprecated-алиас
`VLM_HTTP_TIMEOUT` (удаляется в `2.0.0`).

## 6. Контракт плагина `doc_types/`

7 обязательных файлов: `manifest.json` (ключи `id`, `title`, `folder`,
`registry_base_name`, `js_filename`, `js_var`, `gt_file`, `schema_class`),
`schema.py`, `prompt.md`, `classifier_rules.json`, `flat_columns.json`,
`benchmark.json`, `autonomous.json`; опциональные `verification.json`,
`flat_1c.json` (ключ манифеста `flat_1c_builder`). Состав контракта
заморожен: сторонние плагины, соблюдающие его, работают во всех версиях
`1.x`.

## Политика deprecation

- Удаление или переименование любого элемента контрактов — только в
  `2.0.0`.
- Свернуть (deprecate) элемент можно в минорной версии: в коде остаётся
  работающий путь с `DeprecationWarning`, в `CHANGELOG` — раздел
  «Deprecated». Минимум одна минорная версия между deprecation и удалением.
- Меняются дефолты — только если старое поведение было документированной
  ошибкой (пример: разделение `VLM_TIMEOUT` на `VLM_HTTP_TIMEOUT` /
  `VLM_SLOT_TIMEOUT` в 0.9.6).

## Python

`requires-python >= 3.9` — до `2.0.0`. Подъём минимальной версии —
major-изменение.

## Полы зависимостей (до 2.0.0)

`pydantic>=2.6,<3.0`, `Pillow>=10.0`, `pymupdf>=1.24.3`, `python-docx>=1.1.0`,
`openpyxl>=3.1`, `python-dotenv>=1.0`. Подъём пола — additive-изменение
(новые версии ставятся поверх), но объявляется в CHANGELOG; снятие верхней
границы (`<3.0`) или расширение набора зависимостей ядра — только через
минорную версию.

## Процесс

Изменения контрактов фиксируются в [CHANGELOG_RU.md](CHANGELOG_RU.md)
(формат Keep a Changelog), релизы публикуются тегами `v*` и уходят на PyPI.
Стражи контрактных тестов в `tests/` падают при незадокументированном
изменении публичной поверхности.
