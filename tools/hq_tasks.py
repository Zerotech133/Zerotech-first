#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-02：任务板只读模型 v1（数据源 .worktrees/tasks.json）。

- 只读：不写 tasks.json、不启停服务
- 输出：Markdown / JSON
- 指标：状态分布、WIP（doing+review）、stale（活跃>14天）、缺字段、owner 负载
用法：python -X utf8 tools/hq_tasks.py [--json]
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TASKS = ROOT / ".worktrees" / "tasks.json"
STALE_DAYS = 14


def _load_tasks() -> dict:
    try:
        d = json.loads(TASKS.read_text(encoding="utf-8-sig"))
        return d if isinstance(d, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _age_days(created: str, now: datetime) -> float | None:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            t = datetime.strptime(str(created).strip()[:19], fmt)
            return max(0.0, (now - t).total_seconds() / 86400.0)
        except Exception:  # noqa: BLE001
            continue
    return None


def build_board() -> dict:
    tasks = _load_tasks()
    now = datetime.now()
    by_status: dict[str, int] = {}
    rows = []
    owners: dict[str, int] = {}
    for tid, t in tasks.items():
        if not isinstance(t, dict):
            continue
        st = str(t.get("status") or "?")
        by_status[st] = by_status.get(st, 0) + 1
        owner = str(t.get("owner") or "")
        if owner:
            owners[owner] = owners.get(owner, 0) + 1
        age = _age_days(t.get("created") or "", now)
        rows.append({
            "id": tid,
            "status": st,
            "owner": owner,
            "created": t.get("created") or "",
            "age_days": round(age, 1) if age is not None else None,
            "branch": bool(t.get("branch")),
            "worktree": bool(t.get("worktree")),
            "desc": str(t.get("desc") or "")[:100],
        })
    active = [r for r in rows if r["status"] in ("doing", "review")]
    stale = [r for r in active if (r["age_days"] or 0) > STALE_DAYS]
    return {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "summary": {
            "total": len(rows),
            "by_status": dict(sorted(by_status.items(), key=lambda kv: (-kv[1], kv[0]))),
            "wip": len(active),
            "stale_active": len(stale),
            "unowned_active": sum(1 for r in active if not r["owner"]),
            "no_branch_active": sum(1 for r in active if not r["branch"]),
            "no_worktree_active": sum(1 for r in active if not r["worktree"]),
        },
        "board": {
            "doing": [r for r in active if r["status"] == "doing"],
            "review": [r for r in active if r["status"] == "review"],
            "parked": [r for r in rows if r["status"] == "parked"],
        },
        "top_owners": sorted(owners.items(), key=lambda kv: -kv[1])[:10],
        "stale": stale,
    }


def render_md(b: dict) -> str:
    s = b["summary"]
    lines = [
        "# 零界 HQ · 任务板 v1",
        "",
        f"- 生成：{b['generated_at']}｜总任务：**{s['total']}**｜WIP：**{s['wip']}**｜stale：**{s['stale_active']}**",
        "- 状态：" + "，".join(f"{k}={v}" for k, v in s["by_status"].items()),
        "",
        "## 进行中（doing / review）",
        "",
        "| 任务 | 状态 | owner | 年龄(天) | branch | worktree | 描述 |",
        "|---|---|---|---:|---|---|---|",
    ]
    for r in (b["board"]["doing"] + b["board"]["review"]):
        lines.append("| {id} | {st} | {owner} | {age} | {br} | {wt} | {desc} |".format(
            id=r["id"], st=r["status"], owner=r["owner"] or "-", age=r["age_days"] if r["age_days"] is not None else "-",
            br="✓" if r["branch"] else "—", wt="✓" if r["worktree"] else "—", desc=r["desc"]))
    lines += ["", "## 停放（parked）", ""]
    for r in b["board"]["parked"][:20]:
        lines.append(f"- {r['id']} · owner={r['owner'] or '-'} · {r['desc']}")
    lines += ["", "## 缺口", "",
              f"- 活跃无 owner：{s['unowned_active']}｜无 branch：{s['no_branch_active']}｜无 worktree：{s['no_worktree_active']}｜stale>{STALE_DAYS}天：{s['stale_active']}",
              "", "> 只读模型；HQ-02 的写入走原子领取契约，不直接改 tasks.json。"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-02 任务板只读模型")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    b = build_board()
    print(json.dumps(b, ensure_ascii=False, indent=1) if args.json else render_md(b), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
