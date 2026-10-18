"""计划过期机制测试（P3-1 派生，口径A 同会话）。

背景：V2 曾无计划过期逻辑，PENDING 计划无限堆积；approval.expire() 有定义无调用。
新增 run_plan_expiry：把「创建日 < 当前会话日、且无 APPROVED 授权」的 PENDING 计划置
EXPIRED，其 PENDING 授权一并 expire()。硬排除 approval=APPROVED，幂等。
本测试用 InMemory 后端锁定该口径（真库实现同为按 market/日期/approval 过滤的 UPDATE）。
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.planning.trade_plan import PlanStatus, TradePlan
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

SESSION = date(2026, 8, 25)
PAST = datetime(2026, 8, 20, 9, 15)   # 过往会话
TODAY = datetime(2026, 8, 25, 9, 15)  # 今日在途


def _plan(pid: str, created_at: datetime, market: str = "CN") -> TradePlan:
    return TradePlan(
        plan_id=pid, strategy_version_id="v1-cn-sv-1", market=market,
        ts_code="600000.SH", direction="BUY", qty=Decimal("100"),
        ref_price=Decimal("10"), created_at=created_at,
    )


def _appr(order_intent_id: str, status: ApprovalStatus) -> ApprovalRequest:
    return ApprovalRequest(
        request_id=f"apr-{order_intent_id}", order_intent_id=order_intent_id,
        strategy_version_id="v1-cn-sv-1", market="CN", ts_code="600000.SH",
        direction="BUY", qty=100.0, price=10.0, reason="t", status=status,
    )


def test_past_session_pending_expired_both_sides():
    """过往会话未批准 PENDING → plan EXPIRED + 其 PENDING approval EXPIRED（两侧一致）。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-past", PAST))
    db.save_approval(_appr("p-past", ApprovalStatus.PENDING))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["plans_expired"] == 1
    assert res["approvals_expired"] == 1
    assert db.get_trade_plan("p-past").status == PlanStatus.EXPIRED
    assert db.get_approval("apr-p-past").status == ApprovalStatus.EXPIRED


def test_today_in_flight_not_touched():
    """今日在途 PENDING（创建日==会话日）不被过期。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-today", TODAY))
    db.save_approval(_appr("p-today", ApprovalStatus.PENDING))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["plans_expired"] == 0
    assert db.get_trade_plan("p-today").status == PlanStatus.PENDING
    assert db.get_approval("apr-p-today").status == ApprovalStatus.PENDING


def test_approved_not_expired():
    """过往会话但 approval=APPROVED（已批准/在执行）→ 硬排除，不过期。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-appr", PAST))
    db.save_approval(_appr("p-appr", ApprovalStatus.APPROVED))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["plans_expired"] == 0
    assert db.get_trade_plan("p-appr").status == PlanStatus.PENDING
    assert db.get_approval("apr-p-appr").status == ApprovalStatus.APPROVED


def test_other_market_not_touched():
    """只动目标 market。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-us", PAST, market="US"))
    res = WorkflowService(db).run_plan_expiry("CN", session_date=SESSION)
    assert res["plans_expired"] == 0
    assert db.get_trade_plan("p-us").status == PlanStatus.PENDING


def test_idempotent_repeat_run():
    """重复跑幂等：第二次 plans_expired==0，状态不变。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_plan("p-past", PAST))
    db.save_approval(_appr("p-past", ApprovalStatus.PENDING))
    ws = WorkflowService(db)
    first = ws.run_plan_expiry("CN", session_date=SESSION)
    second = ws.run_plan_expiry("CN", session_date=SESSION)
    assert first["plans_expired"] == 1
    assert second["plans_expired"] == 0
    assert second["approvals_expired"] == 0
    assert db.get_trade_plan("p-past").status == PlanStatus.EXPIRED
    assert db.get_approval("apr-p-past").status == ApprovalStatus.EXPIRED
