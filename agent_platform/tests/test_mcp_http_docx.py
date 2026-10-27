# -*- coding: utf-8 -*-
"""MCP HTTP + DOCX 表格测试（WO-MCP-HTTP-DOCX）。"""

from __future__ import annotations

import importlib.util
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_WT / "agent_platform"))
sys.path.insert(0, str(_WT / "tools"))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import importlib  # noqa: E402

importlib.import_module("legion_mcp")  # 确保包已导入（相对导入可解析）
HC = importlib.import_module("legion_mcp.http_client")
REG = importlib.import_module("legion_mcp.registry")
OD = _load("office_doc", _WT / "agent_platform" / "tools" / "office_doc.py")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(length).decode("utf-8"))
        method = req.get("method")
        if method == "initialize":
            res = {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "mock", "version": "1"},
            }
        elif method == "tools/list":
            res = {
                "tools": [
                    {
                        "name": "ping_tool",
                        "description": "ping",
                        "inputSchema": {"type": "object"},
                    }
                ]
            }
        elif method == "tools/call":
            res = {
                "content": [{"type": "text", "text": json.dumps({"pong": True})}],
                "isError": False,
            }
        else:
            res = {}
        body = [{"jsonrpc": "2.0", "id": req.get("id"), "result": res}]
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Mcp-Session-Id", "sess-1")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def test_http_client_flow():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        c = HC.MCPHttpClient("http://127.0.0.1:%d/mcp" % port, timeout=5)
        c.initialize()
        assert c.session_id == "sess-1"
        tools = c.list_tools()
        assert tools[0]["name"] == "ping_tool"
        rep = c.call_tool("ping_tool", {})
        assert rep["ok"] and rep["payload"]["pong"] is True
        c.close()
    finally:
        srv.shutdown()


def test_registry_http_transport(tmp_path, monkeypatch):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        cfg = {
            "servers": {
                "http-mock": {
                    "transport": "http",
                    "url": "http://127.0.0.1:%d/mcp" % port,
                    "risk": "read-only",
                    "allow_tools": ["ping_tool"],
                    "timeout": 5,
                }
            }
        }
        p = tmp_path / "servers.json"
        p.write_text(json.dumps(cfg), encoding="utf-8")
        monkeypatch.setattr(REG, "CONFIG", p)
        rep = REG.probe("http-mock")
        assert rep["ok"] and rep["servers"][0]["transport"] == "http"
        call = REG.call("http-mock", "ping_tool", {})
        assert call["ok"] and call["payload"]["pong"] is True
        denied = REG.call("http-mock", "other", {})
        assert denied["error"] == "tool_not_allowed"
    finally:
        srv.shutdown()


def test_registry_disabled_http(tmp_path, monkeypatch):
    cfg = {
        "servers": {
            "disabled": {
                "transport": "http",
                "url": "https://api.example.com/mcp",
                "enabled": False,
                "risk": "read-only",
            }
        }
    }
    p = tmp_path / "servers.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(REG, "CONFIG", p)
    rep = REG.probe("disabled")
    assert rep["ok"] is True
    assert rep["servers"][0]["skipped"] is True
    assert "transport_disabled" in rep["servers"][0]["error"]
    call = REG.call("disabled", "x", {})
    assert call["error"] in ("transport_disabled", "tool_not_allowed")


def test_docx_table_roundtrip(tmp_path):
    p = tmp_path / "t.docx"
    rows = [["姓名", "分数"], ["赵云", "90"], ["关羽", "88"]]
    rep = OD.write_docx(p, "表格测试", ["正文"], tables=[rows])
    assert rep["ok"] and rep["tables"] == 1 and rep["table_rows"] == 3
    back = OD.read_docx(p)
    assert back["ok"] and back["tables"] == 1
    assert back["table_data"][0][0] == ["姓名", "分数"]
    assert back["table_data"][0][1] == ["赵云", "90"]
