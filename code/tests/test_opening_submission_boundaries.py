"""单元 B Task B2-3：broker 提交边界的权益复核（真 guard + 隔离真表 seed）。

与 test_owned_execution_pipeline（注入放行桩、只测下游流水）互补：本文件用**真**
OpeningSubmissionGuard 指向隔离 sqlite 的真实 seed 行，验证三条提交边界与 LIVE fail-closed：

- owned run_owned_plan：撤销会员的自建私有实例 BUY → ENTITLEMENT_DENIED，不建仓、预留 RELEASED。
- owned run_owned_plan：有效会员 + enabled 自建私有实例 BUY → 正常成交 EXECUTED、建仓。
- workflow _auto_execute：撤销会员 → 拒绝、claim 回滚（approval 回 APPROVED）、不建仓。
- orchestrator execute：BUY + 非会员归属 → 抛 OpeningSubmissionDenied（不 apply_fill）；
  SELL → 放行不受门禁。
- orchestrator LIVE fail-closed：含 LIVE broker + live_enabled + 默认（模拟盘）出箱 →
  抛 LiveExecutionNotConfigured（code broker.external_cancellation_not_configured）。

seed 范式同 tests/test_opening_submission_guard.py（guard 直读库 membership 行判定，
不受 conftest autouse 会员 monkeypatch 影响，故须真实写 membership 行）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# v2_trade_plan / v2_order_intent 等裸 BigInteger 主键在 SQLite 下不自增，编译为 INTEGER
# （SQLite 整数存储类唯一，语义等价）以便 round-trip。仅影响 sqlite 测试。
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"


from zhice_os_v2.application.services.account_ledger_service import AccountLedgerService
from zhice_os_v2.application.services.approval_execution_service import (
    ApprovalExecutionService,
)
from zhice_os_v2.application.services.execution_orchestrator import (
    ExecutionOrchestrator,
    LiveExecutionNotConfigured,
)
from zhice_os_v2.application.services.execution_service import ExecutionService
from zhice_os_v2.application.services.opening_submission_guard import (
    OpeningSubmissionDenied,
    OpeningSubmissionGuard,
)
from zhice_os_v2.application.services.user_instance_execution_service import (
    UserInstanceExecutionService,
)
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.accounts.reservation import ReservationStatus
from zhice_os_v2.domain.approval.approval import ApprovalRequest, ApprovalStatus
from zhice_os_v2.domain.execution.broker import ExecutionMode, OrderSide
from zhice_os_v2.domain.market.context import MarketContext
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.strategy.user_strategy import ExecutionMode as InstExecMode
from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance
from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase

NOW = datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


# ── 隔离 sqlite + seed ────────────────────────────────────────────────────────
@pytest.fixture()
def Session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _user(s, uid):
    s.add(orm.UserModel(user_id=uid, username=uid, password_hash="x", role="USER",
                        is_active=True, created_at=NOW))


def _membership(s, uid, *, tier, valid_until=None, is_permanent=False,
                revoked_at=None, revoked_by=None, status="active"):
    s.add(orm.MembershipModel(
        user_id=uid, tier=tier, valid_until=valid_until, status=status,
        is_permanent=is_permanent, revoked_at=revoked_at,
        revoked_by_user_id=revoked_by, version=1, created_at=NOW, updated_at=NOW,
    ))


def _strategy(s, sid, owner, vis, pub):
    s.add(orm.StrategyModel(
        strategy_id=sid, strategy_name=sid, market="US",
        status="ACTIVE", owner_user_id=owner,
        visibility=vis, publication_status=pub, created_at=NOW, updated_at=NOW,
    ))


def _version(s, svid, sid):
    s.add(orm.StrategyVersionModel(
        strategy_version_id=svid, strategy_id=sid, version_no="1", market="US",
        status="ACTIVE", spec={}, created_at=NOW,
    ))


def _account(s, aid, uid, cash=Decimal("100000")):
    s.add(orm.UserAccountModel(
        account_id=aid, user_id=uid, market="US", currency="USD",
        account_type="SHARED_PORTFOLIO", initial_capital=cash,
        cash_balance=cash, reserved_cash=Decimal("0"),
        status="ACTIVE", created_at=NOW, updated_at=NOW,
    ))


def _instance(s, usid, uid, svid, aid, enabled=True):
    s.add(orm.UserStrategyInstanceModel(
        user_strategy_id=usid, user_id=uid, strategy_version_id=svid, account_id=aid,
        enabled=enabled, auto_execute=True, execution_mode="FULL_AUTO",
        max_position_ratio=Decimal("0.1"), created_at=NOW, updated_at=NOW,
    ))


_REVOKED = dict(
    tier="member", valid_until=NOW + timedelta(days=30),
    revoked_at=NOW - timedelta(days=1), revoked_by="admin", status="revoked",
)
_ACTIVE = dict(tier="member", valid_until=NOW + timedelta(days=30), status="active")


def _seed_private(Session, *, membership_kwargs, enabled=True):
    """u-t 自建私有 strat-priv + 版本 sv-priv + 账户 acc-1 + enabled 实例 inst-priv。"""
    s = Session()
    _user(s, "u-t")
    _membership(s, "u-t", **membership_kwargs)
    _strategy(s, "strat-priv", "u-t", "PRIVATE", "PRIVATE")
    _version(s, "sv-priv", "strat-priv")
    _account(s, "acc-1", "u-t")
    _instance(s, "inst-priv", "u-t", "sv-priv", "acc-1", enabled=enabled)
    s.commit()
    s.close()


def _domain_instance() -> UserStrategyInstance:
    return UserStrategyInstance(
        user_strategy_id="inst-priv",
        user_id="u-t",
        strategy_version_id="sv-priv",
        account_id="acc-1",
        enabled=True,
        auto_execute=True,
        execution_mode=InstExecMode.FULL_AUTO,
    )


@pytest.fixture()
def owned_env(monkeypatch, Session):
    """真 guard + 隔离真表：ledger/门禁/订单链 get_session 全指向同一隔离 sqlite。"""
    import zhice_os_v2.api.routers.circuit_breaker as cb_mod
    import zhice_os_v2.api.routers.positions as pos_mod
    import zhice_os_v2.application.services.account_ledger_service as ledger_mod
    import zhice_os_v2.application.services.workflow_service as wf_mod

    monkeypatch.setattr(ledger_mod, "get_session", lambda: Session())
    monkeypatch.setattr(wf_mod, "_sim_auto", lambda market: True)
    monkeypatch.setattr(cb_mod, "is_tripped", lambda: False)
    monkeypatch.setattr(pos_mod, "_get_pre_closes", lambda codes, market: {})

    guard = OpeningSubmissionGuard(session_factory=lambda: Session(), clock=lambda: NOW)
    db = InMemoryDatabase()
    svc = UserInstanceExecutionService(
        db,
        ledger=AccountLedgerService(),
        session_factory=Session,
        opening_guard=guard,
    )
    return svc, db


# ── owned run_owned_plan 边界 ───────────────────────────────────────────────────
def test_owned_revoked_member_denied(owned_env, Session):
    """撤销会员的自建私有实例 BUY → ENTITLEMENT_DENIED，不建仓、预留 RELEASED。"""
    _seed_private(Session, membership_kwargs=_REVOKED)
    svc, db = owned_env
    result = svc.run_owned_trade(
        _domain_instance(), market="US", ts_code="AAPL",
        ref_price=Decimal("100"), qty=Decimal("100"),
    )
    assert result.status == "ENTITLEMENT_DENIED"
    assert result.position is None
    assert db.list_open_positions("US") == []
    assert result.reservation is not None
    assert result.reservation.status == ReservationStatus.RELEASED
    account = svc.ledger.get("acc-1")
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("100000")  # 未实扣


def test_owned_active_member_executes(owned_env, Session):
    """有效会员 + enabled 自建私有实例 BUY → 正常成交 EXECUTED、建仓。"""
    _seed_private(Session, membership_kwargs=_ACTIVE)
    svc, db = owned_env
    result = svc.run_owned_trade(
        _domain_instance(), market="US", ts_code="AAPL",
        ref_price=Decimal("100"), qty=Decimal("100"),
    )
    assert result.status == "EXECUTED"
    assert result.position is not None
    assert result.position.user_strategy_id == "inst-priv"
    assert len(db.list_open_positions("US")) == 1
    resv = svc.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.CONSUMED
    account = svc.ledger.get("acc-1")
    assert account.cash_balance == Decimal("90000")  # 100000 - 100*100


# ── workflow _auto_execute 边界 ─────────────────────────────────────────────────
def test_auto_execute_revoked_member_denied_and_rolls_back_claim(monkeypatch, Session):
    """撤销会员 → _auto_execute 拒绝、claim 回滚（approval 回 APPROVED）、不建仓。"""
    import zhice_os_v2.api.routers.circuit_breaker as cb_mod
    import zhice_os_v2.api.routers.positions as pos_mod

    monkeypatch.setattr(cb_mod, "is_tripped", lambda: False)
    monkeypatch.setattr(pos_mod, "_get_pre_closes", lambda codes, market: {})

    _seed_private(Session, membership_kwargs=_REVOKED)
    db = SqlAlchemyDatabase(Session())
    approval = ApprovalRequest(
        request_id="apr-inst-priv", order_intent_id="plan-inst-priv",
        strategy_version_id="sv-priv", market="US", ts_code="AAPL",
        direction="BUY", qty=100, price=100.0, reason="test",
        user_strategy_id="inst-priv",
    )
    approval.approve(actor="test")
    db.save_approval(approval)

    guard = OpeningSubmissionGuard(session_factory=lambda: Session(), clock=lambda: NOW)
    pos = WorkflowService(db, opening_guard=guard)._auto_execute(
        approval, market="US", profile=get_profile("US"), live_price=100.0,
    )
    assert pos is None
    # claim 回滚：EXECUTING → APPROVED（未固着、当日可重试）
    assert db.get_approval("apr-inst-priv").status == ApprovalStatus.APPROVED
    assert db.list_open_positions("US") == []


# ── execute_approved（人工审批/API 路径）边界 ───────────────────────────────────
def test_execute_approved_denied_rolls_back_claim(monkeypatch, Session):
    """撤销会员 owned BUY 走 execute_approved → 抛 OpeningSubmissionDenied 且 claim 回滚
    （approval 回 APPROVED，非永卡 EXECUTING 占配额）。"""
    from zhice_os_v2.application.services.approval_center import StrategySwitch

    _seed_private(Session, membership_kwargs=_REVOKED)
    db = SqlAlchemyDatabase(Session())
    approval = ApprovalRequest(
        request_id="apr-inst-priv", order_intent_id="plan-inst-priv",
        strategy_version_id="sv-priv", market="US", ts_code="AAPL",
        direction="BUY", qty=100, price=100.0, reason="test",
        user_strategy_id="inst-priv",
    )
    approval.approve(actor="test")
    db.save_approval(approval)

    guard = OpeningSubmissionGuard(session_factory=lambda: Session(), clock=lambda: NOW)
    switches = {
        ("US", "sv-priv"): StrategySwitch(
            market="US", strategy_version_id="sv-priv", simulated_enabled=True
        )
    }
    orch = ExecutionOrchestrator(
        {ExecutionMode.SIMULATED: SimulatedBroker()}, switches, opening_guard=guard
    )
    svc = ApprovalExecutionService(db, orch, ExecutionService(db))
    with pytest.raises(OpeningSubmissionDenied) as ei:
        svc.execute_approved(approval, _mctx())
    assert ei.value.code == "opening.membership_required"
    # claim 已回滚：EXECUTING → APPROVED（未固着占 sim_max_positions）
    assert db.get_approval("apr-inst-priv").status == ApprovalStatus.APPROVED
    assert db.list_open_positions("US") == []


# ── orchestrator 边界 ───────────────────────────────────────────────────────────
def _mctx():
    profile = get_profile("US")
    return MarketContext(
        market="US", profile=profile, currency=profile.currency, tz=profile.tz
    )


def test_orchestrator_buy_non_member_denied(Session):
    """orchestrator BUY + 非会员归属 → 抛 OpeningSubmissionDenied（sim/live 都不提交）。"""
    _seed_private(Session, membership_kwargs=_REVOKED)
    guard = OpeningSubmissionGuard(session_factory=lambda: Session(), clock=lambda: NOW)
    from zhice_os_v2.application.services.approval_center import StrategySwitch

    switches = {
        ("US", "sv-priv"): StrategySwitch(
            market="US", strategy_version_id="sv-priv", simulated_enabled=True
        )
    }
    orch = ExecutionOrchestrator(
        {ExecutionMode.SIMULATED: SimulatedBroker()}, switches, opening_guard=guard
    )
    with pytest.raises(OpeningSubmissionDenied) as ei:
        orch.execute(
            _mctx(), strategy_version_id="sv-priv", order_intent_id="oi-1",
            ts_code="AAPL", side=OrderSide.BUY, qty=Decimal("100"),
            price=Decimal("100"), user_strategy_id="inst-priv",
        )
    assert ei.value.code == "opening.membership_required"


def test_orchestrator_sell_bypasses_guard(Session):
    """orchestrator SELL → 门禁不介入（side!=BUY），即便撤销会员也放行提交。"""
    _seed_private(Session, membership_kwargs=_REVOKED)
    guard = OpeningSubmissionGuard(session_factory=lambda: Session(), clock=lambda: NOW)
    from zhice_os_v2.application.services.approval_center import StrategySwitch

    switches = {
        ("US", "sv-priv"): StrategySwitch(
            market="US", strategy_version_id="sv-priv", simulated_enabled=True
        )
    }
    orch = ExecutionOrchestrator(
        {ExecutionMode.SIMULATED: SimulatedBroker()}, switches, opening_guard=guard
    )
    # 不抛门禁异常，正常提交模拟盘（SELL 退出不复核会员）。
    results = orch.execute(
        _mctx(), strategy_version_id="sv-priv", order_intent_id="oi-2",
        ts_code="AAPL", side=OrderSide.SELL, qty=Decimal("100"),
        price=Decimal("100"), user_strategy_id="inst-priv",
    )
    assert isinstance(results, list)
    assert len(results) == 1


def test_orchestrator_live_fail_closed():
    """含 LIVE broker + live_enabled + 默认（模拟盘）出箱 → 抛 LiveExecutionNotConfigured。"""
    from zhice_os_v2.application.services.approval_center import StrategySwitch

    switches = {
        ("US", "sv-x"): StrategySwitch(
            market="US", strategy_version_id="sv-x",
            simulated_enabled=False, live_enabled=True,
        )
    }
    orch = ExecutionOrchestrator(
        {
            ExecutionMode.SIMULATED: SimulatedBroker(),
            ExecutionMode.LIVE: SimulatedBroker(),  # 占位；出箱缺失应在下单前 fail-closed
        },
        switches,
    )  # 默认 cancellation_outbox = SimulatedOnly（supports_durable_outbox=False）
    with pytest.raises(LiveExecutionNotConfigured) as ei:
        orch.execute(
            _mctx(), strategy_version_id="sv-x", order_intent_id="oi-3",
            ts_code="AAPL", side=OrderSide.SELL, qty=Decimal("100"),
            price=Decimal("100"),  # SELL + 无归属：跳过开仓门禁，隔离验 LIVE 门
        )
    assert ei.value.code == "broker.external_cancellation_not_configured"


def test_orchestrator_live_fail_closed_blocks_sim_too():
    """sim+live 同开且无 durable outbox：LIVE fail-closed 前置到任何 place_order 之前，
    sim 单也不提交（防先下 sim 再抛异常丢弃 fill）。"""
    from zhice_os_v2.application.services.approval_center import StrategySwitch

    calls: list[str] = []

    class _SpyBroker(SimulatedBroker):
        def place_order(self, ctx, request):  # noqa: ANN001, ANN201
            calls.append(request.order_id)
            return super().place_order(ctx, request)

    switches = {
        ("US", "sv-x"): StrategySwitch(
            market="US", strategy_version_id="sv-x",
            simulated_enabled=True, live_enabled=True,
        )
    }
    orch = ExecutionOrchestrator(
        {
            ExecutionMode.SIMULATED: _SpyBroker(),
            ExecutionMode.LIVE: SimulatedBroker(),
        },
        switches,
    )
    with pytest.raises(LiveExecutionNotConfigured):
        orch.execute(
            _mctx(), strategy_version_id="sv-x", order_intent_id="oi-5",
            ts_code="AAPL", side=OrderSide.SELL, qty=Decimal("100"),
            price=Decimal("100"),
        )
    assert calls == []  # sim 也未提交（前置 fail-closed）
