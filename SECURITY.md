[English](SECURITY.md) | [Русский](SECURITY_RU.md)

# Security Policy

## 1. Supported Versions

Security updates are applied to the latest minor version:

| Version | Supported |
| :--- | :--- |
| `0.8.x` | ✅ Yes |
| `< 0.8` | ❌ No |

---

## 2. Security Architecture & Threat Model

ScanReader is designed to process legally sensitive court documents, enforcement orders, and personal data (PDn). The architecture enforces the following security boundaries:

### On-Premise Air-Gapped Operation
- ScanReader is capable of operating completely offline in a closed network perimeter using local VLM engines (e.g. Ollama or vLLM running Qwen2.5-VL).
- External network requests to OpenAI or cloud providers occur only when explicitly configured via environment variables (`OPENAI_BASE_URL`).

### Protection of Personal Data (PII / PDn)
- Checksums and validations are calculated deterministically in-process without transmitting data to third parties.
- Logging utilities employ automatic regex-based masking (`mask_secret`) for sensitive strings, API keys (`sk-...`, Bearer tokens), and Russian passport numbers.

### Resource Limits and DoS Protection
- All input files and MCP payloads are bounded by `MAX_INPUT_BYTES = 50 MB` to prevent memory exhaustion, out-of-memory crashes, and denial-of-service vulnerabilities.
- Token Bucket rate limiters protect local GPU instances from concurrency-induced thrashing.

### Resilient Atomic I/O
- File writes employ `write_atomic` with temporary files, explicit disk syncing (`os.fsync`), and atomic replacement (`os.replace`) to guard against corruption during power interruptions.

---

## 3. Reporting a Vulnerability

If you discover a security vulnerability within ScanReader, please do not file a public issue. Instead, report it directly to the security maintainer:

- **Telegram:** [@riper21](https://t.me/riper21)
- **Response Time:** Reports are acknowledged within 48 hours, remediation timelines are provided within 5 business days.
