# -*- coding: utf-8 -*-
"""MCP 只读文件 server：list_dir / read_file / stat（根目录白名单，防路径穿越）。"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
from agent_platform.legion_mcp.server import serve_stdio  # noqa: E402

ROOTS: list[Path] = []


def _set_roots(values: list[str]) -> None:
    global ROOTS
    out = []
    for v in values:
        p = Path(v)
        if not p.is_absolute():
            p = (REPO / p).resolve()
        else:
            p = p.resolve()
        out.append(p)
    ROOTS = out or [REPO.resolve()]


def _resolve(path: str) -> Path:
    p = Path(str(path or "."))
    if not p.is_absolute():
        for root in ROOTS:
            cand = (root / p)
            if cand.exists():
                p = cand
                break
        else:
            p = ROOTS[0] / p
    p = p.resolve()
    for root in ROOTS:
        try:
            p.relative_to(root)
            return p
        except ValueError:
            continue
    raise ValueError("path outside allowed roots: %s" % p)


def list_dir(path: str = ".", limit: int = 100):
    p = _resolve(path)
    if not p.is_dir():
        return {"ok": False, "error": "not_a_dir", "path": str(p)}
    limit = max(1, min(int(limit or 100), 500))
    items = []
    for child in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))[:limit]:
        items.append({"name": child.name, "dir": child.is_dir(),
                      "size": child.stat().st_size if child.is_file() else 0})
    return {"ok": True, "path": str(p), "root": str(ROOTS[0]),
            "count": len(items), "items": items}


def read_file(path: str, max_bytes: int = 200000):
    p = _resolve(path)
    if not p.is_file():
        return {"ok": False, "error": "not_a_file", "path": str(p)}
    max_bytes = max(1, min(int(max_bytes or 200000), 1000000))
    data = p.read_bytes()[:max_bytes]
    return {"ok": True, "path": str(p), "bytes": len(data),
            "truncated": p.stat().st_size > len(data),
            "text": data.decode("utf-8", errors="replace")}


def stat_path(path: str = "."):
    p = _resolve(path)
    st = p.stat()
    return {"ok": True, "path": str(p), "dir": p.is_dir(), "file": p.is_file(),
            "size": st.st_size, "mtime": st.st_mtime}


TOOLS = {
    "list_dir": {"description": "只读列目录（根白名单内）",
                 "inputSchema": {"type": "object", "properties": {
                     "path": {"type": "string"}, "limit": {"type": "integer"}}},
                 "handler": list_dir},
    "read_file": {"description": "只读文本文件（UTF-8，截断保护）",
                  "inputSchema": {"type": "object", "properties": {
                      "path": {"type": "string", "required": True},
                      "max_bytes": {"type": "integer"}}},
                  "handler": read_file},
    "stat": {"description": "只读路径元信息",
             "inputSchema": {"type": "object", "properties": {
                 "path": {"type": "string"}}},
             "handler": stat_path},
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MCP 只读文件 server")
    ap.add_argument("--root", action="append", default=[])
    args = ap.parse_args(argv)
    _set_roots(args.root or [str(REPO)])
    return serve_stdio(TOOLS, "zhaoyun-ro-filesystem")


if __name__ == "__main__":
    raise SystemExit(main())
