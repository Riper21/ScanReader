# -*- coding: utf-8 -*-
"""
Example: Invoking ScanReader tools via Model Context Protocol (MCP).
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from scan_reader.mcp.server import ScanReaderMCPServer, handle_jsonrpc


def main():
    server = ScanReaderMCPServer()

    print("=== 1. Listing Registered MCP Tools ===")
    tools = server.list_tools()
    for t in tools:
        print(f"Tool: {t['name']} - {t['description'][:60]}...")

    print("\n=== 2. Calling 'verify_legal_data' Tool ===")
    payload = {
        "data": {
            "debtor": {"inn": "7707083893"},
            "finances": {"debt_amount_rub": 1000.0, "fee_penalty_rub": 70.0, "total_deduction_rub": 1070.0},
        },
        "doc_type": "salary_deductions",
    }
    result = server.call_tool("verify_legal_data", payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    print("\n=== 3. Simulating JSON-RPC 2.0 Request ===")
    rpc_request = {
        "jsonrpc": "2.0",
        "id": "agent-step-42",
        "method": "tools/call",
        "params": {
            "name": "verify_legal_data",
            "arguments": payload,
        },
    }
    rpc_response = handle_jsonrpc(rpc_request, server)
    print(json.dumps(rpc_response, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
