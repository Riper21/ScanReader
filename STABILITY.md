# ScanReader Stability Policy

[English](STABILITY.md) | [Русский](STABILITY_RU.md)

The commitments below take effect with version **1.0.0**. Versioning
follows [SemVer](https://semver.org/). Until `2.0.0` changes to the public
contracts are **additive only**: new things are added, existing things are
not removed or renamed.

Anything not listed below is internal implementation (`core/`, `verifier/`,
`file_processor/`, private methods) and may change in any minor version.

## 1. Python API

Class `scan_reader.facade.LegalDocPlatformFacade` — 7 methods:

| Method | Signature |
|---|---|
| `classify_via_vlm` | `(file_path: str) -> Tuple[str, float, str, Dict[str, Any]]` |
| `classify_document` | `(file_path: str, use_vlm_fallback: bool = True) -> Tuple[str, float, str]` |
| `extract_document_data` | `(file_path: str, doc_type: Optional[str] = None, max_pages: Optional[int] = None) -> Dict[str, Any]` |
| `validate_document` | `(data: Dict[str, Any], doc_type: str) -> Dict[str, Any]` |
| `benchmark_against_ground_truth` | `(extracted: Dict, ground_truth: Dict, doc_type: str) -> Dict[str, Any]` |
| `process_single_document` | `(file_path: str, doc_type: Optional[str] = None, update_registries: bool = True) -> Dict[str, Any]` |
| `process_batch` | `(folder_or_files: Union[str, List[str]], doc_type: Optional[str] = None, organize_subfolders: bool = False, calculate_metrics: bool = True) -> List[Dict[str, Any]]` |

New parameters are added with a default value only.

## 2. CLI

Commands `scan-reader` / `scanreader`: `run`, `classify`, `verify`, `export`,
`benchmark`, `doctor`, `mcp`; globals `--version`, `-v/--verbose`; the
legacy mode "a path without a subcommand is processed as `run`".

Exit codes and their semantics:

| Code | Constant | Meaning |
|---|---|---|
| `0` | `EXIT_OK` | Success |
| `1` | `EXIT_ERROR` | Processing failure |
| `2` | `EXIT_USAGE` | Argument error |
| `3` | `EXIT_DISCREPANCY` | Discrepancy / gate not executed — manual review required |
| `4` | `EXIT_FALLBACK` | Heuristic result — must not be treated as read from the page |

Subcommand flags (including the value sets of `-f/--format`) are frozen.
Extension happens via new optional flags only.

## 3. JSON-RPC stdio server

5 tools: `scan_document`, `classify_document`, `verify_legal_data`,
`run_benchmark`, `export_results` — names, parameter schemas and semantics
are frozen. Every response carries a `protocol_version` field (string,
`"1.0"` since release 1.0.0). New tools are added without touching the
existing ones.

## 4. JSON artifacts

`{stem}_Full.json`, `{stem}_Flat.json` (PascalCase columns from the
plugin's `flat_columns.json`), `Registry_Full.json` (record-level keys
`file_name`, `doc_type`, `status`, `data`, `verification`, `metrics`), the
Excel block, `run_metrics_summary.{json,md}` — the top-level key structure
is frozen; new fields are additive.

The verification status vocabulary (8 values): `zero_trust_verified`,
`partially_verified`, `gate_not_executed`, `vlm_unverified`,
`heuristic_fallback`, `discrepancy_detected`, `ocr_low_confidence`,
`rejected_unsupported`; document statuses in the registry: `COMPLETED` /
`FAILED` / `NEEDS_REVIEW`. Adding new values is additive; removal happens only in `2.0.0`.

## 5. Environment variables

Names and value semantics of `OPENAI_API_KEY`, `OPENAI_BASE_URL`,
`AI_MODEL_NAME`, `VLM_HTTP_TIMEOUT`, `VLM_SLOT_TIMEOUT`,
`VLM_ROUTER_CONFIDENCE_MIN`, `VLM_RPM_LIMIT`, `VLM_MAX_CONCURRENCY`,
`ENABLE_RATE_LIMITER`, `ENABLE_CACHE`, `SCANREADER_CACHE_DIR`,
`SCANREADER_MAX_CACHE_ENTRIES`, `SCANREADER_CACHE_FLUSH_EVERY`,
`SCANREADER_CACHE_FLUSH_SECONDS`, `SCANREADER_MAX_PAGES`,
`SCANREADER_VLM_ATTEMPTS`, `SCANREADER_OUTPUT_DIR`,
`SCANREADER_INCOMING_DIR`, `SCANREADER_GROUND_TRUTH_DIR`,
`SCANREADER_ALLOWED_DIRS`, `SCANREADER_HOME`, `SCANREADER_OCR_*`,
`LOG_LEVEL`, `NO_COLOR` — frozen. `VLM_TIMEOUT` is a deprecated alias of
`VLM_HTTP_TIMEOUT` (removed in `2.0.0`).

## 6. Plugin contract `doc_types/`

7 required files: `manifest.json` (keys `id`, `title`, `folder`,
`registry_base_name`, `js_filename`, `js_var`, `gt_file`, `schema_class`),
`schema.py`, `prompt.md`, `classifier_rules.json`, `flat_columns.json`,
`benchmark.json`, `autonomous.json`; optional `verification.json`,
`flat_1c.json` (manifest key `flat_1c_builder`). The contract composition
is frozen: third-party plugins that satisfy it keep working across the
`1.x` series.

## Deprecation policy

- Removing or renaming any contract element — only in `2.0.0`.
- Deprecating an element in a minor version is allowed: the working path
  stays with a `DeprecationWarning`, and the `CHANGELOG` gets a
  "Deprecated" section. At least one minor version between deprecation and
  removal.
- Defaults change only when the old behavior was a documented defect
  (example: splitting `VLM_TIMEOUT` into `VLM_HTTP_TIMEOUT` /
  `VLM_SLOT_TIMEOUT` in 0.9.6).

## Python

`requires-python >= 3.9` — until `2.0.0`. Raising the minimum version is a
major change.

## Dependency floors (until 2.0.0)

`pydantic>=2.6,<3.0`, `Pillow>=10.0`, `pymupdf>=1.24.3`, `python-docx>=1.1.0`,
`openpyxl>=3.1`, `python-dotenv>=1.0`. Raising a floor is an additive change
(newer versions install on top) but must be declared in the CHANGELOG;
removing the upper bound (`<3.0`) or growing the core dependency set —
minor version only.

## Process

Contract changes are recorded in [CHANGELOG.md](CHANGELOG.md) (Keep a
Changelog format); releases are published via `v*` tags and shipped to
PyPI. Contract guard tests in `tests/` fail on undocumented changes to the
public surface.
