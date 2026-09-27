"""
Инструментальный сервер ScanReader поверх Stdio (mcp/server.py).

Протокол: приватный строчно-ориентированный JSON-RPC 2.0 (H-21).
ВНИМАНИЕ: это НЕ официальный MCP SDK-протокол — нет Content-Length framing
и handshake initialize. Позиционируется как приватный JSON-RPC-интерфейс
инструментов ScanReader для интеграций внутри доверенного контура.

Безопасность (H-20): доступ к файлам ограничен whitelist корней —
переменная окружения SCANREADER_ALLOWED_DIRS (разделитель os.pathsep),
по умолчанию cwd и каталог результатов.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from ..core.io_utils import MAX_INPUT_BYTES
from ..facade import LegalDocPlatformFacade
from ..verifier.auditor import ZeroTrustAuditor

_srv_logger = logging.getLogger("mcp.server")

TOOL_DEFINITIONS = [
    {
        "name": "scan_document",
        "description": "Распознать юридический/исполнительный документ (PDF, скан, DOCX), выполнить VLM-экстракцию и Zero-Trust аудит.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Абсолютный путь к файлу скана"},
                "doc_type": {
                    "type": "string",
                    "enum": ["auto", "salary_deductions", "executive_documents", "enforcement_orders"],
                    "default": "auto",
                },
                "verify_zero_trust": {"type": "boolean", "default": True},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "classify_document",
        "description": "Выполнить быстрый Fast-Path роутинг документа по его шапке и визуальным маркерам.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Путь к файлу"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "verify_legal_data",
        "description": "Проверить готовые реквизиты (ИНН, СНИЛС, суммы, даты) через детерминированный Zero-Trust аудитор.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "data": {"type": "object", "description": "Словарь с реквизитами документа"},
                "doc_type": {"type": "string", "description": "Тип документа", "default": "salary_deductions"},
                "raw_ocr_text": {"type": "string", "description": "Сырой текст документа для антигаллюцинационного контроля"},
            },
            "required": ["data"],
        },
    },
    {
        "name": "run_benchmark",
        "description": "Запустить бенчмарк точности экстракции против эталонной разметки Ground Truth.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ground_truth_path": {"type": "string", "description": "Каталог эталонов (по умолчанию data/ground_truth)"},
            },
        },
    },
    {
        "name": "export_results",
        "description": "Собрать консолидированный Excel-реестр и 1С-выгрузку из обработанных документов.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "results_dir": {"type": "string", "description": "Каталог с результатами"},
                "target_format": {"type": "string", "enum": ["excel", "1c", "both"], "default": "both"},
            },
        },
    },
]


class ScanReaderMCPServer:
    """Model Context Protocol (MCP) server for ScanReader."""

    def __init__(self, facade: Optional[LegalDocPlatformFacade] = None):
        self._facade = facade

    @property
    def facade(self) -> LegalDocPlatformFacade:
        if self._facade is None:
            self._facade = LegalDocPlatformFacade()
        return self._facade

    # ------------------------------------------------------------------
    # H-20: whitelist корней для доступа к файлам
    # ------------------------------------------------------------------
    def _allowed_roots(self) -> List[Path]:
        env_roots = os.getenv("SCANREADER_ALLOWED_DIRS", "")
        roots: List[Path] = []
        if env_roots:
            roots.extend(Path(p.strip()).resolve() for p in env_roots.split(os.pathsep) if p.strip())
        if not roots:
            roots.append(Path.cwd().resolve())
            try:
                roots.append(Path(self.facade.results_dir).resolve())
            except Exception as e:
                _srv_logger.debug(f"Не удалось разрешить results_dir для whitelist: {e}")
        return roots

    def _is_path_allowed(self, file_path: str) -> bool:
        """Проверяет, что путь находится внутри одного из разрешенных корней (H-20)."""
        try:
            candidate = Path(file_path).resolve()
        except (OSError, ValueError) as e:
            _srv_logger.debug(f"Некорректный путь '{file_path}': {e}")
            return False
        for root in self._allowed_roots():
            try:
                candidate.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def list_tools(self) -> List[Dict[str, Any]]:
        return list(TOOL_DEFINITIONS)

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if not isinstance(arguments, dict):
            return {"error": "Arguments must be a JSON object", "isError": True}

        raw_bytes = len(json.dumps(arguments, ensure_ascii=False).encode("utf-8"))
        if raw_bytes > MAX_INPUT_BYTES:
            return {"error": f"Input exceeds maximum allowed size ({MAX_INPUT_BYTES} bytes)", "isError": True}

        try:
            if name == "scan_document":
                file_path = arguments.get("file_path", "")
                if not Path(file_path).is_file():
                    return {"error": f"File not found: {file_path}", "isError": True}
                if not self._is_path_allowed(file_path):
                    return {
                        "error": f"Access denied: path '{file_path}' is outside allowed roots. "
                                 f"Configure SCANREADER_ALLOWED_DIRS if needed.",
                        "isError": True,
                    }

                doc_type_arg = arguments.get("doc_type", "auto")
                doc_type = None if doc_type_arg == "auto" else doc_type_arg

                result = self.facade.process_single_document(
                    file_path=file_path,
                    doc_type=doc_type,
                    prompt_user_on_unknown=False,
                )

                if arguments.get("verify_zero_trust", True):
                    det_type = result.get("doc_type", "unknown")
                    raw_text = self.facade._extract_raw_text_for_audit(file_path)
                    extracted_data = result.get("data", {}) if isinstance(result, dict) else {}
                    report = ZeroTrustAuditor.audit_document(
                        data=extracted_data,
                        doc_type=det_type,
                        raw_ocr_text=raw_text,
                    )
                    result["zero_trust_report"] = report.to_dict()

                return result

            elif name == "classify_document":
                file_path = arguments.get("file_path", "")
                if not Path(file_path).is_file():
                    return {"error": f"File not found: {file_path}", "isError": True}
                if not self._is_path_allowed(file_path):
                    return {
                        "error": f"Access denied: path '{file_path}' is outside allowed roots.",
                        "isError": True,
                    }

                doc_type, confidence, method = self.facade.classify_document(file_path)
                return {
                    "file_path": file_path,
                    "doc_type": doc_type,
                    "confidence": confidence,
                    "method": method,
                }

            elif name == "verify_legal_data":
                data = arguments.get("data", {})
                doc_type = arguments.get("doc_type", "salary_deductions")
                raw_ocr_text = arguments.get("raw_ocr_text")
                report = ZeroTrustAuditor.audit_document(
                    data=data,
                    doc_type=doc_type,
                    raw_ocr_text=raw_ocr_text,
                )
                return report.to_dict()

            elif name == "run_benchmark":
                from ..core.metrics_evaluator import evaluate_dataset, generate_run_summary
                gt_path_raw: Any = arguments.get("ground_truth_path")
                gt_path = str(gt_path_raw) if gt_path_raw else ""
                if not gt_path:
                    candidates = [
                        os.path.join(os.getcwd(), "data", "ground_truth"),
                        os.path.join(self.facade.results_dir, "..", "data", "ground_truth"),
                    ]
                    for c in candidates:
                        if os.path.isdir(c):
                            gt_path = c
                            break
                    if not gt_path:
                        gt_path = candidates[0]

                gt_dir = Path(gt_path).resolve()
                if not gt_dir.is_dir():
                    return {"error": f"Ground truth directory not found: {gt_dir}", "isError": True}

                bench_results_path = Path(self.facade.results_dir)
                eval_metrics = evaluate_dataset(bench_results_path, gt_dir, self.facade.registry)
                summary = generate_run_summary(eval_metrics)
                return summary

            elif name == "export_results":
                import glob
                from ..excel_exporter import LegalExcelExporter
                from ..core.json_exporter import export_consolidated_registries

                results_dir = str(arguments.get("results_dir") or self.facade.results_dir)
                target_format = arguments.get("target_format", "both")

                json_files = glob.glob(os.path.join(results_dir, "*.json"))
                loaded = []
                for jf in json_files:
                    base = os.path.basename(jf)
                    if base.startswith("run_metrics") or base.startswith("benchmark_") or base.endswith("_quality_metrics.json"):
                        continue
                    try:
                        with open(jf, "r", encoding="utf-8") as f:
                            d = json.load(f)
                            if isinstance(d, dict):
                                loaded.append(d)
                            elif isinstance(d, list):
                                loaded.extend(d)
                    except Exception as e:
                        _srv_logger.debug(f"Пропущен некорректный JSON файл '{jf}': {e}")

                exported_files: List[str] = []
                if loaded:
                    if target_format in ("1c", "both"):
                        jsons = export_consolidated_registries(loaded, results_dir)
                        exported_files.extend(jsons)

                    if target_format in ("excel", "both"):
                        exporter = LegalExcelExporter()
                        out_path = exporter.export_results_to_excel(loaded, output_dir=results_dir)
                        exported_files.append(os.path.basename(out_path))

                return {
                    "records_count": len(loaded),
                    "exported_files": exported_files,
                    "results_dir": results_dir,
                }

            else:
                return {"error": f"Unknown tool: {name}", "isError": True}

        except Exception as exc:
            return {"error": f"Tool execution failed: {exc}", "isError": True}


def handle_jsonrpc(request: Dict[str, Any], server: Optional[ScanReaderMCPServer] = None) -> Dict[str, Any]:
    """Process a single JSON-RPC 2.0 / MCP request."""
    srv = server or ScanReaderMCPServer()
    req_id = request.get("id")
    method = request.get("method")
    params = request.get("params", {})

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": srv.list_tools()}}
    elif method == "tools/call":
        tool_name = params.get("name")
        args = params.get("arguments", {})
        result = srv.call_tool(tool_name, args)
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False, indent=2)}]},
        }
    else:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }


def run_stdio_server() -> None:
    """Run interactive MCP Stdio loop reading line-delimited JSON-RPC from stdin."""
    server = ScanReaderMCPServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_jsonrpc(req, server)
            sys.stdout.write(json.dumps(resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
        except Exception as e:
            err_resp = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"},
            }
            sys.stdout.write(json.dumps(err_resp, ensure_ascii=False) + "\n")
            sys.stdout.flush()
