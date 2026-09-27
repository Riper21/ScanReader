# -*- coding: utf-8 -*-
"""
Tests for Model Context Protocol (MCP) server.
"""

from scan_reader.mcp.server import (
    ScanReaderMCPServer,
    handle_jsonrpc,
)


def test_mcp_list_tools():
    server = ScanReaderMCPServer()
    tools = server.list_tools()
    tool_names = [t["name"] for t in tools]
    assert "scan_document" in tool_names
    assert "classify_document" in tool_names
    assert "verify_legal_data" in tool_names
    assert "run_benchmark" in tool_names
    assert "export_results" in tool_names


def test_mcp_call_verify_legal_data():
    server = ScanReaderMCPServer()
    sample_data = {
        "debtor": {"inn": "7707083893"},
        "finances": {"debt_amount_rub": 1000.0, "fee_penalty_rub": 70.0, "total_deduction_rub": 1070.0},
    }
    result = server.call_tool("verify_legal_data", {"data": sample_data, "doc_type": "salary_deductions"})
    assert result.get("is_valid") is True
    assert result.get("status") == "zero_trust_verified"


def test_mcp_jsonrpc_protocol():
    # Test tools/list request
    req = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    resp = handle_jsonrpc(req)
    assert resp["id"] == 1
    assert "tools" in resp["result"]
    assert len(resp["result"]["tools"]) == 5

    # Test unknown method
    unknown_req = {"jsonrpc": "2.0", "id": 2, "method": "invalid/method", "params": {}}
    resp_err = handle_jsonrpc(unknown_req)
    assert resp_err["id"] == 2
    assert "error" in resp_err
    assert resp_err["error"]["code"] == -32601
