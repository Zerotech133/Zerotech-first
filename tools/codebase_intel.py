# -*- coding: utf-8 -*-
"""codebase-intelligence：repo map / 符号检索 / 引用与影响面（只读，带缓存增量）。

默认只扫 `agent_platform/ tools/ portal/`（快）；`--all` 扫全仓。
缓存：`work/codebase_intel/cache.json`（按 mtime+size 增量，文件变动才重解析）。

用法：
  python -X utf8 tools/codebase_intel.py map [--all] [--scope a b] [--json] [--no-cache]
  python -X utf8 tools/codebase_intel.py search <q> [--json] [--limit N]
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
DEFAULT_SCOPE = ("agent_platform", "tools", "portal")
CACHE = REPO / "work" / "codebase_intel" / "cache.json"
CONFIG = REPO / "config" / "codebase_intel.json"
EXCLUDE_DIRS = {".git", ".worktrees", "node_modules", "__pycache__", ".venv", "venv",
                ".trae", ".pytest_cache", "dist", "build", "work", "quant_backtest",
                "legion_agents", "data_sources", "db_migration"}
MAX_BYTES = 400_000


def _load_config() -> dict:
    try:
        return json.loads(CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _apply_config() -> dict:
    global EXCLUDE_DIRS, MAX_BYTES
    cfg = _load_config()
    if cfg.get("exclude_dirs"):
        EXCLUDE_DIRS = set(cfg["exclude_dirs"])
    if cfg.get("max_bytes"):
        MAX_BYTES = int(cfg["max_bytes"])
    return cfg


def _iter_py(root: Path, scope=None):
    roots = [root / s for s in scope] if scope else [root]
    seen = set()
    for base in roots:
        if not base.exists():
            continue
        if base.is_file() and base.suffix == ".py":
            yield base, base.relative_to(root).as_posix()
            continue
        for p in sorted(base.rglob("*.py")):
            try:
                rel = p.relative_to(root)
            except ValueError:
                continue
            if p in seen or any(part in EXCLUDE_DIRS for part in rel.parts):
                continue
            try:
                if p.stat().st_size > MAX_BYTES:
                    continue
            except OSError:
                continue
            seen.add(p)
            yield p, rel.as_posix()


def _parse(path: Path):
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return ast.parse(text, filename=str(path))
    except (OSError, SyntaxError, ValueError):
        return None


def _entry(p: Path, rel: str):
    tree = _parse(p)
    if tree is None:
        return None
    doc = (ast.get_docstring(tree) or "").strip().splitlines()
    syms = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            syms.append({
                "path": rel, "line": getattr(node, "lineno", 0),
                "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                "name": node.name,
                "doc": (ast.get_docstring(node) or "").strip().splitlines()[0][:120]
                if ast.get_docstring(node) else "",
            })
    return {"module": {"path": rel, "doc": doc[0][:160] if doc else "",
                       "lines": len(tree.body)}, "symbols": syms}


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text(encoding="utf-8")).get("files", {})
    except Exception:
        return {}


def _save_cache(files: dict) -> None:
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"schema": "codebase_intel_cache/v1",
                                     "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                     "files": files}, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def build_map(root: Path | None = None, scope=None, use_cache: bool = True) -> dict:
    root = Path(root) if root else REPO
    cfg = _apply_config()
    scope = list(scope) if scope is not None else list(cfg.get("scope") or DEFAULT_SCOPE)
    t0 = time.time()
    old = _load_cache() if (use_cache and root == REPO) else {}
    files, modules, symbols = {}, [], []
    hits = misses = 0
    for p, rel in _iter_py(root, scope):
        try:
            st = p.stat()
            sig = [st.st_mtime_ns, st.st_size]
        except OSError:
            continue
        hit = old.get(rel)
        if hit and hit.get("sig") == sig:
            ent = {"module": hit["module"], "symbols": hit["symbols"]}
            hits += 1
        else:
            ent = _entry(p, rel)
            misses += 1
            if ent is None:
                continue
        files[rel] = {"sig": sig, **ent}
        modules.append(ent["module"])
        symbols.extend(ent["symbols"])
    if use_cache and root == REPO:
        _save_cache(files)
    return {"schema": "codebase_intel/v1",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "root": str(root), "scope": scope or ["(all)"],
            "file_count": len(modules), "symbol_count": len(symbols),
            "cache_hits": hits, "cache_misses": misses,
            "elapsed_ms": int((time.time() - t0) * 1000),
            "modules": modules, "symbols": symbols}


def search(query: str, root: Path | None = None, limit: int = 50, scope=None) -> list:
    q = str(query or "").strip().lower()
    if not q:
        return []
    data = build_map(root, scope)
    hits = [s for s in data["symbols"]
            if q in s["name"].lower() or q in s.get("doc", "").lower()
            or q in s["path"].lower()]
    return hits[:max(1, limit)]


def refs(symbol: str, root: Path | None = None, scope=None) -> list:
    _apply_config()
    sym = str(symbol or "").strip()
    if not sym:
        return []
    out = []
    for p, rel in _iter_py(Path(root) if root else REPO, scope):
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id == sym:
                out.append({"path": rel, "line": getattr(node, "lineno", 0), "kind": "name"})
            elif isinstance(node, ast.Attribute) and node.attr == sym:
                out.append({"path": rel, "line": getattr(node, "lineno", 0), "kind": "attr"})
    return out


def impact(path: str, root: Path | None = None, scope=None) -> dict:
    _apply_config()
    root = Path(root) if root else REPO
    target = Path(path)
    stem = target.stem
    importers = []
    for p, r in _iter_py(root, scope):
        tree = _parse(p)
        if tree is None:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.split(".")[-1] == stem:
                        importers.append({"path": r, "line": getattr(node, "lineno", 0),
                                          "via": a.name})
            elif isinstance(node, ast.ImportFrom):
                mod = (node.module or "").split(".")[-1]
                if mod == stem or any(a.name == stem for a in node.names):
                    importers.append({"path": r, "line": getattr(node, "lineno", 0),
                                      "via": node.module or ""})
    return {"schema": "codebase_impact/v1", "path": str(path), "importers": importers,
            "importer_count": len({x["path"] for x in importers})}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="田丰 codebase-intelligence（默认 scope + 缓存增量）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map")
    m.add_argument("--all", action="store_true")
    m.add_argument("--scope", nargs="*", default=None)
    m.add_argument("--json", action="store_true")
    m.add_argument("--out", default="")
    m.add_argument("--no-cache", action="store_true")
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
    scope = None if args.cmd == "map" and args.all else (
        (args.scope if getattr(args, "scope", None) is not None else list(DEFAULT_SCOPE))
        if args.cmd == "map" else list(DEFAULT_SCOPE))
    if args.cmd == "map":
        data = build_map(scope=scope, use_cache=not args.no_cache)
        if args.out:
            out = Path(args.out)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        if args.json:
            print(json.dumps({k: v for k, v in data.items() if k not in ("symbols", "modules")},
                             ensure_ascii=False, indent=1))
        else:
            print("# repo map  files=%d symbols=%d hits=%d misses=%d %.0fms"
                  % (data["file_count"], data["symbol_count"], data["cache_hits"],
                     data["cache_misses"], data["elapsed_ms"]))
            for mod in data["modules"][:200]:
                print("- %s  %s" % (mod["path"], mod["doc"]))
        return 0
    if args.cmd == "search":
        hits = search(args.query, limit=args.limit, scope=scope)
        print(json.dumps(hits, ensure_ascii=False, indent=1) if args.json else "\n".join(
            "%s:%s [%s] %s" % (h["path"], h["line"], h["kind"], h["name"]) for h in hits))
        return 0
    if args.cmd == "refs":
        hits = refs(args.symbol, scope=scope)
        print(json.dumps(hits, ensure_ascii=False, indent=1) if args.json else "\n".join(
            "%s:%s [%s]" % (h["path"], h["line"], h["kind"]) for h in hits))
        return 0
    if args.cmd == "impact":
        data = impact(args.path, scope=scope)
        print(json.dumps(data, ensure_ascii=False, indent=1) if args.json else
              "importers=%d" % data["importer_count"])
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
