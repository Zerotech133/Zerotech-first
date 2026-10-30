# -*- coding: utf-8 -*-
"""D1 投研工作台数据口（2026-10-30 WO-D1-RESEARCH）。

只读聚合：ACAD1 池（策略来源）+ 当日池复盘（T1/T2/N1/F1 分析 + D1 结论）
+ 组合计划的买点/止损 + 影子账 D1 rationale/summary。
边界：D1 不管理策略，只做池内研究；不接实盘。
"""
from __future__ import annotations

import io
import json
import os
from datetime import datetime

PORTAL = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(PORTAL)
REVIEW_DIR = os.path.join(REPO, "agent_platform", "data", "pool_review")
SHADOW = os.path.join(REPO, "agent_platform", "data", "shadow", "shadow_ledger.jsonl")

import sys  # noqa: E402
if REPO not in sys.path:
    sys.path.insert(0, REPO)
from agent_platform import research_plan  # noqa: E402

import pool_data  # noqa: E402


def _read(path, default=None):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _shadow_latest(day):
    latest = {}
    if not os.path.exists(SHADOW):
        return latest
    try:
        with io.open(SHADOW, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if str(r.get("date")) == day and r.get("status") == "done":
                    latest[str(r.get("symbol"))] = r
    except OSError:
        pass
    return latest


def d1_snapshot(day=None):
    snap = pool_data.pool_snapshot(day)
    day = snap.get("date") or day
    review = _read(os.path.join(REVIEW_DIR, day + ".json"), {}) if day else {}
    plan_rows = {}
    for row in ((review.get("plan") or {}).get("rows") or []):
        plan_rows[str(row.get("code"))] = row
    shadows = _shadow_latest(day)
    items = []
    for item in snap.get("items") or []:
        code = str(item.get("code") or "")
        # 复盘记录里带 analyses/node_exec 的原始记录
        rec = next((r for r in (review.get("records") or [])
                    if str(r.get("code")) == code), {}) or item
        rec = dict(rec)
        rec.setdefault("analyses", item.get("analyses") or {})
        rec.setdefault("node_exec", item.get("node_exec") or [])
        rec.setdefault("decision_status", item.get("decision_status"))
        rec.setdefault("recommendation", item.get("recommendation"))
        rec.setdefault("confidence", item.get("confidence"))
        rec.setdefault("price", item.get("price"))
        built = research_plan.build_item(item, rec, plan_rows.get(code), shadows.get(code))
        items.append(built)
    out = research_plan.build_snapshot(items)
    out.update({
        "ok": True,
        "mode": "d1_research",
        "date": day,
        "pool_id": snap.get("pool_id"),
        "data_date": snap.get("data_date"),
        "freshness_days": snap.get("freshness_days"),
        "stale": snap.get("stale"),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dates": snap.get("dates") or [],
    })
    return out
