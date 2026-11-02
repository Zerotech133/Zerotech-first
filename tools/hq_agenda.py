#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HQ v2：会议/议程/派单只读聚合。

复用：
- legion_command.hq_agenda（8796 会前议程：council topics/messages/scheduled）
- agent_platform/data/meetings/hq（会议纪要 v2：agenda/decisions/orders）
- legion_command.hq_stats（闭环率/跑偏）
把“会议”变成可执行议程：研发/挖掘/分析/商业分类 + owner + 决议/派单状态。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AP = ROOT / "agent_platform"
COUNCIL = ROOT / "portal" / "data"
MEETINGS = AP / "data" / "meetings" / "hq"
sys.path.insert(0, str(AP))


def _kind(text: str) -> str:
    t = str(text or "")
    if any(k in t for k in ("研发", "代码", "工单", "dev", "merge", "测试", "bug", "修复")):
        return "研发"
    if any(k in t for k in ("挖掘", "因子", "选股", "策略", "回测", "数据", "线索")):
        return "挖掘"
    if any(k in t for k in ("分析", "研究", "研判", "复盘", "报告")):
        return "分析"
    if any(k in t for k in ("商业", "客户", "定价", "销售", "订阅", "收入", "内容", "获客", "市场")):
        return "商业"
    return "其他"


def _meetings() -> list[dict]:
    out = []
    if MEETINGS.is_dir():
        for p in sorted(MEETINGS.glob("hq-*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(d, dict):
                    out.append(d)
            except Exception:  # noqa: BLE001
                continue
    return out


def build_agenda() -> dict:
    from legion_command import hq_agenda, hq_stats  # noqa: E402

    ag = hq_agenda.load_agenda(COUNCIL)
    board = hq_stats.board(MEETINGS)
    ms = _meetings()
    items = []
    for m in ms[-20:]:
        mid = m.get("meeting_id") or ""
        for a in m.get("agenda") or []:
            items.append({"source": "meeting", "meeting_id": mid,
                          "meeting_type": m.get("type"), "created_at": m.get("created_at"),
                          "item": a.get("item"), "owner": a.get("owner") or "",
                          "kind": _kind(a.get("item")), "status": "agenda"})
        for d in m.get("decisions") or []:
            items.append({"source": "decision", "meeting_id": mid,
                          "created_at": m.get("created_at"), "item": d.get("text") or d.get("item") or "",
                          "owner": d.get("owner") or "", "kind": _kind(d.get("text") or d.get("item")),
                          "status": "decided", "due": d.get("due") or ""})
        for o in m.get("orders") or []:
            items.append({"source": "order", "meeting_id": mid,
                          "created_at": m.get("created_at"), "item": o.get("desc") or o.get("item") or "",
                          "owner": o.get("owner") or o.get("agent") or "", "kind": _kind(o.get("desc") or o.get("item")),
                          "status": "dispatched" if o.get("dispatch") else "pending", "due": o.get("due") or ""})
    for t in (ag.get("topics") or [])[-20:]:
        text = t.get("title") or t.get("topic") or t.get("text") or ""
        items.append({"source": "council", "meeting_id": "", "created_at": t.get("ts") or t.get("created_at") or "",
                      "item": text, "owner": t.get("owner") or t.get("by") or "",
                      "kind": _kind(text), "status": "topic"})
    for s in (ag.get("scheduled") or [])[-10:]:
        text = s.get("topic") or s.get("title") or s.get("text") or ""
        items.append({"source": "scheduled", "meeting_id": "", "created_at": s.get("ts") or "",
                      "item": text, "owner": s.get("owner") or "", "kind": _kind(text), "status": "scheduled"})
    by_kind = {}
    for x in items:
        by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
    return {
        "schema": "hq/agenda/v1", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "read_only": True, "items": items[-80:], "meetings": board,
        "summary": {"items": len(items), "by_kind": by_kind,
                    "pending_orders": sum(1 for x in items if x["source"] == "order" and x["status"] == "pending"),
                    "meetings": len(ms)},
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="HQ 会议/议程聚合")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    d = build_agenda()
    print(json.dumps(d, ensure_ascii=False, indent=1) if args.json else
          "# HQ 议程\n\n" + "\n".join(f"- [{x['kind']}] {x['item']} · {x['owner'] or '?'} · {x['status']}" for x in d["items"][-30:]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
