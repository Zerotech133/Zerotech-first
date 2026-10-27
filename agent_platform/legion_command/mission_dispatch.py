# -*- coding: utf-8 -*-
"""Mission Dispatcher（阶段1）：只做 dry-run 派工计划，不建 worktree、不改代码。"""
from __future__ import annotations

import json
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MISSIONS = REPO / "work" / "missions"


def _topo(tasks: dict) -> tuple:
    order, done = [], set()
    remain = dict(tasks)
    while remain:
        ready = [k for k, t in remain.items() if all(d in done for d in (t.get("deps") or []))]
        if not ready:
            return order, list(remain)
        for k in ready:
            order.append(k)
            done.add(k)
            del remain[k]
    return order, []


def _overlap(a: str, b: str) -> bool:
    x = str(a).rstrip("/*")
    y = str(b).rstrip("/*")
    return bool(x and y and (x == y or x.startswith(y + "/") or y.startswith(x + "/")))


def dry_run(plan: dict, parallel_limit: int = 3) -> dict:
    tasks = {}
    for t in plan.get("tasks") or []:
        if isinstance(t, dict) and t.get("id"):
            tasks[str(t["id"])] = t
    order, cycle = _topo(tasks)
    rows = []
    for i, tid in enumerate(order, 1):
        t = tasks[tid]
        rows.append({
            "step": i,
            "task_id": tid,
            "title": t.get("title"),
            "owner": t.get("owner"),
            "deps": t.get("deps") or [],
            "write_scope": t.get("write_scope") or [],
            "acceptance": t.get("acceptance") or [],
            "model": t.get("model") or "auto",
            "ticket_id": "K-MISSION-%s-%s" % (plan.get("mission_id", "M"), tid),
            "command": "python tools/devtask.py new K-MISSION-%s-%s \"%s\" --project mission"
                       % (plan.get("mission_id", "M"), tid, str(t.get("title") or "")[:60]),
            "status": "dry-run",
        })
    # scope conflicts
    conflicts = []
    ids = list(tasks)
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            for sa in tasks[a].get("write_scope") or []:
                for sb in tasks[b].get("write_scope") or []:
                    if _overlap(sa, sb):
                        conflicts.append({"a": a, "b": b, "scope_a": sa, "scope_b": sb})
    # simple wave grouping
    waves = []
    levels = {}
    for tid in order:
        deps = tasks[tid].get("deps") or []
        levels[tid] = 0 if not deps else max(levels.get(d, 0) + 1 for d in deps)
    for lv in sorted(set(levels.values())):
        waves.append([tid for tid in order if levels[tid] == lv])
    result = {
        "schema": "mission_dry_run/v1",
        "mission_id": plan.get("mission_id"),
        "generated": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": "dry-run",
        "parallel_limit": int(parallel_limit),
        "order": order,
        "waves": waves,
        "rows": rows,
        "conflicts": conflicts,
        "cycle": cycle,
        "ok": not cycle and (plan.get("valid") is not False),
        "note": "dry-run 只生成计划；未创建 worktree / 工单，未修改代码",
    }
    try:
        p = MISSIONS / str(plan.get("mission_id")) / "dry_run.json"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(result, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    except Exception:
        pass
    return result
