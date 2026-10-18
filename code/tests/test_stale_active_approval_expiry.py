"""滞留 active 承认过期（日内单语义）测试。

背景（2026-09-02 根因）：sim 自动批准产生的 APPROVED 承认若当日未约定则永不过期，
逐日累积撑爆 sim_max_positions 上限 → pre_market 建计划前早退 → 0 成交。
新增 expire_stale_active_approvals：把「created_at 日期<会话日、且无对应 OPEN 持仓」
的 APPROVED + EXECUTING 承认置 EXPIRED，镜像其计划置 EXPIRED；接入 run_plan_expiry。
今日在途、已成交(EXECUTED)、有 OPEN 持仓的承认均不动。幂等。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.planning.trade_plan import PlanStatus, TradePlan
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

SESSION = date(2026, 9, 2)
PAST = datetime(2026, 8, 20, 9, 15)   # 过往会话
TODAY = datetime(2026, 9, 2, 9, 15)   # 今日在途


def _plan(pid: str, created_at: datetime, market: str = "CN",
          status: PlanStatus = PlanStatus.APPROVED) -> TradePlan:
    p = TradePlan(
        plan_id=pid, strategy_version_id="v1-cn-sv-1", market=market,
        ts_code="600000.SH", direction="BUY", qty=Decimal("100"),
        ref_price=Decimal("10"), created_at=created_at,
    )
    p.status = status
    return p


def _appr(order_intent_id: str, status: ApprovalStatus, created_at: datetime,
          market: str = "CN") -> ApprovalRequest:
    return ApprovalRequest(
        request_id=f"apr-{order_intent_id}", order_intent_id=order_intent_id,
        strategy_version_id="v1-cn-sv-1", market=market, ts_code="600000.SH",
        direction="BUY", qty=100.0, price=10.0, reason="t", status=status,
        created_at=created_at,
    )


def _pos(pid: str, plan_id: str, market: str = "CN") -> PositionLot:
    return PositionLot(
        position_id=pid, strategy_version_id="v1-cn-sv-1", market=market,
        ts_code="600000.SH", direction="BUY", open_qty=Decimal("100"),
        open_price=Decimal("10"), status=PositionStatus.OPEN, trade_plan_id=plan_id,
    )


def test_past_approved_expired():
    """过往会话 APPROVED（无 OPEN 持仓）→ 承认 EXPIRED + 计划 EXPIRED。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-appr", PAST))
    db.save_approval(_appr("p-appr", ApprovalStatus.APPROVED, PAST))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 1
    assert db.get_approval("apr-p-appr").status == ApprovalStatus.EXPIRED
    assert db.get_trade_plan("p-appr").status == PlanStatus.EXPIRED


def test_past_executing_expired():
    """过往会话 EXECUTING（claim 后 fill 失败固着）→ EXPIRED。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-exec", PAST))
    db.save_approval(_appr("p-exec", ApprovalStatus.EXECUTING, PAST))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 1
    assert db.get_approval("apr-p-exec").status == ApprovalStatus.EXPIRED


def test_today_active_not_touched():
    """今日在途 APPROVED（创建日==会话日）不被过期。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-today", TODAY))
    db.save_approval(_appr("p-today", ApprovalStatus.APPROVED, TODAY))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 0
    assert db.get_approval("apr-p-today").status == ApprovalStatus.APPROVED


def test_active_with_open_position_protected():
    """过往 APPROVED 但已落 OPEN 持仓（防误爆在持仓）→ 不过期。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-held", PAST))
    db.save_approval(_appr("p-held", ApprovalStatus.APPROVED, PAST))
    db.save_position(_pos("pos-held", "p-held"))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 0
    assert db.get_approval("apr-p-held").status == ApprovalStatus.APPROVED


def test_orphan_past_pending_expired():
    """过往 PENDING 但计划已 EXPIRED（计划先过期、承认孤儿滞留）→ active sweep 兜底过期。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-orph", PAST, status=PlanStatus.EXPIRED))
    db.save_approval(_appr("p-orph", ApprovalStatus.PENDING, PAST))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 1
    assert db.get_approval("apr-p-orph").status == ApprovalStatus.EXPIRED


def test_other_market_not_touched():
    """只动目标 market。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-us", PAST, market="US"))
    db.save_approval(_appr("p-us", ApprovalStatus.APPROVED, PAST, market="US"))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 0
    assert db.get_approval("apr-p-us").status == ApprovalStatus.APPROVED


def test_executed_terminal_not_touched():
    """已成交 EXECUTED（终态）不被过期。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-done", PAST))
    db.save_approval(_appr("p-done", ApprovalStatus.EXECUTED, PAST))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["active_approvals_expired"] == 0
    assert db.get_approval("apr-p-done").status == ApprovalStatus.EXECUTED


def test_idempotent_repeat_run():
    """重复跑幂等：第二次 active_approvals_expired==0。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-appr", PAST))
    db.save_approval(_appr("p-appr", ApprovalStatus.APPROVED, PAST))
    ws = WorkflowService(db)
    first = ws.run_plan_expiry("CN", session_date=SESSION)
    second = ws.run_plan_expiry("CN", session_date=SESSION)
    assert first["active_approvals_expired"] == 1
    assert second["active_approvals_expired"] == 0
