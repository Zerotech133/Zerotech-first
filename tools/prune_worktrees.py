# -*- coding: utf-8 -*-
"""prune_worktrees.py — worktree 自动回收（默认 dry-run）。

为什么需要：2026-10-23 上午我回收了 4 棵，同一时段其他会话新建 7 棵，
**9 → 16 净增**。一次性清理无效，必须靠定期执行。

判据（三条全满足才建议回收）：
  1. 工作区干净：`git status --porcelain` 为空
  2. 提交已并入主分支：`git rev-list --count <主分支>..<HEAD>` == 0
  3. 空闲超过 --days 天（按该树最后一次提交时间算）

⚠️ `git worktree remove` 是**永久删除**（不走回收站）——这一点 2026-10-24 已实测确认：
   回收站本身可用（SHFileOperationW 能进），但 git 的删除不经它。所以：
     · 默认 dry-run，只打印
     · 只有加 --apply 才真删
     · 删除前会再验一次 clean + merged（防 race）

用法：
    python -B tools/prune_worktrees.py                 # dry-run
    python -B tools/prune_worktrees.py --days 7
    python -B tools/prune_worktrees.py --apply         # 真删
"""
from __future__ import annotations

import argparse
import datetime as dt
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _git(*args: str) -> tuple[int, str]:
    p = subprocess.run(["git", "-C", str(REPO), *args],
                       capture_output=True, timeout=300, check=False)
    raw = (p.stdout or b"") + (p.stderr or b"")
    for enc in ("utf-8", "gbk"):
        try:
            return p.returncode, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return p.returncode, raw.decode("utf-8", "replace")


def main_worktree() -> Path:
    rc, out = _git("rev-parse", "--show-toplevel")
    return Path(out.strip()) if rc == 0 else REPO


def list_worktrees() -> list[Path]:
    rc, out = _git("worktree", "list", "--porcelain")
    if rc != 0:
        return []
    paths = []
    for line in out.splitlines():
        if line.startswith("worktree "):
            paths.append(Path(line[len("worktree "):].strip()))
    return paths


def branch_of(wt: Path) -> str:
    rc, out = subprocess.run(["git", "-C", str(wt), "rev-parse", "--abbrev-ref", "HEAD"],
                             capture_output=True, timeout=120, check=False).stdout.decode(
        "utf-8", "replace").strip(), ""
    return out if rc is not None else ""


def classify(wt: Path, main_branch: str, days: float) -> dict:
    def run(*a: str) -> str:
        p = subprocess.run(["git", "-C", str(wt), *a], capture_output=True,
                           timeout=180, check=False)
        return p.stdout.decode("utf-8", "replace").strip()

    dirty = len([x for x in run("status", "--porcelain").splitlines() if x.strip()])
    head = run("rev-parse", "--short", "HEAD")
    ahead = 0
    try:
        ahead = int(run("rev-list", "--count", f"{main_branch}..{head}") or 0)
    except ValueError:
        ahead = -1
    last = run("log", "-1", "--format=%cI")
    idle_days = None
    if last:
        try:
            when = dt.datetime.fromisoformat(last)
            idle_days = round((dt.datetime.now(when.tzinfo) - when).total_seconds() / 86400.0, 1)
        except ValueError:
            pass
    prunable = (dirty == 0 and ahead == 0
                and idle_days is not None and idle_days >= days)
    return {"path": wt, "head": head, "dirty": dirty, "ahead": ahead,
            "last": last, "idle_days": idle_days, "prunable": prunable}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="worktree 回收（默认 dry-run）")
    ap.add_argument("--days", type=float, default=7.0, help="空闲天数阈值（默认 7）")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认只打印）")
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")

    main_wt = main_worktree().resolve()
    rc, out = _git("rev-parse", "--abbrev-ref", "HEAD")
    main_branch = out.strip() if rc == 0 else "master"

    trees = [p for p in list_worktrees() if p.resolve() != main_wt]
    print(f"主工作树: {main_wt}")
    print(f"主分支  : {main_branch}")
    print(f"其它 worktree: {len(trees)} 棵   阈值: 空闲 >= {a.days:g} 天")
    print()

    rows = [classify(p, main_branch, a.days) for p in trees]
    prunable = [r for r in rows if r["prunable"]]
    keep = [r for r in rows if not r["prunable"]]

    for r in rows:
        tag = "可回收" if r["prunable"] else "保留"
        print(f"  [{tag}] {r['path'].name:<34} head={r['head']:<10} dirty={r['dirty']:<2} "
              f"未并入={r['ahead']:<3} 空闲={r['idle_days']}天")
    print()
    print(f"可回收 {len(prunable)} 棵 / 保留 {len(keep)} 棵")

    if not a.apply:
        print()
        print("dry-run：未删除任何东西。加 --apply 才真删")
        print("注意：--apply 是永久删除（不走回收站）；删除前会再验一次 clean+merged")
        return 0

    removed = failed = 0
    for r in prunable:
        # 删除前再验一次（防 race：别的会话可能刚往这棵树里写了东西）
        recheck = classify(r["path"], main_branch, a.days)
        if not recheck["prunable"]:
            print(f"  跳过 {r['path'].name}：复查已不满足条件")
            failed += 1
            continue
        rc, out = _git("worktree", "remove", str(r["path"]), "--force")
        if rc == 0:
            removed += 1
            print(f"  已回收 {r['path'].name}")
        else:
            failed += 1
            print(f"  失败 {r['path'].name}: {out.strip()[:100]}")
    print()
    print(f"回收 {removed} 棵，失败 {failed} 棵")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
