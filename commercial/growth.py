# -*- coding: utf-8 -*-
"""P3 增长运营：试用/访问事件记录与转化漏斗报表（基于审计日志，无第三方依赖）。"""
from typing import Any, Dict

from . import audit

EVENTS = ("view", "trial", "login", "report", "invoice")


def record(event: str, ref: str = "", detail: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """记录增长事件（不记 PII；ref 可为页面/来源/租户）。"""
    if event not in EVENTS:
        raise ValueError("未知增长事件: " + event)
    return audit.append("growth_" + event, tenant="", actor="growth",
                        detail={"ref": ref, **(detail or {})})


def report(path: str | None = None) -> Dict[str, Any]:
    rows = audit.tail(100000, path=path)
    views: Dict[str, int] = {}
    channels: Dict[str, int] = {}
    trial_tenants = set()
    report_tenants = set()
    paid_tenants = set()
    for r in rows:
        action = str(r.get("action") or "")
        detail = r.get("detail") or {}
        if action == "growth_view":
            ref = str(detail.get("ref") or "direct")
            views[ref] = views.get(ref, 0) + 1
        elif action == "trial_started" or action == "growth_trial":
            t = r.get("tenant") or detail.get("tenant")
            if t:
                trial_tenants.add(t)
            ch = str(detail.get("channel") or "direct")
            channels[ch] = channels.get(ch, 0) + 1
        elif action == "report_generated":
            if r.get("tenant"):
                report_tenants.add(r.get("tenant"))
        elif action in ("license_gen", "invoice_generated"):
            if r.get("tenant"):
                paid_tenants.add(r.get("tenant"))
    n_trial = len(trial_tenants)
    n_active = len(trial_tenants & report_tenants)
    n_paid = len(trial_tenants & paid_tenants)
    return {
        "views": views,
        "channels": channels,
        "view_total": sum(views.values()),
        "trials": n_trial,
        "trial_active": n_active,
        "trial_paid": n_paid,
        "trial_to_active": round(n_active / n_trial * 100, 1) if n_trial else None,
        "trial_to_paid": round(n_paid / n_trial * 100, 1) if n_trial else None,
        "note": "view/试用访问；trial=试用申请；trial_active=试用期内出报告；trial_paid=试用后付费（license/invoice）",
    }
