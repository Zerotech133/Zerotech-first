"""approve_plan 双轨批准测试：plan_status 变更 + ApprovalRequest 自动创建（幂等）。

不依赖真库：使用 InMemoryDatabase + 直接调用 _create_approval_for_plan 辅助函数。
批准接口的 _update_plan_status 部分走 SQLAlchemy（有 _guard_real_pg 守卫）；
此处只验证 ApprovalRequest 自动创建逻辑（与 ORM plan_status 更新解耦）。
"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.api.routers.plans import _create_approval_for_plan
from zhice_os_v2.application.services.approval_center import ApprovalCenter
from zhice_os_v2.domain.approval.approval import ApprovalStatus
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _make_plan(plan_id: str = "p-auto-1") -> TradePlan:
    return TradePlan(
        plan_id=plan_id,
        strategy_version_id="sv-cn-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        qty=Decimal("100"),
        ref_price=Decimal("1700"),
    )


def _make_center() -> ApprovalCenter:
    return ApprovalCenter(switches={})


# ---------------------------------------------------------------------------
# 核心场景
# ---------------------------------------------------------------------------

def test_approve_plan_creates_pending_approval():
    """批准计划后自动生成一个 PENDING ApprovalRequest。"""
    db = InMemoryDatabase()
    plan = _make_plan("p-auto-1")
    db.save_trade_plan(plan)
    center = _make_center()

    extra = _create_approval_for_plan("p-auto-1", db, center, actor="admin")

    assert extra["approval_request_id"] == "apr-p-auto-1"
    assert extra["auto_created"] is True

    approval = db.get_approval("apr-p-auto-1")
    assert approval is not None
    assert approval.status == ApprovalStatus.PENDING
    assert approval.market == "CN"
    assert approval.ts_code == "600519.SH"
    assert approval.direction == "BUY"
    assert approval.qty == 100.0
    assert approval.order_intent_id == "p-auto-1"


def test_approve_plan_idempotent_no_duplicate():
    """重复调用不重复创建 ApprovalRequest，返回 auto_created=False。"""
    db = InMemoryDatabase()
    plan = _make_plan("p-auto-2")
    db.save_trade_plan(plan)
    center = _make_center()

    first = _create_approval_for_plan("p-auto-2", db, center, actor="admin")
    second = _create_approval_for_plan("p-auto-2", db, center, actor="admin")

    assert first["auto_created"] is True
    assert second["auto_created"] is False
    assert second["approval_request_id"] == "apr-p-auto-2"

    # 只有一条记录
    all_approvals = db.list_approvals()
    assert len([a for a in all_approvals if a.order_intent_id == "p-auto-2"]) == 1


def test_approve_plan_missing_plan_returns_empty():
    """计划不存在时返回空 dict（不抛异常）。"""
    db = InMemoryDatabase()
    center = _make_center()

    extra = _create_approval_for_plan("nonexistent-plan", db, center, actor="admin")

    assert extra == {}
    assert db.list_approvals() == []


def test_approve_plan_actor_in_reason():
    """reason 字段包含 actor 名称，便于审计追踪。"""
    db = InMemoryDatabase()
    db.save_trade_plan(_make_plan("p-auto-3"))
    center = _make_center()

    _create_approval_for_plan("p-auto-3", db, center, actor="zhice-admin")

    approval = db.get_approval("apr-p-auto-3")
    assert "zhice-admin" in approval.reason


def test_approve_plan_approval_request_id_matches_plan_id():
    """ApprovalRequest.request_id 固定格式 apr-{plan_id}，用于幂等和关联。"""
    db = InMemoryDatabase()
    plan_id = "plan-xyz-20260830"
    db.save_trade_plan(_make_plan(plan_id))
    center = _make_center()

    extra = _create_approval_for_plan(plan_id, db, center, actor="user")

    assert extra["approval_request_id"] == f"apr-{plan_id}"
    assert db.get_approval(f"apr-{plan_id}") is not None
