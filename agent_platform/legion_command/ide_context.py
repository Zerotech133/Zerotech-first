# -*- coding: utf-8 -*-
"""IDE 上下文引擎：repo map + 词法检索 + 预算压缩。

只读，项目根内；输出给模型前按字符预算截断，避免“把整个仓库塞进 prompt”。
"""
from __future__ import annotations

import ast
import re
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCAN_ROOTS = ("agent_platform", "portal", "tools", "docs", "deliverables", "ops")
BLOCKED_DIRS = {
    ".git", ".worktrees", "_vault", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".venv", "venv", "dist", "build",
}
BLOCKED_PREFIXES = ("work/credentials", "work/models/keys", "_vault", "config/secrets")
TEXT_EXTS = {
    ".py", ".md", ".html", ".js", ".css", ".json", ".yaml", ".yml",
    ".txt", ".ps1", ".bat", ".toml", ".ini", ".cfg", ".sql", ".sh",
}
MAX_FILES = 5000
MAX_FILE_BYTES = 300_000
MAX_SNIPPET_LINES = 12


def _blocked(rel: str) -> bool:
    low = rel.lower()
    for pre in BLOCKED_PREFIXES:
        if low == pre or low.startswith(pre + "/"):
            return True
    parts = low.split("/")
    if any(p in BLOCKED_DIRS for p in parts):
        return True
    name = parts[-1]
    return bool(re.search(r"(^\.env($|\.)|\.local\.json$|^id_rsa|\.pem$|\.key$)", name))


def _iter_files():
    seen = 0
    for root_name in SCAN_ROOTS:
        root = REPO / root_name
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if seen >= MAX_FILES:
                return
            if not p.is_file() or p.suffix.lower() not in TEXT_EXTS:
                continue
            try:
                rel = p.relative_to(REPO).as_posix()
            except ValueError:
                continue
            if _blocked(rel):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue
            seen += 1
            yield p, rel, size


def _terms(q: str) -> list[str]:
    q = str(q or "").lower()
    latin = re.findall(r"[a-z0-9_]{2,}", q)
    cjk = re.findall(r"[\u4e00-\u9fff]{2,}", q)
    return list(dict.fromkeys(latin + cjk))[:12]


def repo_map() -> dict:
    dirs: dict[str, int] = {}
    files = 0
    bytes_total = 0
    for _p, rel, size in _iter_files():
        files += 1
        bytes_total += size
        dirs[rel.split("/")[0]] = dirs.get(rel.split("/")[0], 0) + 1
    return {"files": files, "bytes": bytes_total,
            "dirs": dict(sorted(dirs.items(), key=lambda kv: -kv[1]))}


_SYMBOL_CACHE: dict[tuple[str, float], list[dict]] = {}
_SYMBOL_CACHE_TS = 0.0
_SYMBOL_CACHE_TTL = 120.0


def _file_symbols(p: Path, rel: str, mtime: float) -> list[dict]:
    key = (rel, mtime)
    cached = _SYMBOL_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(text, filename=rel)
    except (OSError, SyntaxError, ValueError):
        _SYMBOL_CACHE[key] = []
        return []
    out = []

    def add(node, prefix=""):
        name = prefix + getattr(node, "name", "")
        if not name:
            return
        kind = "class" if isinstance(node, ast.ClassDef) else "function"
        out.append({"path": rel, "name": name, "kind": kind,
                    "line": int(getattr(node, "lineno", 0))})

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node)
        elif isinstance(node, ast.ClassDef):
            add(node)
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(sub, node.name + ".")
    _SYMBOL_CACHE[key] = out
    return out


def symbol_index(limit_files: int = 2500) -> dict:
    global _SYMBOL_CACHE_TS
    now = time.time()
    if now - _SYMBOL_CACHE_TS > _SYMBOL_CACHE_TTL:
        _SYMBOL_CACHE.clear()
        _SYMBOL_CACHE_TS = now
    symbols = []
    files = 0
    for p, rel, _size in _iter_files():
        if p.suffix.lower() != ".py":
            continue
        files += 1
        if files > limit_files or len(symbols) > 20000:
            break
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        symbols.extend(_file_symbols(p, rel, mtime))
    return {"files": files, "symbols": len(symbols), "items": symbols}


def search_symbols(query: str, limit: int = 20) -> list[dict]:
    """先按查询词预筛文件，再只解析命中文件，避免全仓 AST 扫描。"""
    ts = _terms(query)
    if not ts:
        return []
    hits = []
    scanned = 0
    for p, rel, _size in _iter_files():
        if p.suffix.lower() != ".py":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        if not any(t in low for t in ts):
            continue
        scanned += 1
        if scanned > 200:
            break
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        for sym in _file_symbols(p, rel, mtime):
            name = str(sym.get("name") or "").lower()
            path = str(sym.get("path") or "").lower()
            score = 0
            for t in ts:
                if t in name:
                    score += 10
                if t in path:
                    score += 2
            if score:
                hits.append({**sym, "score": score})
    hits.sort(key=lambda h: (-h["score"], h["line"]))
    return hits[: max(1, min(int(limit), 50))]


def search(query: str, limit: int = 12) -> list[dict]:
    ts = _terms(query)
    if not ts:
        return []
    hits = []
    for p, rel, size in _iter_files():
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        low = text.lower()
        score = 0
        for t in ts:
            score += min(low.count(t), 8)
        if score == 0:
            continue
        score += sum(4 for t in ts if t in rel.lower())
        # 截取首个命中词附近的行
        first = min((low.find(t) for t in ts if low.find(t) >= 0), default=0)
        line_no = low.count("\n", 0, first)
        lines = text.splitlines()
        start = max(0, line_no - 2)
        end = min(len(lines), start + MAX_SNIPPET_LINES)
        hits.append({"path": rel, "score": score, "line": line_no + 1,
                     "snippet": "\n".join(lines[start:end])})
    hits.sort(key=lambda h: (-h["score"], h["path"]))
    return hits[: max(1, min(int(limit), 50))]


def build_context(query: str, budget_chars: int = 8000) -> dict:
    budget_chars = max(1000, min(int(budget_chars or 8000), 30000))
    m = repo_map()
    hits = search(query, 12)
    syms = search_symbols(query, 12)
    head = ("[IDE repo map]\n"
            "根目录: " + ", ".join("%s(%d)" % (k, v) for k, v in m["dirs"].items())
            + "\n文件 %d 个 / %.1f KB\n" % (m["files"], m["bytes"] / 1024.0))
    parts = [head]
    used = len(head)
    omitted = 0
    if syms:
        sym_lines = ["\n[Python symbols · AST]"]
        for s in syms:
            sym_lines.append("- %s %s:%s %s" % (s.get("kind"), s.get("path"),
                                                 s.get("line"), s.get("name")))
        block = "\n".join(sym_lines) + "\n"
        if used + len(block) <= budget_chars:
            parts.append(block)
            used += len(block)
        else:
            omitted += 1
    for h in hits:
        block = "\n### %s (score=%d, line=%d)\n%s\n" % (
            h["path"], h["score"], h["line"], h["snippet"])
        if used + len(block) > budget_chars:
            omitted += 1
            continue
        parts.append(block)
        used += len(block)
    if omitted:
        parts.append("\n[context compressed] 另有 %d 个命中因预算省略；如需请指定文件。\n" % omitted)
    context = "".join(parts)
    return {"ok": True, "query": str(query or ""), "repo": m, "hits": hits,
            "symbols": syms,
            "context": context, "chars": len(context), "budget": budget_chars,
            "truncated": omitted > 0 or len(context) > budget_chars}
