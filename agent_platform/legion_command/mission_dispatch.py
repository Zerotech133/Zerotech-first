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
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/legion_command/mission_dispatch.py", exc)
    return result


# ══════════════════════════════════════════════════════════════════
# Phase 2：真实派工 + 依赖门控 + 自动评审门
# ══════════════════════════════════════════════════════════════════
import hashlib
import os
import subprocess
import sys
import logging


def _ticket_id(plan: dict, tid: str) -> str:
    h = hashlib.sha1(str(plan.get("mission_id") or "M").encode(), usedforsecurity=False).hexdigest()[:6].upper()
    return "K-MISSION-%s-%s" % (h, tid)


def _state_path(mission_id: str) -> Path:
    return MISSIONS / str(mission_id) / "state.json"


def load_state(mission_id: str) -> dict:
    p = _state_path(mission_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_state(state: dict) -> None:
    p = _state_path(state["mission_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    p.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def _run(cmd: list, cwd: Path | None = None, timeout: int = 600):
    env = dict(os.environ)
    env.setdefault("AGENT_NAME", "DSH-Lead")
    env.setdefault("DEVTOOL_AGENT", "DSH-Lead")
    env.setdefault("DEVTOOL_IDE", "DeepSeek Harness")
    env.setdefault("DEVTOOL_MODEL", "deepseek-flash")
    return subprocess.run(cmd, cwd=str(cwd or REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout, env=env)


def _create_ticket(plan: dict, task: dict) -> dict:
    tid = _ticket_id(plan, task["id"])
    # devtask 只接受已注册项目线；默认 portal，可由 plan.project 指定 quant/watchbot
    project = str(plan.get("project") or "portal")
    r = _run([sys.executable, "tools/devtask.py", "new", tid,
              str(task.get("title") or task["id"]), "--project", project])
    return {"ok": r.returncode == 0, "ticket_id": tid,
            "detail": (r.stdout + "\n" + r.stderr).strip()[-800:]}


def _has_commits(tid: str) -> bool:
    wt = REPO / ".worktrees" / tid
    if not wt.exists():
        return False
    try:
        r = _run(["git", "-C", str(wt), "rev-list", "--count", "master..HEAD"], timeout=30)
        return r.returncode == 0 and int((r.stdout or "0").strip() or 0) > 0
    except Exception:
        return False


def _schedule(state: dict, plan: dict, parallel_limit: int) -> list:
    created = []
    tasks = state.get("tasks") or {}
    active = sum(1 for t in tasks.values() if t.get("status") in ("queued", "doing"))
    slots = max(0, int(parallel_limit) - active)
    for tid, t in tasks.items():
        if slots <= 0:
            break
        if t.get("status") != "blocked":
            continue
        if all((tasks.get(d) or {}).get("status") == "done" for d in (t.get("deps") or [])):
            res = _create_ticket(plan, dict(t, id=tid))
            t["ticket_id"] = res["ticket_id"]
            t["create_detail"] = res.get("detail", "")[-400:]
            t["attempts"] = int(t.get("attempts") or 0) + 1
            if res["ok"]:
                if t.get("worker_eligible") is False:
                    t["status"] = "waiting_human"
                    t["manual_owner"] = t.get("owner") or "DEV1"
                else:
                    t["status"] = "doing"
                t["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
                created.append(tid)
                slots -= 1
            else:
                t["status"] = "blocked"
                t["last_error"] = "create_failed"
    return created


def dispatch(plan: dict, parallel_limit: int = 3, create: bool = True) -> dict:
    if not create or plan.get("valid") is False:
        return dry_run(plan, parallel_limit)
    mid = str(plan.get("mission_id") or "")
    if not mid:
        return {"ok": False, "error": "missing_mission_id"}
    state = load_state(mid)
    if not state:
        tasks = {}
        for t in plan.get("tasks") or []:
            if not isinstance(t, dict) or not t.get("id"):
                continue
            tasks[str(t["id"])] = {
                "title": t.get("title"), "owner": t.get("owner"),
                "deps": t.get("deps") or [], "write_scope": t.get("write_scope") or [],
                "acceptance": t.get("acceptance") or [], "model": t.get("model") or "auto",
                "worker_eligible": t.get("worker_eligible"),
                "status": "blocked", "attempts": 0,
            }
        try:
            plan_path = str((MISSIONS / mid / "plan.json").relative_to(REPO)).replace("\\", "/")
        except ValueError:
            plan_path = str(MISSIONS / mid / "plan.json")
        state = {"schema": "mission_state/v1", "mission_id": mid,
                 "plan_path": plan_path,
                 "parallel_limit": int(parallel_limit), "tasks": tasks,
                 "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "log": []}
    state["parallel_limit"] = int(parallel_limit)
    created = _schedule(state, plan, parallel_limit)
    state.setdefault("log", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                       "event": "dispatch", "created": created})
    _save_state(state)
    return {"ok": True, "mode": "real", "mission_id": mid, "created": created,
            "parallel_limit": parallel_limit, "state": state}


def advance(mission_id: str) -> dict:
    state = load_state(mission_id)
    if not state:
        return {"ok": False, "error": "mission_state_not_found"}
    plan_path = REPO / str(state.get("plan_path") or "")
    plan = json.loads(plan_path.read_text(encoding="utf-8")) if plan_path.exists() else {
        "mission_id": mission_id, "tasks": []}
    tasks = state.get("tasks") or {}
    merged, failed = [], []
    for tid, t in tasks.items():
        if t.get("status") not in ("queued", "doing", "review", "waiting_human"):
            continue
        ticket = t.get("ticket_id")
        if not ticket or not _has_commits(ticket):
            continue
        t["status"] = "review"
        t["review_started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        r = _run([sys.executable, "tools/devtask.py", "merge", ticket], timeout=1200)
        out = (r.stdout + "\n" + r.stderr).strip()
        t["gate_detail"] = out[-1500:]
        if r.returncode == 0:
            t["status"] = "done"
            t["done_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
            merged.append(tid)
        else:
            t["status"] = "gate_failed"
            t["last_error"] = "merge_failed"
            failed.append(tid)
    created = _schedule(state, plan, int(state.get("parallel_limit") or 3))
    state.setdefault("log", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                                       "event": "advance", "merged": merged,
                                       "failed": failed, "created": created})
    _save_state(state)
    return {"ok": True, "mission_id": mission_id, "merged": merged,
            "failed": failed, "created": created, "state": state}
