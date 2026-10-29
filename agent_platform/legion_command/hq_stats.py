# -*- coding: utf-8 -*-
"""会议看板（P2）：闭环率/跑偏检测/回放（只读）。"""
from __future__ import annotations

import json
import time
from pathlib import Path


def _meetings(dirpath) -> list[dict]:
    d = Path(dirpath)
    out = []
    if not d.exists():
        return out
    for p in sorted(d.glob("hq-*.json")):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict):
            out.append(doc)
    return out


def _age_hours(created: str) -> float:
    try:
        t = time.mktime(time.strptime(created, "%Y-%m-%dT%H:%M:%S"))
        return max(0.0, (time.time() - t) / 3600.0)
    except (ValueError, TypeError):
        return 0.0


def board(dirpath) -> dict:
    ms = _meetings(dirpath)
    by_type: dict[str, int] = {}
    by_status: dict[str, int] = {}
    decisions = orders = dispatched = 0
    drift = {"draft_stale": 0, "missing_owner": 0, "missing_due": 0,
             "orders_pending": 0, "decisions_without_orders": 0}
    replay = []
    for m in ms:
        typ = str(m.get("type") or "?")
        st = str(m.get("status") or "?")
        by_type[typ] = by_type.get(typ, 0) + 1
        by_status[st] = by_status.get(st, 0) + 1
        ds = m.get("decisions") or []
        os_ = m.get("orders") or []
        decisions += len(ds)
        orders += len(os_)
        dispatched += sum(1 for o in os_ if o.get("dispatch"))
        age = _age_hours(str(m.get("created_at") or ""))
        if st == "draft" and age > 24:
            drift["draft_stale"] += 1
        for d in ds:
            if not d.get("owner"):
                drift["missing_owner"] += 1
            if not d.get("due"):
                drift["missing_due"] += 1
                drift["decisions_without_orders"] += 1 if not os_ else 0
        if ds and not os_:
            drift["decisions_without_orders"] += 1
        drift["orders_pending"] += sum(1 for o in os_ if not o.get("dispatch"))
        replay.append({
            "meeting_id": m.get("meeting_id", ""),
            "type": typ,
            "status": st,
            "created_at": m.get("created_at", ""),
            "attendees": m.get("attendees") or [],
            "decisions": len(ds),
            "orders": len(os_),
        })
    replay = replay[-20:][::-1]
    return {
        "ok": True,
        "total": len(ms),
        "by_type": by_type,
        "by_status": by_status,
        "decisions": decisions,
        "orders": orders,
        "dispatched": dispatched,
        "closure_rate": round(dispatched / decisions, 3) if decisions else 0.0,
        "drift": drift,
        "replay": replay,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "闭环率=已派单订单/决议数（观察口径）；跑偏=草稿超24h/缺负责人/缺截止/订单未派/决议无订单。",
    }
