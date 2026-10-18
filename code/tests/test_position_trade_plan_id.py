"""P1-D 回归：持仓落库补 trade_plan_id，恢复 plan→position 归属链。

结构性 id 修复后 approval.order_intent_id == plan.plan_id（唯一形态
plan-cand-{sv}-{ts}-{date}）。_auto_execute 建仓时应把该 plan_id 写进
v2_position.trade_plan_id，此前恒 NULL（save_position/domain/apply_fill 均不带）。

用 sqlite 真库（StaticPool 跨 session 共享）直连 ORM 写入路径，不打桩，真跑到
缺陷本体：断言落库行 trade_plan_id == plan_id 且非 NULL。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.approval.approval import ApprovalRequest
from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase

_PLAN_ID = "plan-cand-sv-x-000001.SZ-2026-08-26"


def _shared_sessionmaker():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _approval() -> ApprovalRequest:
    a = ApprovalRequest(
        request_id="apr-plan-cand-sv-x-000001.SZ-2026-08-26",
        order_intent_id=_PLAN_ID,  # 结构性 id 修复后 == plan_id
        strategy_version_id="sv-x",  # 非数字 → 不触发 _sv_full_id 映射
        market="CN",
        ts_code="000001.SZ",
        direction="BUY",
        qty=100,
        price=10.0,
        reason="test",
    )
    a.approve(actor="test")  # → status = APPROVED（claim_approval_for_execution 前置条件）
    return a


def test_auto_execute_persists_trade_plan_id():
    """走 _auto_execute 建仓，落库 v2_position.trade_plan_id 应 == plan_id 且非 NULL。"""
    Session = _shared_sessionmaker()
    db = SqlAlchemyDatabase(Session())
    wf = WorkflowService(db)
    approval = _approval()
    db.save_approval(approval)  # claim_approval_for_execution 要求授权已持久化为 APPROVED

    position = wf._auto_execute(
        approval, market="CN", profile=get_profile("CN"), live_price=10.0
    )
    assert position is not None, "建仓应成交（市价单全量成交）"

    q = Session()
    row = (
        q.query(orm.PositionModel)
        .filter_by(position_id=position.position_id)
        .one()
    )
    assert row.trade_plan_id == _PLAN_ID
    assert row.trade_plan_id is not None


def test_apply_fill_persists_trade_plan_id():
    """底层 apply_fill 直传 trade_plan_id 亦应落库（ORM 写路径直测）。"""
    Session = _shared_sessionmaker()
    db = SqlAlchemyDatabase(Session())
    result = OrderResult(
        order_id="o-1",
        status=OrderStatus.FILLED,
        filled_qty=Decimal("100"),
        filled_price=Decimal("10"),
    )
    position = ExecutionService(db).apply_fill(
        result,
        strategy_version_id="sv-x",
        market="CN",
        ts_code="000001.SZ",
        side="BUY",
        trade_plan_id=_PLAN_ID,
    )
    assert position is not None
    assert position.trade_plan_id == _PLAN_ID

    q = Session()
    row = q.query(orm.PositionModel).filter_by(position_id="pos-o-1").one()
    assert row.trade_plan_id == _PLAN_ID


def test_approval_execution_service_persists_plan_id_crypto():
    """ApprovalExecutionService（API 人工审批路径）建 CRYPTO 仓时 trade_plan_id 应落库。

    回归：此路径之前不传 trade_plan_id → v2_position.trade_plan_id 恒 NULL，
    导致 CRYPTO 74% 持仓丢失 plan 归属链，win-rate/策略统计全损。
    """
    from zhice_os_v2.application.services.approval_center import StrategySwitch
    from zhice_os_v2.application.services.approval_execution_service import ApprovalExecutionService
    from zhice_os_v2.application.services.execution_orchestrator import ExecutionOrchestrator
    from zhice_os_v2.domain.execution.broker import ExecutionMode
    from zhice_os_v2.domain.market.context import MarketContext
    from zhice_os_v2.domain.market.registry import get_profile
    from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker

    _CRYPTO_PLAN_ID = "plan-cand-v1-crypto-sv-1-BTC-USDT-2026-08-29"

    Session = _shared_sessionmaker()
    db = SqlAlchemyDatabase(Session())

    # 构造已批准的 CRYPTO 授权（order_intent_id = plan_id）
    approval = ApprovalRequest(
        request_id="apr-" + _CRYPTO_PLAN_ID,
        order_intent_id=_CRYPTO_PLAN_ID,
        strategy_version_id="v1-crypto-sv-1",
        market="CRYPTO",
        ts_code="BTC/USDT",
        direction="BUY",
        qty=0.01,
        price=65000.0,
        reason="crypto-test",
    )
    approval.approve(actor="test")
    db.save_approval(approval)

    # 构造编排器：模拟盘开关启用
    sv_switch = StrategySwitch(
        market="CRYPTO",
        strategy_version_id="v1-crypto-sv-1",
        simulated_enabled=True,
    )
    orchestrator = ExecutionOrchestrator(
        brokers={ExecutionMode.SIMULATED: SimulatedBroker()},
        switches={("CRYPTO", "v1-crypto-sv-1"): sv_switch},
    )
    svc = ApprovalExecutionService(
        db=db,
        orchestrator=orchestrator,
        execution_service=ExecutionService(db),
    )

    profile = get_profile("CRYPTO")
    ctx = MarketContext(
        market="CRYPTO", profile=profile,
        currency=profile.currency, tz=profile.tz,
    )
    positions = svc.execute_approved(approval, ctx)

    assert positions, "CRYPTO 模拟撮合应成功建仓"
    pos = positions[0]
    assert pos.trade_plan_id == _CRYPTO_PLAN_ID, (
        f"trade_plan_id 应为 plan_id={_CRYPTO_PLAN_ID!r}，实际={pos.trade_plan_id!r}"
    )

    q = Session()
    row = q.query(orm.PositionModel).filter_by(position_id=pos.position_id).one()
    assert row.trade_plan_id == _CRYPTO_PLAN_ID, "trade_plan_id 未落库，DB 行仍为 NULL"
