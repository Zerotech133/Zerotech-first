# -*- coding: utf-8 -*-
"""ACAD1（司马徽）推荐池只读数据口（2026-10-30 WO-INV-POOL-UI）。

聚合每日推荐池 + 当日全池复盘记录，供 `/acad1_pool.html` 展示。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime

PORTAL = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PORTAL)
POOL_DIR = os.path.join(REPO, "agent_platform", "data", "recommendation_pool")
REVIEW_DIR = os.path.join(REPO, "agent_platform", "data", "pool_review")


def _read(path, default=None):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def available_dates():
    try:
        out = [f[:-5] for f in os.listdir(POOL_DIR)
               if f.startswith("20") and f.endswith(".json")]
        return sorted(out, reverse=True)[:14]
    except OSError:
        return []


def pool_snapshot(day=None):
    day = day or (available_dates()[0] if available_dates() else "")
    pool_path = os.path.join(POOL_DIR, day + ".json") if day else ""
    pool = _read(pool_path, {}) or {}
    review_path = os.path.join(REVIEW_DIR, day + ".json") if day else ""
    review = _read(review_path, {}) or {}
    recs = {}
    for r in review.get("records") or []:
        recs[str(r.get("code") or "")] = r
    items = []
    for x in pool.get("items") or []:
        code = str(x.get("code") or "")
        r = recs.get(code) or {}
        items.append({
            "code": code,
            "name": x.get("name") or r.get("name") or "",
            "industry": x.get("industry") or "",
            "tier": x.get("tier") or "",
            "sources": x.get("sources") or [],
            "data_date": x.get("data_date") or "",
            "freshness_days": x.get("freshness_days"),
            "expiry_date": x.get("expiry_date") or "",
            "risk_flags": x.get("risk_flags") or [],
            "recommendation": r.get("recommendation"),
            "confidence": r.get("confidence"),
            "price": r.get("price"),
            "decision_status": r.get("decision_status"),
            "action": r.get("action") or "pending_analysis",
            "analyses": r.get("analyses") or {},
        })
    summary = review.get("summary") or {}
    if not summary:
        summary = {
            "pool_total": len(items),
            "reviewed": sum(1 for x in items if x["decision_status"] == "done"),
            "pending": sum(1 for x in items if x["decision_status"] != "done"),
            "by_action": {a: sum(1 for x in items if x["action"] == a)
                          for a in ("buy", "watch", "avoid", "pending_analysis")},
        }
    return {
        "ok": True,
        "mode": "acad1_pool_review",
        "trading_permitted": False,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "date": day,
        "pool_id": pool.get("pool_id"),
        "owner": pool.get("owner") or "ACAD1",
        "data_date": pool.get("data_date"),
        "freshness_days": pool.get("freshness_days"),
        "stale": bool(pool.get("stale")),
        "summary": summary,
        "plan": {
            "selected": len((review.get("plan") or {}).get("selected") or []),
            "watch": len((review.get("plan") or {}).get("watch") or []),
            "avoid": len((review.get("plan") or {}).get("avoid") or []),
            "total_weight": (review.get("plan") or {}).get("total_weight"),
        },
        "items": items,
        "dates": available_dates(),
    }
