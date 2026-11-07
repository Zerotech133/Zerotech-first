# -*- coding: utf-8 -*-
"""D1 research MCP server (stdio) — Hermes 可调用的只读投研工具。

Hermes config entry:
  d1-research:
    command: C:\\Program Files\\Python311\\python.exe
    args: [C:\\项目\\老田的AI助手\\agent_platform\\tools\\d1_research_mcp.py]

Tools:
  - d1_research(code?: str, limit?: int) -> D1 池内研究快照（只读）
"""
from __future__ import annotations

import json
import os
import sys
# 回环禁代理（2026-11-07 批 5）：本机服务的请求不走系统代理，实现见 tools/safe_urlopen.py
import sys as _sys
from pathlib import Path as _Path

for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

BASE = os.environ.get("D1_RESEARCH_URL", "http://127.0.0.1:8734/api/d1/research")
TIMEOUT = float(os.environ.get("D1_RESEARCH_TIMEOUT", "15"))
PROTOCOL = "2024-11-05"

TOOLS = [{
    "name": "d1_research",
    "description": "D1 鲁肃投研快照：池内股票、已研究/可买/盯盘/pending 汇总；可按代码筛选。只读、paper-only。",
    "inputSchema": {
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "可选：6位股票代码"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 100,
                      "description": "可选：最多返回条数，默认 20"},
        },
        "additionalProperties": False,
    },
}]


def fetch(code=None, limit=20):
    with safe_urlopen(BASE, timeout=TIMEOUT) as r:
        data = json.loads(r.read())
    items = data.get("items") or []
    if code:
        c = str(code).split(".")[0]
        items = [x for x in items if str(x.get("code")) == c]
    items = items[:max(1, min(int(limit or 20), 100))]
    return {"ok": True, "date": data.get("date"),
            "summary": data.get("summary"), "team": data.get("team"),
            "items": items, "trading_permitted": False}


def _content(obj):
    return [{"type": "text", "text": json.dumps(obj, ensure_ascii=False)}]


def handle(msg):
    mid = msg.get("id")
    method = msg.get("method")
    if method == "initialize":
        return {"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "d1-research", "version": "1.0.0"}}}
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name != "d1_research":
            return {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32602, "message": "unknown tool: %s" % name}}
        try:
            obj = fetch(args.get("code"), args.get("limit", 20))
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": _content(obj), "isError": False}}
        except Exception as exc:  # noqa: BLE001
            return {"jsonrpc": "2.0", "id": mid,
                    "result": {"content": [{"type": "text",
                                            "text": "d1_research failed: %s" % str(exc)[:200]}],
                               "isError": True}}
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": "method not found: %s" % method}}


def main():
    # WO-MCP-UTF8-GUARD-20261104（判例 K-STOCK-428；根因见 WO-MCP-ENCODING-AUDIT-20261104）：
    # Hermes 以管道拉起 MCP server，且 spawn env 白名单不含 PYTHONIOENCODING/PYTHONUTF8，
    # Windows 文本 stdout 默认 cp936/GBK → json.dumps(ensure_ascii=False) 的中文按 GBK 字节
    # 写出，客户端按 UTF-8 + errors="replace" 解码 → U+FFFD 乱码。故显式 UTF-8；
    # 流不可 reconfigure（如测试 CaptureIO）时无害跳过。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except ValueError:
            continue
        if "id" not in msg:  # notification
            continue
        sys.stdout.write(json.dumps(handle(msg), ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
