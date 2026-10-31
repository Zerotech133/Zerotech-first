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
from agent_platform import technical_levels  # noqa: E402

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




def _team_snapshot():
    """D1 部门编制：投研会 5 席 + 盯盘组 + 交易组 + 观察席。"""
    org = _read(os.path.join(REPO, "agent_platform", "registry", "org.json"), {}) or {}
    invest = next((d for d in org.get("departments") or []
                   if d.get("dept") == "invest"), {})
    prof = {p.get("agent_id"): p for p in invest.get("agent_profiles") or []}
    meeting = _read(os.path.join(REPO, "agent_platform", "registry",
                                 "d1_meeting.json"), {}) or {}

    def row(aid):
        p = prof.get(aid) or {}
        name = p.get("cn_name") or (p.get("persona") or {}).get("name") or aid
        return {"agent_id": aid, "name": name,
                "role": p.get("role_profile") or "",
                "reports_to": p.get("reports_to") or "",
                "team_role": p.get("team_role") or ""}

    return {
        "core": [row(x) for x in ("T1", "T2", "N1", "F1")],
        "chair": row("D1"),
        "watch": [row(x) for x in ("WA1", "WA2", "WA3")],
        "trading": [row(x) for x in ("TR1", "TR2", "TR3")],
        "observer": [row(x) for x in ("ST1", "QS1")],
        "meeting": meeting,
    }


def _merge_record(rec, item):
    """池复盘记录缺值/显式 None 时回退到池 item；空 analyses 也回退。"""
    rec = dict(rec or {})
    item = item or {}
    for f in ("analyses", "node_exec"):
        if not rec.get(f):
            rec[f] = item.get(f) or ({} if f == "analyses" else [])
    for f in ("decision_status", "recommendation", "confidence", "price", "action"):
        if rec.get(f) is None:
            rec[f] = item.get(f)
    return rec


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
        rec = _merge_record(rec, item)
        _price = rec.get("price") or item.get("price")
        _rec = str(rec.get("recommendation") or "").upper()
        _can_buy = (rec.get("decision_status") == "done" and _rec in ("BUY", "STRONG_BUY"))
        try:
            _levels = technical_levels.derive(code, price=_price, can_buy=_can_buy)
        except Exception:  # noqa: BLE001 数据缺失回退旧计划
            _levels = {}
        built = research_plan.build_item(item, rec, plan_rows.get(code),
                                         shadows.get(code), levels=_levels)
        items.append(built)
    out = research_plan.build_snapshot(items)
    out["team"] = _team_snapshot()
    out["watch_methods"] = _read(os.path.join(REPO, "agent_platform", "registry",
                                     "watch_methods.json"), {})
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
