# -*- coding: utf-8 -*-
"""司马徽（ACAD1）推荐池 M1（2026-10-30 WO-INV-RECOMMEND-POOL）。

单一候选池：从 `portal/data/todays_picks.json` + ACAD1 策略健康台账生成每日推荐单。
- 只允许健康策略（red/maintenance 不进池）；
- 默认纳入 T1/T2（重点/高价值），T3 观察不自动进；
- 每条带来源策略/数据日/时效/状态/风险标记；
- 只研究推荐，不接下单。

输出：`agent_platform/data/recommendation_pool/<YYYY-MM-DD>.json` +
`latest.json`（原子写）。
"""
from __future__ import annotations

import io
import json
import os
from datetime import date, datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PICKS = os.path.join(REPO, "portal", "data", "todays_picks.json")
PORTFOLIO = os.path.join(REPO, "agent_platform", "data", "academy", "strategy_portfolio.json")
OUT_DIR = os.path.join(REPO, "agent_platform", "data", "recommendation_pool")
DEFAULT_TIERS = ("T1", "T2")
EXPIRY_DAYS = 3
BLOCK_HEALTH = {"red", "maintenance"}
INFRA_SOURCES = {"strong", "cross", "ninedim", "chanlun", "weak", "qpzm"}


def _read(path, default=None):
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _date(s):
    try:
        return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


def strategy_health(portfolio=None):
    d = _read(portfolio or PORTFOLIO, {}) or {}
    out = {}
    for s in d.get("strategies") or []:
        sid = s.get("id")
        if sid:
            out[sid] = {"status": s.get("status"), "health": s.get("health"),
                        "launch_status": s.get("launch_status")}
    return out


def healthy_sources(health):
    ok = set()
    for sid, h in (health or {}).items():
        # 只有明确 green/yellow 的策略才可进池；未知健康状态不猜“健康”
        if h.get("health") not in ("green", "yellow"):
            continue
        if h.get("health") in BLOCK_HEALTH:
            continue
        if h.get("launch_status") in ("maintenance", "no_go"):
            continue
        ok.add(sid)
    return ok


def _write_atomic(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def build_pool(day=None, picks_path=None, portfolio_path=None, tiers=DEFAULT_TIERS,
               write=True):
    day = day or date.today().isoformat()
    picks = _read(picks_path or PICKS, {}) or {}
    health = strategy_health(portfolio_path)
    allowed = healthy_sources(health)
    data_date = str(picks.get("scan_run_date") or picks.get("trade_basis") or "")[:10]
    dd = _date(data_date) or _date(day)
    base_day = _date(day) or date.today()
    fresh_days = (base_day - dd).days if dd else None
    expiry = (dd + timedelta(days=EXPIRY_DAYS)).isoformat() if dd else ""
    stale = bool(fresh_days is not None and fresh_days > 1)

    items = []
    seen = set()
    for s in picks.get("stocks") or []:
        code = str(s.get("code") or "").split(".")[0]
        if not code or code in seen:
            continue
        tier = str(s.get("tier") or "")
        if tier not in tiers:
            continue
        srcs = []
        for src in s.get("sources") or []:
            sid = str(src.get("s") or "")
            if sid in allowed and sid in INFRA_SOURCES and sid not in srcs:
                srcs.append(sid)
        if not srcs:
            continue
        seen.add(code)
        risk = []
        if stale:
            risk.append("stale_data")
        if s.get("env_diff"):
            risk.append("env_diff")
        if s.get("panic_buy"):
            risk.append("panic_buy")
        if s.get("q_toxic"):
            risk.append("q_toxic")
        reason = "%s · 命中 %s · n_hit=%s" % (
            tier or "—", "+".join(srcs), s.get("n_hit") or 0)
        items.append({
            "item_id": "REC-%s-%s" % (day.replace("-", ""), code),
            "code": code,
            "name": s.get("name") or "",
            "industry": s.get("industry") or "",
            "tier": tier,
            "sources": srcs,
            "reason": reason,
            "close": s.get("close"),
            "first_seen": data_date,
            "last_seen": data_date,
            "data_date": data_date,
            "freshness_days": fresh_days,
            "expiry_date": expiry,
            "status": "pending",
            "risk_flags": risk,
            "decision_ref": "",
        })
    items.sort(key=lambda x: (x["tier"] != "T1", -len(x["sources"]),
                              str(x["code"])))
    pool = {
        "schema": "acad1_recommendation_pool/v1",
        "pool_id": "ACAD1-REC-%s" % day.replace("-", ""),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "owner": "ACAD1",
        "source": "portal/data/todays_picks.json + strategy_portfolio.json",
        "data_date": data_date,
        "freshness_days": fresh_days,
        "stale": stale,
        "expiry_date": expiry,
        "tiers": list(tiers),
        "counts": {"total": len(items),
                   "by_tier": {t: sum(1 for x in items if x["tier"] == t) for t in tiers},
                   "by_source": {sid: sum(1 for x in items if sid in x["sources"]) for sid in sorted(allowed)}},
        "excluded": {"red_or_maintenance": sorted(set(health) - allowed),
                     "t3_not_in_pool": sum(1 for s in picks.get("stocks") or []
                                           if str(s.get("tier")) == "T3")},
        "items": items,
    }
    if write:
        path = os.path.join(OUT_DIR, day + ".json")
        _write_atomic(path, pool)
        _write_atomic(os.path.join(OUT_DIR, "latest.json"), pool)
        pool["path"] = path
    return pool
