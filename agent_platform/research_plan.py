# -*- coding: utf-8 -*-
"""D1 投研工作台数据合同（2026-10-30 WO-D1-RESEARCH）。

边界：股票由 ACAD1 策略池提供；D1 只做池内研究/研判/建仓计划，不管理策略。
输出每票：看多/看空论据、D1 结论、可买性、买点、止盈/止损、盯盘交棒状态。
"""
from __future__ import annotations


def _f(v, default=None):
    try:
        x = float(v)
        return x if x > 0 else default
    except (TypeError, ValueError):
        return default


def _analysis(rec, key):
    return ((rec or {}).get("analyses") or {}).get(key) or ""


def _shadow_info(shadow):
    s = (shadow or {}).get("summary") or {}
    return {
        "risk_level": s.get("risk_level") or (shadow or {}).get("risk_level"),
        "time_horizon": s.get("time_horizon"),
        "rationale": s.get("rationale") or (shadow or {}).get("rationale"),
        "l1_basis": s.get("l1_basis") or (shadow or {}).get("l1_basis"),
        "execution_modes": s.get("execution_modes"),
        "target_price": s.get("target_price"),
    }


def build_item(item, rec, plan_row=None, shadow=None, levels=None):
    item = item or {}
    rec = rec or {}
    plan = plan_row or {}
    levels = levels or {}
    price = _f(rec.get("price")) or _f(plan.get("price")) or _f((shadow or {}).get("price_at_decision"))
    recommendation = str(rec.get("recommendation") or plan.get("recommendation") or "").upper()
    confidence = _f(rec.get("confidence"))
    status = rec.get("decision_status") or (shadow or {}).get("status")
    done = status == "done"
    can_buy = bool(done and recommendation in ("BUY", "STRONG_BUY") and price)
    watch_trigger = (levels.get("watch_trigger") or plan.get("watch_trigger") or (
        {"pullback": round(price * 0.97, 2), "breakout": round(price * 1.02, 2)} if price else None))
    # WATCH/无买点时不得显示“买点区间”；只在可买时给买点。
    buy_zone = ((levels.get("buy_zone") or plan.get("buy_zone") or
                 ([round(price * 0.98, 2), round(price * 1.01, 2)] if price else None))
                if can_buy else None)
    stop = _f(levels.get("stop_loss")) or _f(plan.get("stop_loss")) or (round(price * 0.92, 2) if price else None)
    target1 = _f(levels.get("target1")) or _f(plan.get("target1")) or (round(price * 1.08, 2) if price else None)
    target2 = _f(levels.get("target2")) or _f(plan.get("target2")) or (round(price * 1.15, 2) if price else None)
    rr = round((target1 - price) / (price - stop), 2) if (price and target1 and stop and price > stop) else None

    if not done:
        action = "pending_research"
        handoff = {"to": "D1", "status": "pending", "note": "等待投研分析完成"}
    elif can_buy:
        action = "buy_ready"
        handoff = {"to": "TR1", "status": "ready",
                   "gate": "外部用户需人工确认；内部运行走 auto（paper）",
                   "buy_zone": buy_zone, "stop_loss": stop}
    elif recommendation in ("SELL", "STRONG_SELL"):
        action = "avoid"
        handoff = {"to": "none", "status": "no_trade", "note": "空头/回避"}
    else:
        action = "watch_trigger"
        handoff = {"to": "WA1/WATCH_TOWER/watchbot", "status": "armed",
                   "condition": "盯盘触发后通知交易", "watch_trigger": watch_trigger}

    info = _shadow_info(shadow)
    return {
        "code": str(item.get("code") or ""),
        "name": item.get("name") or "",
        "industry": item.get("industry") or "",
        "tier": item.get("tier") or "",
        "sources": item.get("sources") or [],
        "data_date": item.get("data_date") or "",
        "stale": bool(item.get("stale")),
        "recommendation": recommendation,
        "confidence": confidence,
        "decision_status": status,
        "price": price,
        "can_buy": can_buy,
        "action": action,
        "buy_zone": buy_zone,
        "stop_loss": stop,
        "target1": target1,
        "target2": target2,
        "risk_reward": rr,
        "watch_trigger": watch_trigger,
        "handoff": handoff,
        "bull": _analysis(rec, "T1"),
        "bear": _analysis(rec, "T2"),
        "sentiment": _analysis(rec, "N1"),
        "fundamental": _analysis(rec, "F1"),
        "rationale": info["rationale"],
        "l1_basis": info["l1_basis"],
        "risk_level": info["risk_level"],
        "time_horizon": info["time_horizon"],
        "level_source": levels.get("source") or "legacy_rule",
        "levels": {k: levels.get(k) for k in ("atr", "ma20", "support", "resistance")} if levels else {},
        "node_exec": rec.get("node_exec") or [],
        "strategy_owner": "ACAD1",
        "research_owner": "D1",
    }


def summarize(items):
    items = items or []
    counts = {"total": len(items)}
    for a in ("buy_ready", "watch_trigger", "avoid", "pending_research"):
        counts[a] = sum(1 for x in items if x.get("action") == a)
    counts["can_buy"] = sum(1 for x in items if x.get("can_buy"))
    counts["analyzed"] = sum(1 for x in items if x.get("decision_status") == "done")
    counts["armed_watch"] = sum(1 for x in items if x.get("handoff", {}).get("status") == "armed")
    return counts


def build_snapshot(items):
    items = list(items or [])
    return {"items": items, "summary": summarize(items),
            "strategy_owner": "ACAD1", "research_owner": "D1",
            "trading_permitted": False,
            "note": "策略由 ACAD1 池提供；D1 只做池内研究/建仓计划，不管理策略。"}
