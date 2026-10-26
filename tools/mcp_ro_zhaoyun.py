# -*- coding: utf-8 -*-
"""赵云 MCP 只读工具 server（stdlib，复用 zhuge_mcp_server 的只读数据函数）。

工具：tasks_read / org_read / health_read / docs_search
边界：只读；不写文件、不 subprocess、不派单；health 仅本机回环 HTTP。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))

from agent_platform.mcp.server import serve_stdio  # noqa: E402

try:
    import zhuge_mcp_server as z  # noqa: E402
except Exception as e:  # noqa: BLE001
    z = None
    _import_err = "%s: %s" % (type(e).__name__, e)


def _wrap(fn, *a, **k):
    if z is None:
        return {"ok": False, "read_only": True, "error": "zhuge_import_failed",
                "detail": _import_err}
    return fn(*a, **k)


def tasks_read(status: str = "", owner: str = "", limit: int = 20):
    return _wrap(z.tasks_read, status=status, owner=owner,
                 limit=max(1, min(int(limit or 20), 50)))


def org_read(agent_id: str = ""):
    return _wrap(z.org_read, agent_id=agent_id)


def docs_search(query: str = "", limit: int = 8):
    return _wrap(z.docs_search, query=query,
                 limit=max(1, min(int(limit or 8), 20)))


def health_read(timeout: float = 3.0):
    return _wrap(z.health_read, timeout=max(0.5, min(float(timeout or 3.0), 10.0)))


TOOLS = {
    "tasks_read": {
        "description": "只读任务账本（可按 status/owner 过滤，最多 50 条）",
        "inputSchema": {"type": "object", "properties": {
            "status": {"type": "string"}, "owner": {"type": "string"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50}}},
        "handler": tasks_read},
    "org_read": {
        "description": "只读组织/席位信息（org.json）",
        "inputSchema": {"type": "object", "properties": {
            "agent_id": {"type": "string"}}},
        "handler": org_read},
    "docs_search": {
        "description": "只读 docs/ 文本检索（返回路径/行/片段）",
        "inputSchema": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"]},
        "handler": docs_search},
    "health_read": {
        "description": "只读本机关键服务健康（8091/8795 等，回环 HTTP）",
        "inputSchema": {"type": "object", "properties": {
            "timeout": {"type": "number"}}},
        "handler": health_read},
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="赵云 MCP 只读工具 server")
    ap.add_argument("--list-tools", action="store_true")
    args = ap.parse_args(argv)
    if args.list_tools:
        import json
        print(json.dumps([{"name": n, "description": s["description"]}
                          for n, s in TOOLS.items()], ensure_ascii=False))
        return 0
    return serve_stdio(TOOLS, "zhaoyun-readonly")


if __name__ == "__main__":
    raise SystemExit(main())
