#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""W0 止血清单（只读）：doing 31 / worktree 42 的可审计处置清单。

不删任何 worktree、不改任务状态；只输出 CSV/JSON/摘要，供 owner 拍板。
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GIT = ["git", "--git-dir", str(ROOT / ".git"), "--work-tree", str(ROOT)]
TASKS = ROOT / ".worktrees" / "tasks.json"
OUT = ROOT / "deliverables" / "w0_cleanup"


def _read_tasks() -> dict:
    try:
        d = json.loads(TASKS.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return d if isinstance(d, dict) else {}


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 20) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=str(cwd) if cwd else None, capture_output=True,
                           text=True, encoding="utf-8", errors="replace", timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except Exception as e:  # noqa: BLE001
        return 1, type(e).__name__ + ":" + str(e)[:160]


def _worktrees() -> list[dict]:
    rc, out = _run(GIT + ["worktree", "list", "--porcelain"])
    rows, cur = [], {}
    if rc != 0:
        return rows
    for line in out.splitlines():
        if line.startswith("worktree "):
            if cur:
                rows.append(cur)
            cur = {"path": line[9:].strip()}
        elif line.startswith("HEAD "):
            cur["head"] = line[5:].strip()
        elif line.startswith("branch "):
            cur["branch"] = line[7:].strip()
    if cur:
        rows.append(cur)
    # 主工作区不是待清理 worktree
    root = str(ROOT.resolve())
    return [w for w in rows if str(Path(w["path"]).resolve()) != root]


def _classify(wt: dict, tasks: dict) -> dict:
    path = Path(wt["path"])
    name = path.name
    task = tasks.get(name) or {}
    exists = path.is_dir()
    dirty = 0
    merged = False
    if exists:
        rc, out = _run(["git", "-C", str(path), "status", "--porcelain"], timeout=20)
        dirty = len([x for x in out.splitlines() if x.strip()]) if rc == 0 else -1
        rc, _ = _run(["git", "-C", str(path), "merge-base", "--is-ancestor", "HEAD", "master"], timeout=20)
        merged = rc == 0
    if not exists:
        action = "missing_cleanup"
    elif task and task.get("status") == "doing":
        action = "active_task_review"
    elif merged and dirty == 0:
        action = "merged_cleanup"
    elif dirty > 0:
        action = "dirty_review"
    else:
        action = "stale_review"
    return {**wt, "name": name, "exists": exists, "dirty": dirty, "merged": merged,
            "task_status": task.get("status", ""), "owner": task.get("owner", ""),
            "action": action}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="W0 止血清单（只读）")
    ap.add_argument("--out", default=str(OUT / datetime.date.today().isoformat()))
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tasks = _read_tasks()
    doing = [(k, v) for k, v in tasks.items() if isinstance(v, dict) and v.get("status") == "doing"]
    wts = [_classify(w, tasks) for w in _worktrees()]
    registered = {Path(w["path"]).name for w in wts}
    for d in sorted((ROOT / ".worktrees").glob("*")):
        # 只有真正的 git worktree（.git 文件/目录）才纳入；lock/temp 目录不算
        if d.is_dir() and d.name not in registered and (d / ".git").exists():
            wts.append({"path": str(d), "name": d.name, "branch": "", "head": "", "exists": True,
                        "dirty": -1, "merged": False,
                        "task_status": (tasks.get(d.name) or {}).get("status", ""),
                        "owner": (tasks.get(d.name) or {}).get("owner", ""),
                        "action": "unregistered_review"})
    with (out / "doing.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["task_id", "owner", "created", "worktree", "desc"])
        for k, v in doing:
            w.writerow([k, v.get("owner", ""), v.get("created", ""), v.get("worktree", ""), str(v.get("desc") or "")[:300]])
    with (out / "worktrees.csv").open("w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "branch", "head", "exists", "dirty", "merged", "task_status", "owner", "action"])
        for x in wts:
            w.writerow([x["name"], x.get("branch", ""), str(x.get("head", ""))[:8], x["exists"], x["dirty"],
                        x["merged"], x["task_status"], x["owner"], x["action"]])
    summary = {
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "doing": len(doing),
        "worktrees": len(wts),
        "worktree_actions": {},
        "doing_owners": {},
        "targets": {"doing": 8, "worktree": 20, "parked": 8},
    }
    for x in wts:
        summary["worktree_actions"][x["action"]] = summary["worktree_actions"].get(x["action"], 0) + 1
    for _k, v in doing:
        o = v.get("owner", "unknown")
        summary["doing_owners"][o] = summary["doing_owners"].get(o, 0) + 1
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (out / "README.md").write_text(
        "# W0 止血清单（只读）\n\n"
        "- 生成：%s\n- 目标：doing≤8 / worktree≤20 / parked≤8\n- 实际：doing=%d / worktree=%d\n\n"
        "## 文件\n\n- doing.csv：31 个 doing 任务（owner/创建时间/worktree）\n- worktrees.csv：worktree 处置建议\n- summary.json：统计\n\n"
        "## 纪律\n\n本目录只做清单，不删 worktree、不改任务状态；处置必须 owner 确认。\n"
        % (summary["generated_at"], len(doing), len(wts)), encoding="utf-8")
    if a.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
