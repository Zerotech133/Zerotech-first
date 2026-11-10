# -*- coding: utf-8 -*-
"""Tushare MCP 工具桥（streamable-http JSON-RPC）。

- 只从本机 .mcp.local.json 读取 URL/token，不向调用方返回 token；
- 提供 tools/list 与 tools/call，供 IDE/Agent 查数据；
- 缓存工具目录 5 分钟。
"""
from __future__ import annotations

import logging
import json
import time
import urllib.request
from pathlib import Path

try:
    from safe_http import safe_urlopen
except ImportError:  # 作为包导入时
    from legion_command.safe_http import safe_urlopen


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
CONFIG = REPO / ".mcp.local.json"
_CACHE = {"at": 0.0, "tools": []}
_CACHE_TTL = 300


def _url() -> str:
    try:
        d = json.loads(CONFIG.read_text(encoding="utf-8"))
        u = str(((d.get("mcpServers") or {}).get("tushareMcp") or {}).get("url") or "")
    except Exception:
        u = ""
    if not u.startswith("https://api.tushare.pro/mcp/"):
        raise RuntimeError("tushare_mcp_config_missing")
    return u


def _rpc(method: str, params: dict, timeout: int = 60) -> dict:
    body = json.dumps({"jsonrpc": "2.0", "id": int(time.time() * 1000) % 10_000_000,
                       "method": method, "params": params}).encode("utf-8")
    req = urllib.request.Request(_url(), data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    })
    with safe_urlopen(req, timeout=timeout) as r:
        raw = r.read().decode("utf-8", "replace")
    data = None
    if raw.lstrip().startswith("{"):
        data = json.loads(raw)
    else:
        for line in raw.splitlines():
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    break
                except Exception:
                    logging.getLogger(__name__).debug("swallowed exception at line 57")
                    continue
    if not isinstance(data, dict):
        raise RuntimeError("mcp_bad_response")
    if data.get("error"):
        raise RuntimeError("mcp_error: %s" % str(data["error"])[:200])
    return data.get("result") or {}


def list_tools(force: bool = False) -> list:
    now = time.time()
    if not force and _CACHE["tools"] and now - _CACHE["at"] < _CACHE_TTL:
        return _CACHE["tools"]
    res = _rpc("tools/list", {})
    tools = []
    for t in res.get("tools") or []:
        if not isinstance(t, dict):
            continue
        tools.append({
            "name": str(t.get("name") or ""),
            "description": str(t.get("description") or "")[:400],
            "inputSchema": t.get("inputSchema") or {},
        })
    _CACHE["tools"] = tools
    _CACHE["at"] = now
    return tools


def call_tool(name: str, arguments: dict | None = None) -> dict:
    name = str(name or "").strip()
    if not name:
        raise ValueError("tool 必填")
    names = {t["name"] for t in list_tools()}
    if name not in names:
        raise ValueError("unknown tool: %s" % name)
    args = arguments if isinstance(arguments, dict) else {}
    if len(json.dumps(args, ensure_ascii=False)) > 100_000:
        raise ValueError("arguments too large")
    return _rpc("tools/call", {"name": name, "arguments": args})


def status() -> dict:
    tools = list_tools()
    return {"ok": True, "provider": "tushareMcp", "tools": len(tools),
            "names": [t["name"] for t in tools[:20]]}
