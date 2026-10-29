# -*- coding: utf-8 -*-
"""任务板对账（只读）：找出「已合并未关单/无分支/提交未合并/无 worktree」等状态漂移。

用途：每日 09:30 对账（P1），把 tasks.json 状态机与 git 实际状态对齐，输出建议
（不自动改状态；关闭/合并由 owner 或 Lead 执行）。
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TASKS = REPO / ".worktrees" / "tasks.json"


def _git(*args) -> tuple[int, str]:
    r = subprocess.run(["git"] + list(args), cwd=str(REPO), capture_output=True,
                       text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "").strip()


def classify(*, status: str, branch_exists: bool, merged: bool,
             ahead: int, worktree_exists: bool, dirty: bool) -> str:
    """返回单条工单的漂移分类（纯函数，便于测试）。"""
    if status not in ("doing", "review"):
        return "closed"
    if not branch_exists:
        return "branch_missing"
    if merged:
        return "merged_but_open"
    if ahead > 0:
        return "ahead_unmerged"
    if not worktree_exists:
        return "worktree_missing"
    if dirty:
        return "worktree_dirty"
    return "clean"


def audit(repo: Path | None = None, tasks_path: Path | None = None) -> dict:
    repo = Path(repo or REPO)
    tp = Path(tasks_path or (repo / ".worktrees" / "tasks.json"))
    try:
        tasks = json.loads(tp.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"ok": False, "error": "tasks.json 不可读: %s" % e}
    rows = []
    for tid, t in tasks.items():
        status = str(t.get("status") or "?")
        if status not in ("doing", "review"):
            continue
        br = str(t.get("branch") or ("task/%s" % tid))
        rc_br, _ = _git("rev-parse", "--verify", "--quiet", br)
        branch_exists = rc_br == 0
        merged = False
        ahead = 0
        if branch_exists:
            rc_m, _ = _git("merge-base", "--is-ancestor", br, "master")
            merged = rc_m == 0
            if not merged:
                rc_c, out = _git("rev-list", "--count", "master..%s" % br)
                try:
                    ahead = int(out) if rc_c == 0 else 0
                except ValueError:
                    ahead = 0
        wt = repo / ".worktrees" / tid
        wt_exists = (wt / ".git").exists()
        dirty = False
        if wt_exists:
            rc_d, out = _git("-C", str(wt), "status", "--porcelain")
            dirty = rc_d == 0 and bool(out)
        cat = classify(status=status, branch_exists=branch_exists, merged=merged,
                       ahead=ahead, worktree_exists=wt_exists, dirty=dirty)
        rows.append({"tid": tid, "status": status, "owner": t.get("owner", "?"),
                     "branch": br, "branch_exists": branch_exists, "merged": merged,
                     "ahead": ahead, "worktree_exists": wt_exists, "dirty": dirty,
                     "category": cat})
    summary: dict[str, int] = {}
    for r in rows:
        summary[r["category"]] = summary.get(r["category"], 0) + 1
    return {"ok": True, "generated_at": __import__("time").strftime("%F %T"),
            "open_tasks": len(rows), "summary": summary, "rows": rows}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="任务板对账（只读）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--only", default="", help="只看某类：merged_but_open/ahead_unmerged/...")
    args = ap.parse_args(argv)
    rep = audit()
    if not rep.get("ok"):
        print(rep)
        return 1
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=1))
        return 0
    print("任务板对账 %s · 开单 %d" % (rep["generated_at"], rep["open_tasks"]))
    for cat, n in sorted(rep["summary"].items(), key=lambda kv: -kv[1]):
        print("  %-18s %d" % (cat, n))
    for r in rep["rows"]:
        if args.only and r["category"] != args.only:
            continue
        print("  [%s] %s owner=%s ahead=%d wt=%s dirty=%s"
              % (r["category"], r["tid"], r["owner"], r["ahead"],
                 "Y" if r["worktree_exists"] else "N", "Y" if r["dirty"] else "N"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
