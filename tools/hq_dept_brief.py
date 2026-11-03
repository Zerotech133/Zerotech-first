#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""08:00 部门早会：部门负责人早报采集 + 会议载荷生成（只读/可 dry-run）。

流程：
- 07:50 前各部门负责人提交早报（JSON）；
- 08:00 A0 汇总 13 份早报，挑出红黄/带决策事项的负责人上桌（<=6）；
- 输出可直接交给 hq_meeting.save_meeting 的载荷（默认只打印）。
"""
from __future__ import annotations

import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ORG = ROOT / "agent_platform" / "registry" / "org.json"
SCHEMA = "hq/dept_brief/v1"


def _leads() -> list[dict]:
    o = json.loads(ORG.read_text(encoding="utf-8-sig"))
    seen = set()
    out = []
    for d in o.get("departments", []):
        lead = d.get("lead")
        if not lead or lead in seen or lead == "A0":
            continue
        seen.add(lead)
        out.append({"dept": d.get("dept"), "dept_name": d.get("name"), "lead": lead})
    return out


def template(date: str | None = None) -> dict:
    date = date or datetime.date.today().isoformat()
    items = []
    for x in _leads():
        items.append({
            "dept": x["dept"], "dept_name": x["dept_name"], "lead": x["lead"],
            "legions": [], "status": "todo",
            "highlights": [], "blockers": [],
            "decision_requests": [], "evidence": [],
            "note": "请填：军团进展 / 3 条工作要点 / 阻塞 / 需 A0 决策事项（无证据标需查证）",
        })
    return {"schema": SCHEMA, "date": date, "deadline": "07:50", "items": items,
            "note": "只读模板；提交到 deliverables/daily_briefs/<date>/<lead>.json"}


def _read(path: Path) -> list[dict]:
    if path.is_dir():
        rows = []
        for p in sorted(path.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(d, dict):
                    d.setdefault("_file", p.name)
                    rows.append(d)
            except Exception:  # noqa: BLE001
                continue
        return rows
    d = json.loads(path.read_text(encoding="utf-8-sig"))
    return d.get("items") if isinstance(d, dict) and isinstance(d.get("items"), list) else [d]


def collect(path: Path) -> dict:
    leads = {x["lead"]: x for x in _leads()}
    rows = _read(path)
    valid, errors = [], []
    for r in rows:
        lead = str(r.get("lead") or "").strip()
        if lead not in leads:
            errors.append({"lead": lead, "error": "unknown_lead"})
            continue
        status = str(r.get("status") or "todo")
        if status not in ("green", "amber", "red", "todo"):
            errors.append({"lead": lead, "error": "bad_status"})
            continue
        valid.append({
            "lead": lead, "dept": leads[lead]["dept"], "dept_name": leads[lead]["dept_name"],
            "legions": r.get("legions") or [], "status": status,
            "highlights": r.get("highlights") or [], "blockers": r.get("blockers") or [],
            "decision_requests": r.get("decision_requests") or [],
            "evidence": r.get("evidence") or [],
        })
    valid.sort(key=lambda x: (x["status"] != "red", x["status"] != "amber", -len(x["decision_requests"])))
    live = ["A0"] + [x["lead"] for x in valid if x["lead"] != "A0"][:5]
    decisions = [{"lead": x["lead"], "dept": x["dept_name"], "request": str(q)[:300]}
                 for x in valid for q in x["decision_requests"]][:30]
    return {
        "schema": "hq/dept_brief_rollup/v1",
        "generated_at": datetime.datetime.now().astimezone().isoformat(timespec="seconds"),
        "read_only": True, "submitted": len(valid), "expected": len(leads),
        "errors": errors, "decision_requests": decisions,
        "live_attendees": live,
        "meeting_payload": {
            "type": "dept_morning", "attendees": live,
            "agenda": [{"item": "部门早报速览（13 份）", "owner": "A0", "minutes": 5},
                        {"item": "红黄军团/阻塞", "owner": "A0", "minutes": 5},
                        {"item": "需 A0 决策事项", "owner": "A0", "minutes": 5},
                        {"item": "今日决策清单", "owner": "A0", "minutes": 5}],
            "transcript": [{"by": x["lead"], "text": "；".join(x["highlights"])[:1900] or "（未填工作要点）",
                            "src": "brief", "evidence": x["evidence"][:10]} for x in valid],
            "decisions": [],
        },
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="08:00 部门早会早报/汇总")
    ap.add_argument("--template", action="store_true")
    ap.add_argument("--collect", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    if args.template:
        d = template()
    elif args.collect:
        d = collect(Path(args.collect))
    else:
        ap.error("need --template or --collect")
    text = json.dumps(d, ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print("wrote", args.out)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
