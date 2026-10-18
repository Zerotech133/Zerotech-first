"""run_pre_market_job 必须在建计划前先跑一次 run_plan_expiry。

前一会话遗留的 APPROVED/PENDING（created < 今日）若不先过期，会整段当日仍占
sim_max_positions 额度，致本轮 pre_market 早退（0 成交窝案的 1-session 滞后残余）。
此测锁死「先过期、后建计划」的顺序，防未来误删前置过期或调换顺序。
"""
from __future__ import annotations

from zhice_os_v2 import scheduler_jobs as sj


def test_expires_stale_approvals_before_building_plans(monkeypatch):
    events: list[str] = []

    monkeypatch.setattr(sj, "_active_strategy_types", lambda m: ["TREND"])
    monkeypatch.setattr(sj, "_db", lambda: None)

    def fake_expiry(self, market, session_date=None):
        events.append("expiry")
        return {"market": market, "expired": 0}

    def fake_pre_market(self, market, d, strategy_types=None, max_candidates=10, **kw):
        events.append("pre_market")
        return {"market": market, "candidates": 0, "plans": [], "approvals": [], "pools": []}

    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService.run_plan_expiry",
        fake_expiry,
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService.run_pre_market",
        fake_pre_market,
    )

    sj.run_pre_market_job("CN")

    assert events == ["expiry", "pre_market"]  # 过期严格先于建计划


def test_no_active_strategy_skips_without_expiry(monkeypatch):
    """有 ACTIVE 策略但与配置无交集 → strategy_types 空提前跳过，此时不过期
    （盘后 plan_expiry_job 会兜底清理）。注意空 active 会回退 configured，非此路径。"""
    events: list[str] = []
    # active 非空但为配置外类型 → configured∩active=∅ → strategy_types 空 → skip
    monkeypatch.setattr(sj, "_active_strategy_types", lambda m: ["__NOMATCH__"])
    monkeypatch.setattr(sj, "_db", lambda: None)
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService.run_plan_expiry",
        lambda self, market, session_date=None: events.append("expiry"),
    )
    out = sj.run_pre_market_job("CN")
    assert out.get("skipped") == "no_active_strategy"
    assert events == []
