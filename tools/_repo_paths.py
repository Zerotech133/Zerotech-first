# -*- coding: utf-8 -*-
"""_repo_paths.py — 统一「主工作树根」解析（linked worktree 回落主仓）。

AGENTS.md §3：每个 Agent 在独立 worktree 干活；但 zhuge 席位、tasks.json、
work/ 产物在**主工作树**（worktree 内被 .gitignore/不存在）。各工具统一用本函数，
避免在 worktree 内写幻影路径并打印成功（审计 A-M2）。
"""
from __future__ import annotations

from pathlib import Path


def repo_root(start=None) -> Path:
    """返回主工作树根：普通 checkout 返回自身；linked worktree 解析 .git 文件回主仓。"""
    p = Path(start or __file__).resolve()
    base = p if p.is_dir() else p.parent
    for cand in (base, *base.parents):
        g = cand / ".git"
        try:
            if g.is_file():
                txt = g.read_text(encoding="utf-8", errors="replace").strip()
                if txt.lower().startswith("gitdir:"):
                    raw = txt.split(":", 1)[1].strip()
                    gitdir = Path(raw)
                    if not gitdir.is_absolute():
                        gitdir = (cand / gitdir).resolve()
                    # <main>/.git/worktrees/<name> → parents[2] 即 <main>
                    try:
                        return gitdir.parents[2]
                    except IndexError:
                        return cand
                return cand
            if g.is_dir():
                return cand
        except OSError:
            continue
    return Path(__file__).resolve().parents[1]
