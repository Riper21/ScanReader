[English](ARCHITECTURE.md) | [Русский](ARCHITECTURE_RU.md)

# Architecture and Status Taxonomy

## 1. System Mission and Boundaries
`ScanReader` is an in-process document recognition, Zero-Trust verification, and data orchestration library designed for legal technology pipelines, accounting systems (such as 1C:Enterprise), and autonomous AI agents.

The system does **not** assume neural model outputs are ground truth. Instead, it operates on a **Zero-Trust paradigm**:
- Fast-Path multimodal visual classification routes documents to domain plugins;
- Specialized VLM extraction gathers structured fields according to strict Pydantic v2 schemas;
- An independent, deterministic **Zero-Trust Verification Engine** independently validates mathematical reconciliations, Russian statutory checksums (INN, SNILS, OGRN, BIK), temporal chronology, and cross-modal presence in the OCR layer;
- Atomic file operations ensure safe persistence and prevent corrupted output artifacts.

---

## 2. Verification Status Taxonomy

ScanReader reports clear, unambiguous statuses without false mathematical claims:

| Status | Code | Meaning & Guarantees |
| :--- | :--- | :--- |
| **`zero_trust_verified`** | `ZT_VERIFIED` | 100% corroborated: arithmetic matches ($\text{debt} + \text{fee} = \text{total}$), all check digits (INN, SNILS, OGRN) match, dates are chronologically consistent, and entities appear in OCR. Ready for automated unassisted import. |
| **`vlm_unverified`** | `VLM_UNVERIFIED` | Fields extracted and schema-valid, but no checksums or line-item breakdowns exist in the source document. Exported with an advisory verification flag. |
| **`heuristic_fallback`** | `HEURISTIC_FALLBACK` | Neural model produced broken JSON or timed out; deterministic regex fallback extracted monetary amounts and identifiers. |
| **`discrepancy_detected`** | `DISCREPANCY_ERROR` | A mathematical conflict, check digit mismatch, or statutory limit violation (e.g. deduction $> 70\%$) was detected. Quarantined for human review. |
| **`ocr_low_confidence`** | `OCR_POOR_QUALITY` | Scan is illegible, corrupted, or low resolution ($< 150 \text{ DPI}$ or OCR confidence $< 0.6$). |
| **`rejected_unsupported`** | `UNSUPPORTED_TYPE` | Document does not belong to any recognized legal execution or court order category. |

---

## 3. Component Pipeline Architecture

```mermaid
flowchart TD
    subgraph INGESTION ["1. Ingestion & Pre-processing"]
        INP["Input File (PDF, TIFF, JPG, DOCX)"] --> LIMIT["MAX_INPUT_BYTES Guard (50 MB)"]
        LIMIT --> PREP["FileProcessor: Dual-Zone Header Crop + PyMuPDF OCR"]
    end

    subgraph ROUTING ["2. Fast-Path Router"]
        PREP --> H_RULES["Heuristic Path/Keyword Rules"]
        H_RULES -->|Ambiguous| VLM_ROUTER["VLM Dual-Zone CoT Router"]
        H_RULES -->|Confident| REGISTRY["DocumentTypeRegistry"]
        VLM_ROUTER --> REGISTRY
    end

    subgraph EXTRACTION ["3. Extraction & Fallback"]
        REGISTRY --> VLM_EXTRACT["VLM Extractor (Qwen2.5-VL / Ollama / OpenAI)"]
        VLM_EXTRACT --> REGEX_FALLBACK["Financial Regex Recovery Scanner"]
        VLM_EXTRACT --> SCHEMAS["Pydantic v2 Document Schema Validation"]
        REGEX_FALLBACK --> SCHEMAS
    end

    subgraph ZERO_TRUST ["4. Zero-Trust Verification Engine"]
        SCHEMAS --> ZT_CHECKSUMS["Algorithmic Checksums: INN-10/12, SNILS, OGRN, BIK"]
        SCHEMAS --> ZT_MATH["Math Reconciliation: Total == Debt + Fee"]
        SCHEMAS --> ZT_LIMITS["Statutory Limits: Art. 99 229-FZ (<= 50% / 70%)"]
        SCHEMAS --> ZT_CHRONO["Chronology: Court Act <= Writ <= Enforcement"]
        SCHEMAS --> CROSS_MODAL["Cross-Modal OCR Gate: Anti-Hallucination"]
    end

    subgraph EXPORT ["5. Atomic Persistence"]
        ZT_CHECKSUMS & ZT_MATH & ZT_LIMITS & ZT_CHRONO & CROSS_MODAL --> STATUS["Taxonomy Status Assignment"]
        STATUS --> WRITE_ATOMIC["Atomic Write (_write_atomic + fsync)"]
        WRITE_ATOMIC --> EX_1C["1C JSON Import Registry"]
        WRITE_ATOMIC --> EX_EXCEL["Multi-Sheet Excel Report (openpyxl)"]
        WRITE_ATOMIC --> EX_MASTER["Structured Master JSON"]
    end
```

---

## 4. Model Context Protocol (MCP) Interface

The `scan_reader.mcp` module implements standard JSON-RPC 2.0 / MCP Stdio tools:
- `scan_document`: Complete multimodal parsing and Zero-Trust verification.
- `classify_document`: Fast-Path document routing.
- `verify_legal_data`: Standalone algorithmic auditing of JSON structures.
- `run_benchmark`: Evaluation suite against ground truth datasets.
- `export_results`: Consolidation into Excel and 1C import registries.

All tools enforce `MAX_INPUT_BYTES = 50 MB` and automatically mask secrets from logs.
