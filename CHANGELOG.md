[English](CHANGELOG.md) | [Русский](CHANGELOG_RU.md)

# Changelog

All notable changes to this project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.3] - 2026-09-29

Thin output contract. **Breaking:** the 1C-import file layer is removed;
per-plugin registries, `Registry_Flat.json`, `all_documents_registry.json`,
`1C_Импорт/` cards, `{stem}_raw.json` and `{stem}_{doc_type}.json` are no
longer written. A run now leaves exactly two files per document
(`{stem}_Full.json`, `{stem}_Flat.json`), one consolidated registry
(`Registry_Full.json`, raw records), the Excel block and the JSON/MD
metrics.

### Removed

- Per-document byte-identical copies: `{stem}_raw.json` and
  `{stem}_{doc_type}.json` duplicated `_Full.json` (or `_Flat.json` for
  salary writs); `1C_Импорт/{stem}_1c.json` duplicated `_Flat.json` and was
  written twice (single-run and batch). A 13-document cold run left 56
  files, three of which were the same file.
- Registry duplicates: `Registry_Flat.json`,
  `all_documents_registry.json`, per-plugin registries including the
  `documents_registry.json` alias (two files for one category), and
  `salary_deductions_registry_1c.json` — all derived from the same
  records as `Registry_Full.json` and the per-doc flat cards.
- `run_metrics_summary.xlsx` — a duplicate of the Excel block; run numbers
  live in `run_metrics_summary.json`/`.md` and `metrics_history.json`.
- Dead exporters `export_1c_target_json` / `export_single_1c_target_json`
  (no callers) and the `REGISTRY_ALIASES` table.

### Changed

- CLI `-f raw` is now a synonym of `full` (prints `{stem}_Full.json`);
  `-f 1c`/`flat`/`default` print `{stem}_Flat.json`; `-f stdout` prints the
  flat record body. `save_single_document_json` returns the `_Full.json`
  path.
- `Registry_Full.json` keeps storing raw result records (data,
  verification, metrics) — the merge (C-07) and Excel rebuild read this
  shape; 1C-flat normalization stays at the per-doc `{stem}_Flat.json`
  level.
- The MCP `export_results` tool now skips registry/flat/raw/metrics files
  when collecting documents: previously one document was loaded three
  times (Full + raw + type card) and the registries were re-fed into
  themselves, inflating `records_count`.
- The batch-directory CLI summary prints the `Registry_Full.json` and
  Excel paths; the measurement harness C.5 probe reads
  `Registry_Full.json` (fresh numbers in
  `docs/measurements/risks-0.9.3.md`).

### Compatibility notes

- 1C batch feeds can be rebuilt from per-doc `{stem}_Flat.json` records if
  needed; the standalone converter
  (`py -3 -m scan_reader.core.json_exporter <in> <out>`) still produces
  the target 1C array.
- `export`/launcher rebuilds skip legacy files when re-scanning old
  output directories.

### Tests

667 tests (was 660). Slim-output contract (exactly two files per card,
one registry per batch, no 1C_Импорт, no metrics xlsx, MCP single-count),
updated registry/CLI/format tests.

## [0.9.2] - 2026-09-29

Cross-modal gate honesty on real Russian text: inflection and money formats.

### Fixed

- A surname in the nominative case was never corroborated by the inflected
  document wording ("Иванов" against "Взыскать с должника Иванова Ивана
  Ивановича"), because the gate required an exact word boundary. The
  inflected-stem match now tolerates case endings of up to 3 letters (4 for
  soft "-ий/-ый/-ой" stems like "Римский" -> "Римского", and stem-replacing
  feminine endings "Ромашка" -> "Ромашки"), while values shorter than 5
  characters are not inflected at all — "Иван" still cannot be confirmed
  inside "Иванов", and "Иванов" cannot be confirmed inside "Ивановский".
- A whole-ruble amount returned as a JSON number with ".0" ("5075.0") was
  never corroborated by the accounting format "5 075,00": the digit atom
  "507500" loses the decimal position, and the int candidate "5075" did not
  match. Amounts and short identifiers (INN, SNILS, OGRN) are now compared
  numerically; formatting cannot mask a match, and a tenfold error cannot
  pass either. 20-digit accounts still compare digit-by-digit (float is not
  precise enough there).
- The digit fallback previously confirmed a *different* amount: the atom
  "507500" produced by "5 075,00" corroborated a claimed "507500" through
  digit-stripping. For values that parse unambiguously as numbers, the
  digit-strip path is no longer consulted.
- The batch-stage marker reported "[OK ...]" for documents whose extraction
  had failed; stage 5 counted failed extractions as "успешно обработано".
- A failed extraction set `requires_human_review: false` — a document with
  no extracted data cannot be accepted without a human.
- `scan-reader run <directory>` rejected the directory despite the help
  text ("Путь к файлу скана или каталогу"); directories are now dispatched
  to the batch pipeline with aggregated exit codes.
- `.gif` files were silently skipped by directory discovery (13 of 14 files
  found, no warning). GIF is now a supported image extension, and skipped
  unsupported files are reported.

### Changed

- `check_presence_in_raw_text` restructured: letter values go through
  exact pattern, inflection, then insertion-tolerant fuzzy; numeric values
  through numeric equality first, digit atoms only for identifiers and
  accounts. `normalize_token` (separator-stripping search form) is removed
  as dead code, together with the duplicated `MIN_REFERENCE_LENGTH`.
- CLI batch runs: all FAILED -> exit 1; any `discrepancy_detected` or
  `gate_not_executed` -> exit 3; any `heuristic_fallback` -> exit 4.

### Added

- `scripts/corpus/`: adversarial measurement harness for the open risks
  (routing by messy filenames, gate false positives under OCR noise,
  page-limit behaviour, registry I/O volume), deterministic under a seed.
  Clean-reference false positives: 0.0 %; degradation curve and caveats in
  `docs/measurements/risks-0.9.2.md`.

### Tests

660 tests (was 623). Inflection (nominative/genitive/soft/feminine,
cross-person and short-token negatives), kopecks formats for whole and
fractional amounts, tenfold-error rejection, CLI directory runs and exit
codes, honest stage-5 counters, GIF discovery, human-review flag for
failed extractions, and a smoke test of the measurement harness.

## [0.9.1] - 2026-09-28

Verification honesty and plugin isolation. **Breaking:** verification
semantics change — see "Breaking changes" below.

### Breaking changes

- A document with a fabricated, uncorroborated requisite (INN, case number,
  party name, amount) is now reported as `discrepancy_detected` with
  `is_valid=False`. It was previously reported as `zero_trust_verified` with
  `is_valid=True`, which contradicted the audit report in the same payload.
- `zero_trust_verified` now requires that checks actually ran: at least one
  control digit passed, a monetary reconciliation compared two or more numbers,
  and the cross-modal gate executed. A payload holding a single number no longer
  reaches it.
- Two statuses were added: `partially_verified` (some checks applied) and
  `gate_not_executed` (the document was supplied but no reference text was
  available, so cross-modal verification did not run). CLI exit code `3` is now
  also returned for `gate_not_executed`, because automated import of unconfirmed
  requisites is not permissible.
- The financial regex recovery now marks the fields it recovered
  (`_recovered_by_regex`, reported as `details.recovered_by_regex`), which makes
  `heuristic_fallback` reachable and CLI exit code `4` meaningful. Amounts
  produced this way are **not** to be treated as read off the page.
- Consoles must not assume `QualityScore` is an accuracy figure. Reports now
  carry `measurement_caveats` describing the measurement mode, how many
  documents were compared against ground truth, and when the figure rests on
  very few fields.
- Accuracy against ground truth is weighted over fields **present in the
  reference**; fields absent on both sides are excluded from the denominator,
  and a field invented by the model is penalised and fails validation.
- `benchmark.json` field `type` must come from the known vocabulary
  (`exact`, `numeric`/`number`, `text`/`string`, `inn`, `date`). Plugin loading
  fails on an unknown type. Previously `"number"` passed validation and money
  fields were compared as strings, so a tenfold error scored 98.32 % and was
  labelled "excellent".

### Fixed

- Money was never reconciled for writs, FSSP orders or acceptance certificates.
  Real figures `157611.62 + 7004.39 + 60000.00` and a fabricated `999999`
  produced identical results. Reconciliation is now declared per plugin.
- Legitimate zeroes were dropped from reconciliation: `d.get(a) or d.get(b)`
  discards `0.0`, so a waived debt, a zero fee and a zero total silently
  skipped the check.
- `parse_russian_currency` turned a negative amount into `None`, so a VLM
  returning `-150000` produced a field indistinguishable from an unfilled one.
  The sign is now preserved and the schema rejects it with a clear message.
- A failed extraction was reported as `status: COMPLETED` and reached the 1C and
  Excel registries. The registry filter read
  `status == "FAILED" and "data" not in item`, which made it a no-op for exactly
  the records it was meant to catch.
- Processing a single file truncated the accumulated registry, because
  `process_single_document` passed a one-element list to a full-replacement
  writer. Registries are now merged, keyed by path and document type.
- A benchmark run before processing compared ground truth with itself and
  reported 100.00 % accuracy. Extraction accuracy and ground-truth schema
  integrity are now separate figures, and a filename mismatch is reported as
  `autonomous_no_ground_truth_match` instead of `benchmark`.
- `generate_run_summary` discarded the measurement metadata, so an operator
  read "average 100 %" with no indication of what had been measured.
- The statutory limit check did not read the wording actually used in court
  orders: `parse_percentage_value` required a literal `%`, so "50 процентов" and
  "1/4 части заработка" were never parsed. The percentage check was also a
  keyword test that awarded 90 points to `80%` and `100%`.
- Art. 138 ТК РФ (20 % of total monthly income) was claimed in AGENTS.md but not
  implemented. `verify_tk138_ceiling` added.
- The docstring stated that the 70 % cap covers "child support or compensation
  of harm". Legally wrong: since Federal Law 314-FZ (2019) the 50 % cap does not
  apply to health harm at all and no percentage ceiling exists for it.
- The PFRS legacy SNILS exemption used `<= 1001997`, rejecting number
  001-001-998, which the standard exempts.
- The `inn` checksum existed twice; the copy in `core/metrics_evaluator.py`
  returned `is_valid=True` and 85 points for an INN failing its control digit,
  and the result fed the Quality Score.
- Month detection matched the substring `ма` (a truncated "мая"), so "сумма",
  "компания" and "норма" scored as valid dates. ISO dates were never accepted.
- `SecretMaskingFilter` touched only `record.msg`, so
  `logger.info("token %s", secret)` leaked the secret from `record.args`. Four
  modules used a raw `logging.getLogger` with no filter at all.
- Multi-page TIFF decoding ignored the page limit: all frames were decoded and
  encoded, and the limit only truncated the list. A whole PDF went into one
  multimodal request; the default is now 20 pages.
- The PDF file handle was closed inside the `try` block, so any read error left
  the file open until garbage collection.
- An empty text layer was sent to the model as the entire document content, and
  the model filled a legal schema with invented values. It is now an explicit
  `FAILED` before the model client is obtained.
- The cache rewrote the whole cache file with `fsync` while holding the lock on
  every `set()` (quadratic per run) and evicted by insertion order, discarding
  frequently reused entries first. Writes are batched outside the lock and
  eviction is LRU.
- The cache key hashed `repr()` of the entire base64 payload, materialising a
  second full copy of every document.
- Schema modules were registered in `sys.modules` and never removed, so every
  `force_reload` leaked one module per plugin.
- An unreadable existing result file made the collision guard treat the name as
  free and silently overwrite unknown content.
- The shipped `examples/samples/salary_deduction_sample.txt` paired BIK
  `044525225` with an account whose ЦБ РФ key does not balance, which would
  produce `INVALID_BANK_ACCOUNT`.
- A shipped BIK/account pair in a plugin prompt was rejected by the project's
  own validator.
- `verify_chronology` used `enforcement_date`, a field that exists in no schema,
  so the check was dead for every plugin.

### Added

- `verification.json` as an optional eighth plugin file, declaring parties and
  their identifiers, the bank block, monetary reconciliation rules, the Art. 99
  deduction limit, chronology rules and the fields the cross-modal gate must
  corroborate. `auditor.py` and `hallucination_gate.py` are now interpreters and
  contain no document field name at all, which a test enforces.
- `core/fields.py` with `ValidatedPartyMixin` and `ValidatedBankMixin`. The nine
  schemas had 43 normalisers and **zero** constraints: a 15-digit "INN",
  `deduction_percentage="999%"` and a negative amount all passed. `ge=0` on
  money fields, patterns on identifiers, control-digit validators, and
  `__test__ = False` on all nine root models.
- `core/ocr.py` and the `[ocr]` / `[ocr-full]` extras. The project had no OCR
  engine at all, so the cross-modal gate never ran for images. The gate now
  records its reference provenance (`gate_source`, `gate_independent`) and warns
  when the reference came from the same model as the extraction.
- `core/config.py`; `.env` is loaded in `__init__.py` before any submodule, so
  the E402 suppression for `facade.py` was removed from `pyproject.toml`.
- `ValidatedBankMixin` verifies the ЦБ РФ account key; a Bank of Russia account
  yields a warning rather than a false error.

### Changed

- `ValidationError` from a plugin schema is no longer swallowed into a silent
  `dict(parsed_data)` dump.
- Twelve plugin schemas, 152 regression tests across seven files.

### Removed

- `heuristic_fallback` is no longer dead code, but six genuinely dead symbols
  are gone: `build_document_schemas`, `build_system_prompts`,
  `build_category_names`, `build_category_folders`, `PluginSpec.dashboard_meta`,
  `read_file_safe`, plus the `extract_raw_text_for_audit` alias, the `ROOT_DIR`
  alias and the unused `prompt_user_on_unknown` parameter.
- UTF-8 stream configuration existed in three places, one of which
  reconfigured an already-reconfigured stream and raised
  `ValueError: cannot set 'encoding' after a stream has been accessed`.
- `process_batch` was a single 232-line method; it is now seven stage methods
  plus four helpers.

### Tests

623 tests (was 155). Coverage 71.88 % with the floor raised from 55 % to 70 %.
Both VLM call sites, all five JSON-RPC tools, both OCR backends, PDF rendering
and the batch stages are now covered. `tests/test_ground_truth_integrity.py`
validates every INN, SNILS and BIK in `data/ground_truth`, because an invalid
identifier there makes the measurement meaningless.

### Known limitations

- `data/ground_truth` holds 23 documents; six of the nine types have exactly
  one. Figures for those types describe a single document.
- No Russian legal document extraction benchmark exists in the open-source
  ecosystem; accuracy claims here are internal measurements, not published
  results.
- The JSON-RPC stdio server is a private protocol, not the official MCP
  standard, so MCP clients cannot connect without an adapter.
- `core/ocr.py` backend-specific code paths are covered with a stubbed engine;
  the engines themselves are not exercised in tests.


## [0.9.0] - 2026-09-28

Первая формальная релизная версия. Включает полное устранение находок аудита от 26.09.2026,
remediation качества сканов (S-1…S-8) и охранные механизмы распознавания (S-10…S-13).

### Added — Recognition guards (S-10…S-13)
- **S-13 OCR cross-check for scans:** images without a text layer now get a second VLM pass
  ("exact transcription") used as the reference in the cross-modal gate — garbled names, УИН,
  ИП/ИЛ numbers no longer pass silently. Controlled by `SCANREADER_OCR_CROSSCHECK` (default on).
  Each unconfirmed requisite applies a −5 pp quality penalty (capped at 15).
- **S-10:** Fractional withholding rates (`1/4`, `1/3`, `1/2`, `2/3` of income) now participate in
  the 229-FZ statutory limits check — alimony-deduction documents were previously exempt from control.
  IP numbers (`10701/16/3001-ИП`) are explicitly excluded from fraction parsing.
- **S-11:** Slash dates (`21/04/2016`) are parsed; garbage dates in any date field produce an
  `UNPARSEABLE_DATE` warning instead of silently disabling chronology checks.
- **S-12:** Cross-checks added: УИН must embed the РОСП code (`UIN_ROSP_MISMATCH`),
  ОКТМО length (8/11), КПП length (9), бланк ИЛ number length (8–9 digits),
  case numbers without separators score 60 instead of 100 (glue suspicion).

### Fixed — Scan quality (S-1…S-8, кейс 745×1024 @ 96 DPI)

Root cause analysis of a real 96 DPI scan with garbled requisites (lost `/` in IP numbers,
misread digits, false INVALID_BANK_ACCOUNT on a Bank of Russia account, Excel showing 100%).

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

### Fixed — Audit remediation (26.09.2026)

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
