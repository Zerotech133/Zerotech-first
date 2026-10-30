# -*- coding: utf-8 -*-
"""D1 research MCP 工具测试（WO-D1-HERMES-BRIDGE-20261030）。"""
import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
P = REPO / "agent_platform" / "tools" / "d1_research_mcp.py"
spec = importlib.util.spec_from_file_location("d1_research_mcp", str(P))
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def test_tools_list():
    r = mod.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert r["result"]["tools"][0]["name"] == "d1_research"


def test_fetch_filter(monkeypatch):
    monkeypatch.setattr(mod, "fetch", lambda code=None, limit=20: {
        "ok": True, "summary": {"total": 1},
        "items": [{"code": code or "600519"}]})
    r = mod.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "d1_research",
                               "arguments": {"code": "600519"}}})
    assert r["result"]["isError"] is False
    text = r["result"]["content"][0]["text"]
    assert "600519" in text


def test_unknown_tool_error():
    r = mod.handle({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                    "params": {"name": "nope"}})
    assert "error" in r
