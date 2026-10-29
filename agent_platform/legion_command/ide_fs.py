# -*- coding: utf-8 -*-
"""IDE 调试通道只读文件工具：list_dir / read_file / search。

安全边界：
- 只允许仓库根目录内相对路径；
- 不读 .git/.worktrees/_vault/node_modules/缓存/凭据目录；
- 不读 .env / *.local.json / 密钥类文件；
- 只读，不提供写入/删除/执行。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO.resolve()

BLOCKED_DIRS = {
    ".git", ".worktrees", "_vault", "node_modules", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".venv", "venv", "dist", "build",
}
BLOCKED_PREFIXES = (
    "work/credentials", "work/models/keys", "_vault", "config/secrets",
)
TEXT_EXTS = {
    ".py", ".md", ".html", ".js", ".css", ".json", ".yaml", ".yml",
    ".txt", ".ps1", ".bat", ".toml", ".ini", ".cfg", ".sql", ".sh",
    ".ts", ".tsx", ".jsx", ".csv",
}
BLOCKED_NAME_RE = re.compile(
    r"(?i)(^\.env($|\.)|\.local\.json$|^id_rsa|^id_ed25519|\.pem$|\.key$|"
    r"^credentials?\.(json|txt|ya?ml)$|^secrets?\.(json|txt|ya?ml)$)"
)
MAX_LIST = 300
MAX_READ = 20000
MAX_SEARCH_FILES = 4000
MAX_SEARCH_HITS = 50


class FsError(Exception):
    def __init__(self, code: str, status: int = 400, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.status = status
        self.detail = detail


def _rel(raw: str) -> str:
    s = str(raw or ".").strip().replace("\\", "/")
    if not s:
        s = "."
    if s.startswith("/") or re.match(r"^[A-Za-z]:", s):
        raise FsError("absolute_path_denied", 403)
    parts = [p for p in s.split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise FsError("path_escape_denied", 403)
    return "/".join(parts) or "."


def _resolve(raw: str) -> tuple[Path, str]:
    rel = _rel(raw)
    low = rel.lower()
    for pre in BLOCKED_PREFIXES:
        if low == pre or low.startswith(pre + "/"):
            raise FsError("sensitive_path_denied", 403)
    for part in rel.split("/"):
        if part in BLOCKED_DIRS:
            raise FsError("blocked_path", 403)
        if BLOCKED_NAME_RE.search(part):
            raise FsError("sensitive_file_denied", 403)
    p = (ROOT / rel).resolve()
    if p != ROOT and ROOT not in p.parents:
        raise FsError("path_escape_denied", 403)
    return p, rel


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTS


def list_dir(path: str = ".") -> dict:
    p, rel = _resolve(path)
    if not p.exists():
        raise FsError("not_found", 404)
    if not p.is_dir():
        raise FsError("not_a_directory", 400)
    rows = []
    try:
        for e in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            if len(rows) >= MAX_LIST:
                break
            if e.name in BLOCKED_DIRS or BLOCKED_NAME_RE.search(e.name):
                continue
            try:
                erel = e.relative_to(ROOT).as_posix()
            except ValueError:
                continue
            if any(erel.lower() == pre or erel.lower().startswith(pre + "/")
                   for pre in BLOCKED_PREFIXES):
                continue
            if e.is_dir():
                rows.append({"name": e.name, "path": erel, "type": "dir", "size": 0})
            elif e.is_file():
                rows.append({"name": e.name, "path": erel, "type": "file",
                             "size": e.stat().st_size})
    except OSError as exc:
        raise FsError("list_failed", 500, str(exc)[:160]) from exc
    return {"ok": True, "root": ".", "path": rel, "items": rows, "count": len(rows)}


def read_file(path: str) -> dict:
    p, rel = _resolve(path)
    if not p.exists():
        raise FsError("not_found", 404)
    if not p.is_file():
        raise FsError("not_a_file", 400)
    if not _is_text(p):
        raise FsError("binary_or_unsupported", 400)
    try:
        data = p.read_bytes()
    except OSError as exc:
        raise FsError("read_failed", 500, str(exc)[:160]) from exc
    truncated = len(data) > MAX_READ
    text = data[:MAX_READ].decode("utf-8", "replace")
    return {"ok": True, "path": rel, "size": len(data), "truncated": truncated,
            "content": text}


def search(query: str, glob: str = "", path: str = ".") -> dict:
    q = str(query or "").strip()
    if len(q) < 2:
        raise FsError("query_too_short", 400)
    start, rel = _resolve(path)
    if not start.exists():
        raise FsError("not_found", 404)
    if not start.is_dir():
        raise FsError("not_a_directory", 400)
    glob_low = str(glob or "").strip().lower()
    hits = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = [d for d in dirnames if d not in BLOCKED_DIRS]
        for fn in filenames:
            if len(hits) >= MAX_SEARCH_HITS or scanned >= MAX_SEARCH_FILES:
                break
            fp = Path(dirpath) / fn
            if BLOCKED_NAME_RE.search(fn) or not _is_text(fp):
                continue
            try:
                frel = fp.relative_to(ROOT).as_posix()
            except ValueError:
                continue
            if any(frel.lower() == pre or frel.lower().startswith(pre + "/")
                   for pre in BLOCKED_PREFIXES):
                continue
            if glob_low and glob_low not in frel.lower() and not fp.match(glob or "*"):
                continue
            scanned += 1
            try:
                if fp.stat().st_size > 300000:
                    continue
                text = fp.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for i, line in enumerate(text.splitlines(), 1):
                if q.lower() in line.lower():
                    hits.append({"path": frel, "line": i, "text": line[:240]})
                    if len(hits) >= MAX_SEARCH_HITS:
                        break
        if len(hits) >= MAX_SEARCH_HITS or scanned >= MAX_SEARCH_FILES:
            break
    return {"ok": True, "query": q, "path": rel, "hits": hits,
            "count": len(hits), "scanned": scanned}


def dispatch(action: str, args: dict) -> dict:
    a = str(action or "").strip()
    d = args if isinstance(args, dict) else {}
    if a == "list_dir":
        return list_dir(str(d.get("path") or "."))
    if a == "read_file":
        return read_file(str(d.get("path") or ""))
    if a == "search":
        return search(str(d.get("query") or ""), str(d.get("glob") or ""),
                      str(d.get("path") or "."))
    raise FsError("unknown_tool", 400)
