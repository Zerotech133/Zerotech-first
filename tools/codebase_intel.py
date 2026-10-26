# -*- coding: utf-8 -*-
"""codebase-intelligence：本仓库 repo map / 符号检索 / 引用与影响面（只读）。

能力卡 codebase-intelligence 的落地工具：
  python -X utf8 tools/codebase_intel.py map [--json] [--out path]
  python -X utf8 tools/codebase_intel.py search <query> [--json] [--limit N]
  python -X utf8 tools/codebase_intel.py refs <symbol> [--json]
  python -X utf8 tools/codebase_intel.py impact <path> [--json]
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
EXCLUDE_DIRS = {".git", ".worktrees", "node_modules", "__pycache__", ".venv", "venv",
                ".trae", ".pytest_cache", "dist", "build", "work", "quant_backtest"}
MAX_BYTES = 400_000


def _iter_py(root: Path):
    for p in sorted(root.rglob("*.py")):
        rel = p.relative_to(root)
        if any(part in EXCLUDE_DIRS for part in rel.parts):
            continue
        try:
            if p.stat().st_size > MAX_BYTES:
                continue
            yield p, rel.as_posix()
        except OSError:
            continue


def _parse(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None
    return tree


def build_map(root: Path | None = None) -> dict:
    root = Path(root) if root else REPO
    symbols, modules = [], []
    for p, rel in _iter_py(root):
        tree = _parse(p)
        if tree is None:
            continue
        doc = (ast.get_docstring(tree) or "").strip().splitlines()
        modules.append({"path": rel, "doc": doc[0][:160] if doc else "",
                        "lines": len(tree.body)})
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(node, ast.ClassDef) else "function"
                symbols.append({
                    "path": rel, "line": getattr(node, "lineno", 0), "kind": kind,
                    "name": node.name,
                    "qualname": getattr(node, "name", ""),
                    "doc": (ast.get_docstring(node) or "").strip().splitlines()[0][:120]
                    if ast.get_docstring(node) else "",
                })
    return {"schema": "codebase_intel/v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "root": str(root), "file_count": len(modules), "symbol_count": len(symbols),
            "modules": modules, "symbols": symbols}


def search(query: str, root: Path | None = None, limit: int = 50) -> list:
    q = str(query or "").strip().lower()
    if not q:
        return []
    data = build_map(root)
    hits = [s for s in data["symbols"]
            if q in s["name"].lower() or q in s.get("doc", "").lower()
            or q in s["path"].lower()]
    return hits[:max(1, limit)]


def refs(symbol: str, root: Path | None = None) -> list:
    sym = str(symbol or "").strip()
    if not sym:
        return []
    out = []
    for p, rel in _iter_py(Path(root) if root else REPO):
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == sym:
                out.append({"path": rel, "line": getattr(node, "lineno", 0), "kind": "name"})
            elif isinstance(node, ast.Attribute) and node.attr == sym:
                out.append({"path": rel, "line": getattr(node, "lineno", 0), "kind": "attr"})
    return out


def impact(path: str, root: Path | None = None) -> dict:
    root = Path(root) if root else REPO
    target = Path(path)
    rel = target.as_posix()
    stem = target.stem
    importers = []
    for p, r in _iter_py(root):
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[-1] == stem:
                        importers.append({"path": r, "line": getattr(node, "lineno", 0), "via": a.name})
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[-1]
                if mod == stem or any(a.name == stem for a in node.names):
                    importers.append({"path": r, "line": getattr(node, "lineno", 0),
                                      "via": node.module or ""})
    return {"schema": "codebase_impact/v1", "path": rel, "importers": importers,
            "importer_count": len({x["path"] for x in importers})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 codebase-intelligence（repo map/符号检索/影响面）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map")
    m.add_argument("--json", action="store_true")
    m.add_argument("--out", default="")
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("--json", action="store_true")
    s.add_argument("--limit", type=int, default=50)
    r = sub.add_parser("refs")
    r.add_argument("symbol")
    r.add_argument("--json", action="store_true")
    i = sub.add_parser("impact")
    i.add_argument("path")
    i.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.cmd == "map":
        data = build_map()
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.json:
            print(json.dumps({k: v for k, v in data.items() if k != "symbols"},
                             ensure_ascii=False, indent=1))
        else:
            print("# repo map  files=%d symbols=%d" % (data["file_count"], data["symbol_count"]))
            for mod in data["modules"][:200]:
                print("- %s  %s" % (mod["path"], mod["doc"]))
        return 0
    if args.cmd == "search":
        hits = search(args.query, limit=args.limit)
        print(json.dumps(hits, ensure_ascii=False, indent=1) if args.json else "\n".join(
            "%s:%s [%s] %s" % (h["path"], h["line"], h["kind"], h["name"]) for h in hits))
        return 0
    if args.cmd == "refs":
        hits = refs(args.symbol)
        print(json.dumps(hits, ensure_ascii=False, indent=1) if args.json else "\n".join(
            "%s:%s [%s]" % (h["path"], h["line"], h["kind"]) for h in hits))
        return 0
    if args.cmd == "impact":
        data = impact(args.path)
        print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else
              "importers=%d" % data["importer_count"])
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
