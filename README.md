[English](README.md) | [Русский](README_RU.md)

# ScanReader

[![CI](https://github.com/Riper21/ScanReader/actions/workflows/ci.yml/badge.svg)](https://github.com/Riper21/ScanReader/actions)
[![Python 3.9+](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Checked with mypy](https://www.mypy-lang.org/static/mypy_badge.svg)](https://mypy-lang.org/)

**ScanReader** is a production-grade Python 3.9+ platform for multimodal document recognition, Fast-Path classification, Zero-Trust legal and financial verification, and automated 1C/Excel registry orchestration.

It bridges the gap between raw document scans (court orders, writs of execution, salary deduction resolutions) and enterprise accounting systems (1C:Enterprise, ERP, relational data warehouses) with strict deterministic integrity guarantees.

---

## Key Guarantees & Features

- **Fast-Path Visual Routing:** a three-stage cascade — path/filename rules, then text markers, and only if both miss a Dual-Zone Chain-of-Thought VLM pass. The model is *not* consulted when a rule already matched, which keeps routine batches deterministic and cheap.
- **Zero-Trust Verification Engine:** deterministic audit of the extracted data, driven per document type by a `verification.json` declaration:
  - Checksum validation for Russian legal identifiers: INN (10 & 12 digits), SNILS, OGRN/OGRNIP, BIK, and 20-digit bank accounts (ЦБ РФ key 565-П).
  - Monetary reconciliation of components. A reconciliation only counts as a check when it compared **two or more numbers**; a single amount is reported as unchecked, not as verified.
  - Statutory salary deduction limits: 50 % standard, up to 70 % with a child-support basis (Art. 99 229-ФЗ), and 20 % of total monthly income (Art. 138 ТК РФ). Compensation for harm to health is *not* subject to the 50 % cap (Federal Law 314-ФЗ) and has no percentage ceiling — the auditor does not claim one.
  - Chronology of procedural acts.
- **Cross-Modal Anti-Hallucination Gate:** corroborates extracted names, identifiers and amounts against a reference channel, and records where that channel came from (`gate_source`, `gate_independent`). With `pip install "scan-reader[ocr]"` the reference is an **independent** OCR pass; without it, transcription comes from the same model that performed the extraction, which is explicitly marked as *not* independent.
- **Honest verification statuses:** `zero_trust_verified` means every applicable check passed. It is not a 100 % correctness claim, and the documentation states the boundary explicitly (see [ARCHITECTURE.md](ARCHITECTURE.md#2-verification-status-taxonomy)).
- **Private JSON-RPC Stdio Server:** 5 tools for trusted in-perimeter integrations, with file access confined to `SCANREADER_ALLOWED_DIRS`. Not the official MCP protocol.
- **Atomic File Operations:** prevents corrupted or partial writes using fsync and temporary replacement; registries are merged, never truncated.
- **Dual Canonical JSON Output (Full & Flat):** generates `{stem}_Full.json` (hierarchical schema, Zero-Trust audit report, Guardrails, measurement caveats) and `{stem}_Flat.json` (flat single-level schema for 1C accounting import).
- **Backward-compatible CLI:** drop-in replacement for existing 1C imports and batch runners, with deterministic exit codes.

### Exit codes

| Code | Meaning |
| :--- | :--- |
| `0` | Processed; no blocking verification finding |
| `1` | Processing error, or input file missing |
| `2` | CLI usage error |
| `3` | Discrepancy detected, or the cross-modal gate could not run — **human review required** |
| `4` | Amounts were recovered by the heuristic regex scanner rather than read by the model |

---

## Supported Document Types (Universal Enterprise Matrix)

ScanReader features a plug-and-play plugin architecture (`TypeRegistry`) providing out-of-the-box support for 9 essential Russian document classes:

| Category | Plugin ID | Key Entities & Zero-Trust Checks | Export Targets |
| :--- | :--- | :--- | :--- |
| **Commercial Contracts** | `commercial_contracts` | Counterparties, INN/KPP, OGRN, signatory powers, contract subject, total amount, VAT rates, duration dates. | 1C, Excel, Full JSON |
| **Invoices & UPD** | `invoices_upd` | Status 1/2, Seller/Buyer, INN 10/12, line items, VAT rates (20%/10%/0%), subtotal/total math balance. | 1C:Trade/ERP, Excel, Full JSON |
| **Acceptance Certificates** | `acceptance_certificates` | Customer, Contractor, underlying contract, reporting period, scope of services, total cost, claims-free clause. | 1C:Accounting, Excel, Full JSON |
| **Powers of Attorney** | `powers_of_attorney` | Principal, Agent (passport details, SNILS), scope of powers, subdelegation right, notary certification, validity term. | 1C:HR/ERP, Excel, Full JSON |
| **Legal Claims (Pre-Trial)** | `legal_claims` | Claimant, Debtor, contract basis, principal debt, penalties/late fees, Art. 395 CC interest, voluntary cure deadline. | Legal ERP, Excel, Full JSON |
| **HR Orders** | `hr_orders` | Unified forms T-1, T-5, T-6, T-8, employee ID, structural department, base salary, hiring/firing dates, labor contract. | 1C:HR/Payroll, Excel, Full JSON |
| **Enforcement Orders** | `enforcement_orders` | FSSP enforcement initiation orders & creditor applications, bailiff department, creditor/debtor data. | 1C, Excel, Full JSON |
| **Writs of Execution** | `executive_documents` | Series FS/VS, arbitral and state court rulings, awarded debt sums, judgment operative provisions. | 1C, Excel, Full JSON |
| **Salary Deductions** | `salary_deductions` | FSSP salary deduction resolutions, Art. 99 229-FZ, 50%/70% deduction caps, bank routing details. | 1C:Payroll, Excel, Full JSON |

---

## Installation

### Base Installation
```bash
pip install .
```

### OCR (recommended for scans)

Without an OCR engine the cross-modal gate for **scans** falls back to a transcription produced by the same model that performed the extraction. That is not an independent check — recognition errors cannot be told apart from extraction errors — and the report marks it as such.

```bash
# CPU, ~15 MB, same engine family as PaddleOCR
pip install ".[ocr]"

# PaddleOCR with GPU support and broader language coverage
pip install ".[ocr-full]"
```

After installation `scan-reader doctor` reports the active backend.

### Development Tools
```bash
pip install ".[dev]"
```

---

## Command-Line Interface (CLI)

The `scan-reader` CLI provides dedicated subcommands and supports machine-readable `--json` output:

```bash
# 1. Process document: return path to Flat JSON for 1C (default)
scan-reader run "C:\Scans\order.pdf" --format flat

# 2. Process document: return path to Full hierarchical JSON
scan-reader run "C:\Scans\order.pdf" --format full

# 3. Process document: return paths to both Full and Flat JSONs
scan-reader run "C:\Scans\order.pdf" --format both

# 4. Process with structured JSON output and Zero-Trust report
scan-reader run "C:\Scans\order.pdf" --json

# 5. Fast-Path classification of header
scan-reader classify "C:\Scans\order.pdf" --json

# 6. Zero-Trust audit of an existing extracted JSON
scan-reader verify "output\order_salary_deductions_Full.json" --json

# 7. Consolidate processed documents into 1C and Excel registries
scan-reader export "output" --format both

# 8. Run diagnostic self-check (VLM, TTFT, GPU, dependencies)
scan-reader doctor

# 9. Start private JSON-RPC stdio server (5 tools)
scan-reader mcp
```

### Exit Codes
- `0`: Processed; no blocking verification finding.
- `1`: Processing error or input file missing.
- `2`: CLI usage / argument syntax error.
- `3`: Discrepancy detected, or the cross-modal gate could not run — human review required.
- `4`: Amounts recovered by the heuristic regex scanner instead of read by the model.

---

## Python SDK API

```python
from scan_reader import LegalDocPlatformFacade
from scan_reader.verifier import ZeroTrustAuditor, VerificationStatus

# Initialize platform facade
facade = LegalDocPlatformFacade()

# Process document through the full multimodal pipeline
result = facade.process_single_document("scan.pdf")
print("Document Type:", result["doc_type"])
print("Quality Score:", result["quality_score_percent"])

# Perform independent Zero-Trust audit
report = ZeroTrustAuditor.audit_document(
    data=result["data"],
    doc_type=result["doc_type"],
    raw_ocr_text=result.get("raw_text")
)

if report.status == VerificationStatus.ZERO_TRUST_VERIFIED:
    # «100 % verified» would be a false claim: this means every applicable
    # deterministic check passed, which is a statement about the checks,
    # not about the document being correct.
    print("All applicable Zero-Trust checks passed")
    print("  checksums verified:", report.details.get("checksums_verified_ok"))
    print("  reconciliation run  :", report.details.get("math_verified_ok"))
    print("  gate source        :", report.details.get("gate_source"))
elif report.status == VerificationStatus.PARTIALLY_VERIFIED:
    print("Only part of the checks could be applied - selective review needed")
elif report.status == VerificationStatus.GATE_NOT_EXECUTED:
    print("Cross-modal gate did not run: requisites are unconfirmed")
elif report.status == VerificationStatus.DISCREPANCY_DETECTED:
    print("Discrepancy detected:", [issue.message for issue in report.issues])
```

---

## JSON-RPC Integration (Stdio)

ScanReader exposes a **private line-delimited JSON-RPC 2.0 stdio server** with 5 tools
(usage: `scan-reader mcp`). This is a private protocol — it is not compatible with the
official MCP SDK (no Content-Length framing, no `initialize` handshake); use it for
trusted in-perimeter integrations:

1. `scan_document`: Full extraction and Zero-Trust audit (paths restricted by `SCANREADER_ALLOWED_DIRS`).
2. `classify_document`: Fast-Path routing of document headers.
3. `verify_legal_data`: Algorithmic audit of identifiers and arithmetic.
4. `run_benchmark`: Evaluation against ground truth datasets.
5. `export_results`: Consolidation into Excel and 1C registries.

### Example request (one JSON object per line)
```json
{"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
{"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "classify_document", "arguments": {"file_path": "incoming/order.pdf"}}}
```

---

## Architecture & Status Taxonomy

Detailed component diagrams, lifecycle flows, and status semantics are available in [ARCHITECTURE.md](ARCHITECTURE.md) ([Русский](ARCHITECTURE_RU.md)).

---

## Support

- 🐛 **Bugs & feature requests:** [GitHub Issues](https://github.com/Riper21/ScanReader/issues)
- 💬 **Quick questions & integration help:** Telegram — [@riper21](https://t.me/riper21)

---

## License

MIT License. See [LICENSE](LICENSE) for details.
