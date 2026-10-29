# -*- coding: utf-8 -*-
"""IDE 受控编辑（R1）：读/写 worktree 内文件 + 变更摘要（不碰 git index）。"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from . import rw_guard as G
import logging

MAX_READ = G.MAX_FILE_BYTES


def read_file(worktree: Path, rel: str) -> dict:
    path, error = G.safe_path(worktree, rel)
    if error:
        return error
    try:
        data = path.read_bytes()
    except OSError as exc:
        return G.err("read_failed", "读取失败：%s" % type(exc).__name__)
    if len(data) > MAX_READ:
        return G.err("too_large", "文件超过 %d KB，拒绝在线编辑" % (MAX_READ // 1024))
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return G.err("not_utf8", "非 UTF-8 文件，拒绝在线编辑")
    return {
        "ok": True,
        "path": str(Path(rel).as_posix()),
        "content": text,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def write_file(worktree: Path, rel: str, content: str) -> dict:
    path, error = G.safe_path(worktree, rel)
    if error:
        return error
    if not isinstance(content, str):
        return G.err("bad_content", "content 必须是字符串")
    raw = content.encode("utf-8")
    if len(raw) > G.MAX_FILE_BYTES:
        return G.err("too_large", "内容超过 %d KB" % (G.MAX_FILE_BYTES // 1024))
    existed = path.is_file()
    tmp = path.with_name(path.name + ".tmp-ide")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        return G.err("write_failed", "写入失败：%s" % type(exc).__name__)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_ide/writer.py", exc)
    return {
        "ok": True,
        "path": str(Path(rel).as_posix()),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "created": not existed,
        "diff": diff_stat(worktree, rel),
    }


def diff_stat(worktree: Path, rel: str = "") -> dict:
    """git diff --stat（只读；失败返回空，不挡写）。"""
    args = ["git", "-C", str(worktree), "diff", "--stat"]
    if rel:
        args.append("--")
        args.append(str(Path(rel).as_posix()))
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        return {
            "stat": (proc.stdout or "").strip()[:2000],
            "untracked_hint": "（未跟踪文件不出现在 diff，用 git status 看）"
            if not (proc.stdout or "").strip()
            else "",
        }
    except (OSError, subprocess.SubprocessError):
        return {"stat": "", "error": "git_diff_failed"}
