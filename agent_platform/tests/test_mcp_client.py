# -*- coding: utf-8 -*-
"""MCP 客户端/服务端/注册表测试（WO-MCP-CLIENT）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_PLAT = _WT / "agent_platform"
sys.path.insert(0, str(_PLAT))

from mcp import registry  # noqa: E402
from mcp.client import MCPClient, MCPError  # noqa: E402
from mcp.server import dispatch  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dispatch_protocol():
    tools = {"echo": {"description": "echo", "inputSchema": {},
                      "handler": lambda text="": {"echo": text}}}
    init = dispatch({"id": 1, "method": "initialize", "params": {}}, tools, "t")
    assert init["result"]["serverInfo"]["name"] == "t"
    listed = dispatch({"id": 2, "method": "tools/list"}, tools, "t")
    assert listed["result"]["tools"][0]["name"] == "echo"
    called = dispatch({"id": 3, "method": "tools/call",
                       "params": {"name": "echo", "arguments": {"text": "hi"}}},
                      tools, "t")
    text = json.loads(called["result"]["content"][0]["text"])
    assert text["echo"] == "hi"
    assert dispatch({"id": 4, "method": "nope"}, tools, "t")["error"]["code"] == -32601


def _client(script: str, *args, timeout=20) -> MCPClient:
    return MCPClient([sys.executable, str(_WT / script), *args],
                     cwd=str(_WT), timeout=timeout)


def test_filesystem_server_read_and_traversal(tmp_path):
    (tmp_path / "a.txt").write_text("hello", encoding="utf-8")
    c = _client("tools/mcp_ro_filesystem.py", "--root", str(tmp_path)).start()
    try:
        names = [t["name"] for t in c.list_tools()]
        assert set(names) == {"list_dir", "read_file", "stat"}
        r = c.call_tool("read_file", {"path": "a.txt"})
        assert r["ok"] and r["payload"]["text"] == "hello"
        bad = c.call_tool("read_file", {"path": "C:/Windows/win.ini"})
        assert bad["isError"]
        assert "outside allowed roots" in json.dumps(bad["payload"], ensure_ascii=False)
    finally:
        c.close()


def test_zhaoyun_server_tools():
    c = _client("tools/mcp_ro_zhaoyun.py").start()
    try:
        names = {t["name"] for t in c.list_tools()}
        assert {"tasks_read", "org_read", "docs_search", "health_read"} <= names
        r = c.call_tool("tasks_read", {"limit": 2})
        assert r["ok"] and r["payload"].get("read_only") is True
    finally:
        c.close()


def test_git_server_status():
    c = _client("tools/mcp_ro_git.py", "--repo", str(_WT)).start()
    try:
        names = {t["name"] for t in c.list_tools()}
        assert {"git_status", "git_log", "git_diff"} <= names
        r = c.call_tool("git_status", {})
        assert r["ok"] and "repo" in r["payload"]
    finally:
        c.close()


def test_registry_probe_and_allowlist(tmp_path, monkeypatch):
    (tmp_path / "x.txt").write_text("xy", encoding="utf-8")
    cfg = {
        "servers": {
            "fs": {"script": "tools/mcp_ro_filesystem.py",
                   "args": ["--root", str(tmp_path)],
                   "risk": "read-only",
                   "allow_tools": ["list_dir", "read_file"]},
        }}
    cfg_path = tmp_path / "servers.json"
    cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(registry, "CONFIG", cfg_path)
    rep = registry.summary(force=True)
    assert rep["ok"] and rep["servers"][0]["name"] == "fs"
    assert set(rep["servers"][0]["tools"]) == {"list_dir", "read_file", "stat"}
    ok = registry.call("fs", "list_dir", {"path": "."})
    assert ok["ok"] and ok["payload"]["count"] >= 1
    deny = registry.call("fs", "stat", {"path": "."})
    assert deny["error"] == "tool_not_allowed"
    assert registry.call("nope", "list_dir")["error"] == "server_not_allowed"


def test_client_timeout(tmp_path):
    slow = tmp_path / "slow_server.py"
    slow.write_text("import time\ntime.sleep(30)\n", encoding="utf-8")
    c = MCPClient([sys.executable, str(slow)], timeout=1.0)
    try:
        c.start()
        raise AssertionError("should timeout")
    except MCPError as e:
        assert "timeout" in str(e)
    finally:
        c.close()
