#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主仓 core.bare 自愈守卫（2026-10-31 事故 · 关羽 P110）。

背景：2026-10-31 01:46/01:49，主仓 `.git/config` 的 `core.bare` 被外部改为 `true`
（仓库内未发现写入者，疑并行会话手工 git config），导致：
  - `git -C <worktree> merge` 报 “this operation must be run in a work tree”；
  - 所有 worktree 的合并/追平被卡，devtask 门却先跑测试、报错不直观。
本模块提供只读检测 + 最小自愈（core.bare 改回 false，不改其他配置），
供 devtask merge / merge_preflight 在入口处调用。

用法：
  python tools/git_worktree_guard.py [--repo PATH] [--check]
--check 只检测：bare → 退出码 1；非 bare → 0。
默认（无 --check）遇到 bare 自动修复并打印。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace")


def is_bare(repo=REPO) -> bool:
    r = _git(repo, "config", "--get", "core.bare")
    return (r.stdout or "").strip().lower() == "true"


def repair(repo=REPO) -> bool:
    """把 core.bare 改回 false（最小修复，不动其它配置）；返回是否已修复。"""
    _git(repo, "config", "core.bare", "false")
    return not is_bare(repo)


def ensure_worktree(repo=REPO, log=print) -> bool:
    """入口守卫：主仓被误判 bare 时自动修复；返回最终是否可继续。"""
    if not is_bare(repo):
        return True
    log("[WARN] 主仓被判定为 bare（core.bare=true）——疑似外部误改；"
        "自动修复为 false（只改这一项，不动其它配置）")
    ok = repair(repo)
    if ok:
        log("[OK] core.bare=false，worktree 操作恢复")
    else:
        log("[ERR] core.bare 修复失败；请手工执行: git -C <repo> config core.bare false")
    return ok


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="主仓 core.bare 自愈守卫")
    ap.add_argument("--repo", default=str(REPO))
    ap.add_argument("--check", action="store_true", help="只检测，不修复")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    if args.check:
        bare = is_bare(repo)
        print("bare" if bare else "worktree-ok")
        return 1 if bare else 0
    return 0 if ensure_worktree(repo) else 1


if __name__ == "__main__":
    sys.exit(main())
