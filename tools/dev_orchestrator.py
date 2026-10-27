# -*- coding: utf-8 -*-
"""研发编排状态机 MVP（O1 · 2026-10-27 WO-HARNESS-O12）。

只读 tasks.json，维护 `work/orchestrator/state.json`（goals/tasks_context/
next_steps/decisions_made/updated_at）；提供：
  --init   创建状态文件
  --status 打印当前状态与建议（JSON）
  --run    扫描并更新状态文件 + 打印建议（**不自动派工/不自动合并**）

边界：只读 tasks.json；只写 work/orchestrator/state.json；不改 org/工单/服务。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / ".worktrees" / "tasks.json"
STATE = ROOT / "work" / "orchestrator" / "state.json"
DEFAULT_GOALS = ["按研发方案总览 C→A→B 推进；P0 派工真实化"]


def load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def load_tasks(path=TASKS):
    p = Path(path)
    if not p.exists():
        return {}
    raw = p.read_text(encoding="utf-8")
    while True:
        try:
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError as e:
            i = raw.rfind(",", 0, e.pos)
            if i < 0:
                return {}
            raw = raw[:i] + raw[i + 1:]


def load_state(path=STATE):
    st = load_json(path, {})
    if not isinstance(st, dict):
        st = {}
    st.setdefault("version", 1)
    st.setdefault("goals", list(DEFAULT_GOALS))
    st.setdefault("tasks_context", {})
    st.setdefault("next_steps", [])
    st.setdefault("decisions_made", [])
    return st


def save_state(st, path=STATE):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    st = dict(st)
    st["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    p.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n",
                 encoding="utf-8")
    return st


def _ts(s):
    try:
        return time.mktime(time.strptime(str(s)[:19], "%Y-%m-%d %H:%M:%S"))
    except Exception:  # noqa: BLE001
        return None


def _deps(task):
    raw = task.get("deps") or []
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
    return [str(x) for x in raw if str(x).strip()] if isinstance(raw, list) else []


def compute_status(tasks, state=None, now=None, max_concurrent=4,
                   stale_hours=24):
    now = now or time.time()
    st = state if isinstance(state, dict) else {}
    done = {tid for tid, t in tasks.items()
            if isinstance(t, dict) and t.get("status") == "done"}
    doing = [(tid, t) for tid, t in tasks.items()
             if isinstance(t, dict) and t.get("status") in ("doing", "review")]
    in_progress, blocked, ready, stale = [], [], [], []
    for tid, t in doing:
        deps = _deps(t)
        missing = [d for d in deps if d not in done]
        cur = {"id": tid, "owner": t.get("owner") or "",
               "status": t.get("status") or "", "deps": deps}
        in_progress.append(cur)
        if missing:
            blocked.append({"id": tid, "reason": "deps_pending",
                            "deps": missing})
        else:
            ready.append(cur)
        created = _ts(t.get("created"))
        if created and (now - created) / 3600.0 > stale_hours:
            stale.append({"id": tid, "created": t.get("created"),
                          "age_hours": round((now - created) / 3600.0, 1)})
    recent_done = []
    for tid, t in tasks.items():
        if isinstance(t, dict) and t.get("status") == "done" and t.get("merged"):
            recent_done.append((str(t.get("merged")), tid))
    recent_done.sort(reverse=True)
    snapshot = {
        "goals": list(st.get("goals") or DEFAULT_GOALS),
        "tasks_context": {
            "in_progress": [r["id"] for r in in_progress],
            "blocked": [{"id": b["id"], "reason": b["reason"],
                         "deps": b["deps"]} for b in blocked],
            "recently_completed": [tid for _, tid in recent_done[:5]],
        },
        "next_steps": [r["id"] for r in ready],
        "decisions_made": list(st.get("decisions_made") or []),
    }
    return {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
        "capacity": {"max_concurrent": max_concurrent,
                     "in_flight": len(in_progress),
                     "over_capacity": len(in_progress) > max_concurrent},
        "in_progress": in_progress,
        "ready": ready,
        "blocked": blocked,
        "stale": stale,
        "snapshot": snapshot,
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description="研发编排状态机 MVP（只读+状态文件）")
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--state", default=str(STATE))
    ap.add_argument("--max-concurrent", type=int, default=4)
    ap.add_argument("--stale-hours", type=int, default=24)
    ap.add_argument("--goal", action="append", default=[])
    args = ap.parse_args(argv)
    st = load_state(args.state)
    if args.goal:
        st["goals"] = args.goal
    if args.init:
        st = save_state(st, args.state)
        print("state -> %s" % args.state)
        return 0
    rep = compute_status(load_tasks(), st, max_concurrent=args.max_concurrent,
                         stale_hours=args.stale_hours)
    if args.run:
        st.update(rep["snapshot"])
        save_state(st, args.state)
    print(json.dumps(rep, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
