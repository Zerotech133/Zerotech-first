#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ-06 snapshot：把 HQ-01~05 只读模型合成一个静态快照。

用途：W3 实时 API 接入前的 /hq 页面数据源（portal/static/hq_snapshot.json）。
只读源数据；生成文件可随时重跑，不写 DB/审批/任务/调度。

用法：python -X utf8 tools/hq_snapshot.py [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import hq_approvals  # noqa: E402
import hq_budget  # noqa: E402
import hq_heartbeat  # noqa: E402
import hq_org  # noqa: E402
import hq_tasks  # noqa: E402

DEFAULT_OUT = ROOT / "portal" / "static" / "hq_snapshot.json"


def build_snapshot() -> dict:
    org = hq_org.build_org()
    tasks = hq_tasks.build_board()
    approvals = hq_approvals.build_approvals()
    budget = hq_budget.build_budget(days=30)
    heartbeat = hq_heartbeat.build_heartbeat()
    return {
        "schema": "hq/snapshot/v1",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True,
        "note": "HQ-06 只读数据快照（静态文件）；实时 API=/api/hq/snapshot",
        "org": {
            "summary": org["summary"],
            "chief": org["chief"],
            "departments": [
                {"dept": d["dept"], "name": d["name"], "lead": d["lead"], "n": d["n"]}
                for d in org["departments"]
            ],
            "gaps": {
                "no_hr_profile": len(org["gaps"]["no_hr_profile"]),
                "no_team_projection": len(org["gaps"]["no_team_projection"]),
            },
        },
        "tasks": {
            "summary": tasks["summary"],
            "board": {k: tasks["board"][k][:20] for k in ("doing", "review", "parked")},
        },
        "approvals": {
            "summary": approvals["summary"],
            "policy": approvals["policy"],
            "risks": approvals["risks"],
            "latest": approvals["latest"][:8],
        },
        "budget": {
            "totals": budget["totals"],
            "credits_estimate": budget["credits_estimate"],
            "budget": budget["budget"],
            "by_domain": budget["by_domain"],
            "risks": budget["risks"],
        },
        "heartbeat": {
            "summary": heartbeat["summary"],
            "schedules": heartbeat["schedules"],
            "coalesced_wakes": heartbeat["coalesced_wakes"],
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ-06 静态快照生成")
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    args = ap.parse_args(argv)
    snap = build_snapshot()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(snap, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "out": str(out), "bytes": out.stat().st_size,
                      "generated_at": snap["generated_at"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
