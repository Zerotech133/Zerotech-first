#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-05：heartbeat 合并唤醒只读模型 + 纯去重契约 v1。

数据源（只读）：
- agent_platform/data/schedules.json：本地调度条目
- .worktrees/tasks.json：活跃任务（doing/review）

不启停调度器、不写 schedules/tasks；只提供：
- 合并规则：同一 agent 的多个待唤醒合并为一次 heartbeat；
- 调度体检：同 agent 多 schedule、last_result 失败、活跃任务 owner 与调度冲突。

用法：python -X utf8 tools/hq_heartbeat.py [--json]
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCHEDULES = ROOT / "agent_platform" / "data" / "schedules.json"
TASKS = ROOT / ".worktrees" / "tasks.json"


def _load(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def coalesce_wakes(wakes: list[dict]) -> list[dict]:
    """同一 agent 的多个 wake 合并为一条；保留最早 due，合并 reasons/sources。"""
    merged: dict[str, dict] = {}
    for w in wakes:
        agent = str(w.get("agent") or "?")
        if agent not in merged:
            merged[agent] = {
                "agent": agent,
                "due": w.get("due"),
                "reasons": [w.get("reason") or w.get("kind") or "wake"],
                "sources": [w.get("source") or "?"],
                "count": 1,
            }
        else:
            m = merged[agent]
            m["count"] += 1
            if w.get("due") and (not m["due"] or str(w["due"]) < str(m["due"])):
                m["due"] = w["due"]
            r = w.get("reason") or w.get("kind") or "wake"
            if r not in m["reasons"]:
                m["reasons"].append(r)
            s = w.get("source") or "?"
            if s not in m["sources"]:
                m["sources"].append(s)
    return list(merged.values())


def build_heartbeat() -> dict:
    sched = _load(SCHEDULES, [])
    if not isinstance(sched, list):
        sched = []
    tasks = _load(TASKS, {})
    enabled = [s for s in sched if isinstance(s, dict) and s.get("enabled")]
    by_agent: dict[str, int] = {}
    for s in enabled:
        a = str(s.get("agent") or "?")
        by_agent[a] = by_agent.get(a, 0) + 1
    failed = [s for s in enabled if "不可达" in str(s.get("last_result") or "") or "error" in str(s.get("last_result") or "").lower()]
    active = []
    for tid, t in (tasks or {}).items():
        if isinstance(t, dict) and t.get("status") in ("doing", "review"):
            active.append({"agent": str(t.get("owner") or ""), "task_id": tid, "status": t.get("status")})
    wakes = []
    for s in enabled:
        wakes.append({"agent": str(s.get("agent") or "?"), "due": s.get("cron"), "kind": "schedule",
                      "reason": "schedule", "source": s.get("id")})
    for a in active:
        if a["agent"]:
            wakes.append({"agent": a["agent"], "due": None, "kind": "task",
                          "reason": "assignment", "source": a["task_id"]})
    merged = coalesce_wakes(wakes)
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "schedules": {
            "total": len(sched), "enabled": len(enabled),
            "by_agent": dict(sorted(by_agent.items(), key=lambda kv: -kv[1])),
            "multi_schedule_agents": [a for a, n in by_agent.items() if n > 1],
            "failed_last_result": [{"id": s.get("id"), "agent": s.get("agent"), "result": s.get("last_result")} for s in failed],
        },
        "active_tasks": active,
        "coalesced_wakes": merged,
        "summary": {
            "agents_to_wake": len(merged),
            "raw_wakes": len(wakes),
            "coalesced": len(wakes) - len(merged),
            "multi_schedule_agents": sum(1 for n in by_agent.values() if n > 1),
            "failed_schedules": len(failed),
        },
    }


def render_md(h: dict) -> str:
    s = h["schedules"]
    m = h["summary"]
    lines = [
        "# 零界 HQ · Heartbeat 合并 v1",
        "",
        f"- 生成：{h['generated_at']}｜调度 total **{s['total']}** / enabled **{s['enabled']}**",
        f"- 原始 wake **{m['raw_wakes']}** → 合并后 **{m['agents_to_wake']}**（合并 {m['coalesced']}）",
        f"- 同 agent 多调度：**{m['multi_schedule_agents']}**｜last_result 失败：**{m['failed_schedules']}**",
        "",
        "## 合并后的待唤醒（每 agent 一次）",
        "",
        "| agent | count | due | reasons | sources |",
        "|---|---:|---|---|---|",
    ]
    for w in h["coalesced_wakes"]:
        lines.append(f"| {w['agent']} | {w['count']} | {w['due'] or '-'} | {', '.join(w['reasons'])} | {', '.join(w['sources'])} |")
    lines += ["", "## 调度体检", ""]
    if s["multi_schedule_agents"]:
        lines.append("- 多调度 agent：" + "，".join(s["multi_schedule_agents"]))
    for f in s["failed_last_result"][:10]:
        lines.append(f"- 失败 {f['id']}（{f['agent']}）：{f['result']}")
    lines += ["", "## 活跃任务", ""]
    for a in h["active_tasks"]:
        lines.append(f"- {a['task_id']} · {a['status']} · owner={a['agent'] or '-'}")
    lines += ["", "> 只读模型；合并规则供 W3 调度器接入，当前不启停调度。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-05 heartbeat 合并只读模型")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    h = build_heartbeat()
    print(json.dumps(h, ensure_ascii=False, indent=1) if args.json else render_md(h), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
