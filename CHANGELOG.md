[English](CHANGELOG.md) | [Русский](CHANGELOG_RU.md)

# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased] — Recognition guards (S-10…S-13): закрытие слепых зон распознавания

### Added
- **S-13 OCR cross-check for scans:** images without a text layer now get a second VLM pass
  ("exact transcription") used as the reference in the cross-modal gate — garbled names, УИН,
  ИП/ИЛ numbers no longer pass silently. Controlled by `SCANREADER_OCR_CROSSCHECK` (default on).
  Each unconfirmed requisite applies a −5 pp quality penalty (capped at 15).

### Fixed
- **S-10:** Fractional withholding rates (`1/4`, `1/3`, `1/2`, `2/3` of income) now participate in
  the 229-FZ statutory limits check — alimony-deduction documents were previously exempt from control.
  IP numbers (`10701/16/3001-ИП`) are explicitly excluded from fraction parsing.
- **S-11:** Slash dates (`21/04/2016`) are parsed; garbage dates in any date field produce an
  `UNPARSEABLE_DATE` warning instead of silently disabling chronology checks.
- **S-12:** Cross-checks added: УИН must embed the РОСП code (`UIN_ROSP_MISMATCH`),
  ОКТМО length (8/11), КПП length (9), бланк ИЛ number length (8–9 digits),
  case numbers without separators score 60 instead of 100 (glue suspicion).

## [Unreleased] — Scan quality remediation (S-1…S-8, кейс 745×1024 @ 96 DPI)

Root cause analysis of a real 96 DPI scan with garbled requisites (lost `/` in IP numbers,
misread digits, false INVALID_BANK_ACCOUNT on a Bank of Russia account, Excel showing 100%).

### Fixed
- **S-1:** Low-resolution scans (< 1500 px) are upscaled 2× (LANCZOS) before VLM inference —
  requisites digits become readable instead of guessed.
- **S-2:** Status coherence: a Zero-Trust error now forces `validation.passed = False`
  and `quality_status = needs_attention` (no more `discrepancy_detected` + `excellent` side by side);
  `OCR_LOW_CONFIDENCE` (< 150 DPI) applies a 10-point quality penalty and forbids "excellent".
- **S-3:** IP-number format restoration: `NNNNN/NN/NNNNN-ИП` is deterministically rebuilt from
  slash-less VLM output (digits preserved) in salary_deductions / enforcement_orders schemas.
- **S-4:** Excel summary "Средний балл качества" now averages `quality_score_percent`
  (was Guardrails score → misleading 100%); detail sheets get a "Качество (%)" column.
- **S-5:** Treasury BIKs (`01xxxxxxx`, УФК) are valid — the most common FSSP payee no longer
  triggers a false INVALID_BIK.
- **S-6:** Bank of Russia (ГРКЦ) accounts that fail the standard 565-П key check produce a
  `BANK_ACCOUNT_UNVERIFIED` warning instead of a false error (regular accounts still fail hard).
- **S-7/S-8:** Guardrails DSL gains `ip_number_format` / `ip_number_format_optional` rules;
  salary_deductions and enforcement_orders autonomous configs now check IP-number format
  and the mandatory base-document number.

## [Unreleased] — Audit remediation (Итоги аудита 26.09.2026)

### Fixed — Blockers
- **B-01:** Plugin files (JSON/MD, 7 files per plugin) now shipped in wheel/sdist via
  `__init__.py` in every `doc_types/<id>/`, `[tool.setuptools.package-data]`, and `MANIFEST.in`.
  Verified: clean install loads 9/9 plugins with 7/7 required files each.
- **B-02:** `autonomous.json` for `hr_orders`, `legal_claims`, `powers_of_attorney` converted
  to canonical DSL (`{field, rule, severity, message}`); `PluginSpec` validates the structure.
- **B-03:** `ZeroTrustAuditor` resilient to malformed VLM output (non-dict nested fields,
  string amounts, None payloads) via `_as_dict()` guards and numeric coercion in `math_verifier`.

### Fixed — Critical
- **C-01:** ×100 scale error in `руб + коп` parsing (thousands separators vs decimal comma).
- **C-02:** Currency marker required for string amounts; bare INN/identifiers and negative
  values rejected.
- **C-03/C-04:** Canonical `path`/`label`/`kind` keys in all `benchmark.json`/`flat_columns.json`;
  `PluginSpec` validates and normalizes legacy keys.
- **C-05:** Ground Truth benchmark no longer compares ground truth with itself; missing
  extraction is reported as "schema integrity" check.
- **C-06:** Generic per-plugin evaluators for all 9 types (no silent `executive_documents` fallback).
- **C-07:** `doctor` returns aggregated `status` (`run_diagnostics`/`get_system_report` added).
- **C-08:** `evaluate_dataset` accepts directory paths and registry (MCP signature).
- **C-09:** MCP `scan_document` audits `result["data"]` with raw OCR text.
- **C-10:** CLI returns `EXIT_DISCREPANCY=3` / `EXIT_FALLBACK=4`; docs synchronized.
- **C-11:** `RateLimiter` raises `RateLimitTimeoutError` on timeout; `BoundedSemaphore`
  invariant enforced; `time.monotonic()` used.
- **C-12:** Filename collision guard with `__2`/`__3` suffixes and warning.
- **C-13/C-14:** `.env` and data roots resolved via cwd + `SCANREADER_HOME` /
  `SCANREADER_GROUND_TRUTH_DIR` / `SCANREADER_OUTPUT_DIR` env overrides.
- **C-15:** `validate_bank_account` (CB RF key algorithm) wired into the auditor.
- **C-16:** All `sys.path` hacks removed; package-relative imports; single registry instance.

### Fixed — High
- **H-01:** Remaining `except: pass` replaced with logged diagnostics.
- **H-02:** `SecretMaskingFilter` attached to all framework loggers (masking in logs).
- **H-03:** `write_atomic` (fsync + replace) in all production write paths, including
  1C JSON, metrics JSON/MD, cache, and Excel (temp + replace).
- **H-04:** `MAX_INPUT_BYTES` (50 MB) enforced on input files and DOCX zip members.
- **H-05:** `-v/--verbose` reconfigures existing loggers; framework logs go to stderr.
- **H-06:** Global flags before subcommand parse correctly (`scan-reader -v run f.pdf`).
- **H-07:** `path_patterns` use glob semantics (ordered parts, `*` = any run).
- **H-08:** Overly broad `*ispol*` pattern removed (no more FSSP→writ misclassification).
- **H-09:** Unknown autonomous rule types fail with a warning instead of silently passing.
- **H-10/H-11:** Bounded FIFO cache with atomic writes outside the package;
  SHA-256 cache keys including file size/mtime/content digest.
- **H-12/H-13:** `TokenUsageTracker` bounded deque + cumulative counters; thread-safe `set_stage`.
- **H-15/H-16:** Legacy root `core/` duplicate removed; shims delegate to the package;
  masking `except ImportError` fallbacks removed.
- **H-17:** `organize_subfolders` copies (shutil.copy2) instead of moving user documents.
- **H-18:** Test isolation: env/.env/cache/синглтоны/network blocking via autouse fixtures.
- **H-20:** MCP/JSON-RPC file access restricted to `SCANREADER_ALLOWED_DIRS` roots.
- **H-21:** Server positioned honestly as private line-delimited JSON-RPC 2.0;
  dead `mcp` extra removed from packaging.

### Fixed — Medium/Low
- **M-01:** Single version source (`__version__` = 0.8.0) across banner, `.env`, requirements,
  user guide, and `.bat` scripts.
- **M-02/M-03:** `valid_date_format` parses real dates; `positive_number_or_percentage` checks sign.
- **M-04:** Severity derived from the percentage value, not from message wording.
- **M-05:** Alimony detection checks multiple subject fields.
- **M-08:** Hallucination gate precomputes normalization once and matches digit groups exactly
  (no cross-boundary false confirmations).
- **M-10/M-11/M-12:** `type_registry`: cached `enabled()`, logger instead of stdout `print`,
  validation of 7 required plugin files.
- **M-13:** Batch processing resumes from checkpoints (`load_checkpoint`).
- **M-14–M-17:** RTF markup stripping; OLE2 `.doc` handled without mojibake;
  UTF-16 BOM/no-BOM detection; input size limits.
- **M-18:** `determine_recipient_type` can return `Individual`.
- **M-19:** Atomic writes in all remaining exporters.
- **M-22:** Ground truth keys aligned with plugin schemas.
- **M-25/M-26:** All env variables documented in `.env.example`; unused `pypdf` dependency removed.
- **M-28:** `doctor` CLI test runs on mocks without real network calls.
- **L-05/L-06:** `ruff`/`mypy`/`build` added to `requirements.txt`; coverage gate raised to 55%.

### Tests
- Test suite grown to 118 tests (was 75) covering the audit invariants:
  plugin packaging, malformed VLM payloads, rate limiter invariants, cache collisions,
  file size limits, glob classification, RTF/UTF-16 loaders, MCP whitelist, CLI exit codes.

## [0.8.0] - 2026-09-26

### Added
- **Modern Package Architecture:** Full transition to `src/scan_reader` layout with clean PEP 517/621 `pyproject.toml` packaging.
- **Zero-Trust Verification Engine (`scan_reader.verifier`):**
  - Checksum validation algorithms for Russian legal entities: INN (10 and 12 digits), SNILS, OGRN (13 digits), OGRNIP (15 digits), BIK, and 20-digit bank accounts.
  - Financial math reconciliation: verification of debt + fee == total within tolerance.
  - Statutory 229-FZ deduction limits check (<= 50% standard, <= 70% with alimony).
  - Legal temporal chronology validation: Court Act Date <= Writ Date <= Enforcement Initiation Date.
  - Cross-Modal Anti-Hallucination Gate against the raw OCR/text layer.
  - Status Taxonomy: `zero_trust_verified`, `vlm_unverified`, `heuristic_fallback`, `discrepancy_detected`, `ocr_low_confidence`, `rejected_unsupported`.
- **Private JSON-RPC Server (`scan_reader.mcp`):**
  - Line-delimited JSON-RPC 2.0 stdio server with 5 tools: `scan_document`, `classify_document`, `verify_legal_data`, `run_benchmark`, `export_results`.
- **Modular CLI (`scan-reader`):**
  - Dedicated subcommands: `run`, `classify`, `verify`, `export`, `benchmark`, `doctor`, `mcp`.
  - Machine-readable `--json` flag and deterministic exit codes (`EXIT_OK=0`, `EXIT_ERROR=1`, `EXIT_DISCREPANCY=3`, `EXIT_FALLBACK=4`).
- **Resilient I/O Utilities (`scan_reader.core.io_utils`):**
  - Atomic writing via temporary files with `os.fsync` and `os.replace`.
  - Global `MAX_INPUT_BYTES` input size limits enforced on input files.
  - Automatic secret masking for API tokens and PII in logs.
- **Comprehensive Test Suite:**
  - Unit and integration tests with 100% pass rate.
- **Standardized Bilingual Documentation:**
  - English and Russian editions for `README`, `ARCHITECTURE`, `CHANGELOG`, `SECURITY`, and `LICENSE`.

### Changed
- Refactored `facade.py`, `file_processor.py`, `type_registry.py`, and `excel_exporter.py` into `scan_reader` package without `sys.path` hacks.
- Moved operational `.bat` scripts to `scripts/windows/` while retaining legacy root entry points.
- Consolidated `cli.py` to delegate to `scan_reader.cli` with complete backward compatibility for 1C silent execution.

### Removed
- Removed temporary junk files (`— копия.env`).
