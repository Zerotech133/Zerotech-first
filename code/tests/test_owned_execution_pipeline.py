"""Phase B Task 5：enforced 归属贯穿 + 预留 consume/release + owner-scoped 过滤。

覆盖：
- RuntimeContext.from_instance 归属载体。
- run_owned_plan（enforced 下游流水）：一条 owned 计划贯穿到 owned position，
  plan/approval/intent/position 归属全一致（intent 经 OwnershipResolver 链路证明）。
- 成交成功 → consume 预留（reserved 归 0、cash_balance 实扣）。
- 风控 BLOCK / 未成交 → release 预留（幂等，不泄漏冻结资金）。
- account_stats / pnl 新增 owner 过滤：旧全局调用行为不变 + owner 过滤只返回带归属行。

legacy 回归由现有执行测试保证（本文件只测 enforced 新路径，不触碰 legacy）。
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# v2_trade_plan / v2_order_intent 等用裸 BigInteger 主键；SQLite 下 BIGINT 不自增，
# 编译为 INTEGER（SQLite 整数存储类唯一，语义等价）以便 round-trip。仅影响 sqlite。
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"


from zhice_os_v2.application.services.account_ledger_service import AccountLedgerService
from zhice_os_v2.application.services.ownership_resolver import OwnershipResolver
from zhice_os_v2.application.services.user_instance_execution_service import (
    UserInstanceExecutionService,
)
from zhice_os_v2.domain.accounts.reservation import ReservationStatus
from zhice_os_v2.domain.execution.runtime_context import RuntimeContext
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


class _AllowGuard:
    """放行桩：本文件用 InMemoryDatabase + 内存实例，真 guard 经 get_session 读隔离
    sqlite（无 strategy/instance 行）会误判 instance_not_found。注入本桩放行开仓门禁，
    只测既有 enforced 下游流水；门禁判定本身由 test_opening_submission_boundaries 用真
    guard + 隔离真表覆盖。不削弱真 guard。"""

    @contextmanager
    def hold(self, *, user_strategy_id):  # noqa: ANN001, ANN202
        yield None


# ──────────────────────────────────────────────────────────────────────────────
# RuntimeContext
# ──────────────────────────────────────────────────────────────────────────────
def test_runtime_context_from_instance():
    inst = UserStrategyInstance(
        user_strategy_id="ustrat-1",
        user_id="u1",
        strategy_version_id="sv-1",
        account_id="acct-1",
        enabled=True,
        auto_execute=True,
    )
    ctx = RuntimeContext.from_instance(inst)
    assert ctx.user_id == "u1"
    assert ctx.user_strategy_id == "ustrat-1"
    assert ctx.account_id == "acct-1"
    assert ctx.strategy_version_id == "sv-1"
    assert ctx.auto_execute is True


# ──────────────────────────────────────────────────────────────────────────────
# enforced 下游流水环境（隔离 sqlite；账户/实例/台账/订单链/解析器共用）
# ──────────────────────────────────────────────────────────────────────────────
@pytest.fixture
def owned_env(monkeypatch, users):
    import zhice_os_v2.application.services.account_ledger_service as ledger_mod
    import zhice_os_v2.infrastructure.db.session as db_session_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )
    from zhice_os_v2.application.services.user_account_service import UserAccountService
    from zhice_os_v2.application.services.user_strategy_service import UserStrategyService

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    # 台账 / 账户仓储 / 实例仓储 / 订单链 get_session 全指向同一隔离 sqlite
    for mod in (ledger_mod, acc_repo_mod, inst_repo_mod, db_session_mod):
        monkeypatch.setattr(mod, "get_session", lambda: Session())

    # 市场级 kill-switch（运营停牌总闸）：owned 自动执行须 AND 该闸。默认置 True
    # （市场开放）使 EXECUTED 路径成立；kill-switch 关闭场景由专项测试覆盖。
    import zhice_os_v2.application.services.workflow_service as wf_mod
    monkeypatch.setattr(wf_mod, "_sim_auto", lambda market: True)
    # 熔断急停基线置 False（否则 is_tripped 经 app_state 模块全局 _memory_state 兜底可能
    # 被其它测试的熔断状态污染 → owned 撮合 fail-closed 成 HALTED）。I1 专项测试覆盖 True。
    import zhice_os_v2.api.routers.circuit_breaker as cb_mod
    monkeypatch.setattr(cb_mod, "is_tripped", lambda: False)
    # 离群价护栏基线：无昨收（放行，不误伤）。否则 _get_pre_closes 的跨测试缓存/脏数据
    # （曾见 AAPL pre_close=324.96）会把撮合价 100 判为离群 → REJECTED。I2 专项测试覆盖。
    import zhice_os_v2.api.routers.positions as pos_mod
    monkeypatch.setattr(pos_mod, "_get_pre_closes", lambda codes, market: {})

    db = InMemoryDatabase()
    account_service = UserAccountService()
    instance_service = UserStrategyService(db=db, account_service=account_service)
    catalog = StrategyCatalogService(db)
    strat = catalog.create_public("owned-strat", market="US")
    version = next(
        v for v in db.list_strategy_versions() if v.strategy_id == strat.strategy_id
    )
    account = account_service.create(
        users.alice, market="US", account_type="SHARED_PORTFOLIO",
        initial_capital=100000,
    )
    instance = instance_service.create(
        users.alice,
        strategy_version_id=version.strategy_version_id,
        account_id=account.account_id,
        capital_quota=None,
        auto_execute=True,  # enforced 自动执行：approval 自动批准 → EXECUTED 路径
    )
    svc = UserInstanceExecutionService(
        db,
        ledger=AccountLedgerService(),
        session_factory=Session,
        opening_guard=_AllowGuard(),
    )
    resolver = OwnershipResolver(session_factory=Session)
    return SimpleNamespace(
        db=db,
        svc=svc,
        ledger=AccountLedgerService(),
        resolver=resolver,
        account=account,
        instance=instance,
        version=version,
        session_factory=Session,
    )


def test_owned_plan_reaches_owned_position(owned_env):
    """enforced：owned 计划贯穿到 owned position，plan/approval/intent/position 归属一致。"""
    inst = owned_env.instance
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "EXECUTED"
    # 计划归属
    assert result.plan.user_strategy_id == inst.user_strategy_id
    assert result.plan.account_id == inst.account_id
    # 审批归属
    assert result.approval is not None
    assert result.approval.user_strategy_id == inst.user_strategy_id
    assert result.approval.account_id == inst.account_id
    # 持仓归属
    assert result.position is not None
    assert result.position.user_strategy_id == inst.user_strategy_id
    assert result.position.account_id == inst.account_id
    # intent 归属（直接读 + 经成交链解析都命中同一实例）
    intent_owner = owned_env.resolver.for_intent(result.intent_id)
    assert intent_owner is not None
    assert intent_owner.user_strategy_id == inst.user_strategy_id
    assert intent_owner.account_id == inst.account_id
    fill_owner = owned_env.resolver.for_fill(result.fill_id)
    assert fill_owner is not None
    assert fill_owner.user_strategy_id == inst.user_strategy_id
    assert fill_owner.account_id == inst.account_id


def test_owned_fill_consumes_reservation(owned_env):
    """成交成功 → 预留 CONSUMED；reserved 归 0、cash_balance 实扣 amount。"""
    inst = owned_env.instance
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "EXECUTED"
    resv = owned_env.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.CONSUMED
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("90000")  # 100000 - 100*100


def test_owned_rejected_plan_releases_reservation(owned_env):
    """风控 BLOCK → 预留 RELEASED；reserved 归 0、cash_balance 不变（不泄漏冻结资金）。"""
    inst = owned_env.instance
    ctx = RuntimeContext.from_instance(inst)
    # max_position_ratio > 0.10 → 风控 BLOCK
    plan = TradePlan(
        plan_id="plan-owned-reject",
        strategy_version_id=inst.strategy_version_id,
        market="US",
        ts_code="AAPL",
        direction="BUY",
        qty=Decimal("100"),
        ref_price=Decimal("100"),
        max_position_ratio=Decimal("0.5"),
        user_strategy_id=inst.user_strategy_id,
        account_id=inst.account_id,
    )
    reservation = owned_env.ledger.reserve(
        inst.account_id, inst.user_strategy_id, Decimal("10000"), plan.plan_id
    )
    result = owned_env.svc.run_owned_plan(plan, ctx, reservation.reservation_id)
    assert result.status == "REJECTED"
    assert result.position is None
    resv = owned_env.ledger.get_reservation(reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("100000")  # 未实扣


def test_owned_manual_approval_pending_releases_reservation(owned_env):
    """auto_execute=False → approval 停 PENDING：不下单、不成交，预留 RELEASED，现金不动。

    [必修 #2] run_owned_plan 须按 approval 状态门控执行，杜绝绕过人工审批闸成交。
    """
    inst = owned_env.instance
    ctx = RuntimeContext(
        user_id=inst.user_id,
        user_strategy_id=inst.user_strategy_id,
        account_id=inst.account_id,
        strategy_version_id=inst.strategy_version_id,
        execution_mode="enforced",
        auto_execute=False,  # 人工审批：approval 停 PENDING
    )
    plan = TradePlan(
        plan_id="plan-owned-manual",
        strategy_version_id=inst.strategy_version_id,
        market="US",
        ts_code="AAPL",
        direction="BUY",
        qty=Decimal("100"),
        ref_price=Decimal("100"),  # 默认 max_position_ratio=0.05 → 风控 PASS
        user_strategy_id=inst.user_strategy_id,
        account_id=inst.account_id,
    )
    reservation = owned_env.ledger.reserve(
        inst.account_id, inst.user_strategy_id, Decimal("10000"), plan.plan_id
    )
    result = owned_env.svc.run_owned_plan(plan, ctx, reservation.reservation_id)
    assert result.status == "PENDING"
    assert result.position is None
    # 未成交无持仓
    assert owned_env.db.list_open_positions("US") == []
    # 预留 RELEASED、现金未实扣
    resv = owned_env.ledger.get_reservation(reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("100000")


def test_owned_consume_failure_keeps_reserved_conservative(owned_env, monkeypatch):
    """[必修 #1] 成交落库后 consume 失败 → 不 release（保守冻结 RESERVED 待对账），异常上抛。

    宁可冻结待对账，也不 release 造成"持仓存在但现金未扣"的凭空造钱。
    """
    inst = owned_env.instance  # auto_execute=True

    def _boom(self, reservation_id, actual_amount=None):
        raise RuntimeError("ledger consume down")

    monkeypatch.setattr(AccountLedgerService, "consume", _boom)
    with pytest.raises(RuntimeError, match="ledger consume down"):
        owned_env.svc.run_owned_trade(
            inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
            qty=Decimal("100"),
        )
    # 持仓已落库（成交成功）
    positions = owned_env.db.list_open_positions("US")
    assert len(positions) == 1
    # 预留保守冻结在 RESERVED（未被 release 造钱），reserved_cash 仍冻结
    resvs = owned_env.ledger.list_reservations(inst.account_id)
    assert any(r.status == ReservationStatus.RESERVED for r in resvs)
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("10000")  # 100*100 仍冻结待对账


def test_owned_partial_fill_consumes_actual_amount(owned_env, monkeypatch):
    """[必修 #1] 部分成交：consume 按实际成交额扣 cash，reserved 全额解冻（不超扣）。"""
    from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus

    inst = owned_env.instance

    def _partial(self, ctx, request):
        return OrderResult(
            order_id=request.order_id,
            status=OrderStatus.PARTIALLY_FILLED,
            filled_qty=Decimal("50"),
            filled_price=Decimal("100"),
            fee=Decimal("0"),
            avg_fill_ratio=0.5,
            remaining_qty=Decimal("50"),
        )

    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.brokers.simulated.SimulatedBroker.place_order",
        _partial,
    )
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "EXECUTED"
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("0")  # 预留额全额解冻
    assert account.cash_balance == Decimal("95000")  # 只实扣 50*100，非满额 10000
    resv = owned_env.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.CONSUMED


def test_owned_auto_blocked_by_market_kill_switch(owned_env, monkeypatch):
    """[必修 #2] 市场 kill-switch 关闭 → owned auto_execute 实例仍停 PENDING、预留 released。"""
    import zhice_os_v2.application.services.workflow_service as wf_mod

    monkeypatch.setattr(wf_mod, "_sim_auto", lambda market: False)  # 市场停牌总闸
    inst = owned_env.instance  # auto_execute=True，但市场闸关闭须硬拦
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "PENDING"
    assert result.position is None
    assert owned_env.db.list_open_positions("US") == []
    resv = owned_env.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("0")
    assert account.cash_balance == Decimal("100000")


def test_owned_sell_fail_closed(owned_env):
    """[必修 #3] owned SELL 计划 fail-closed：拒绝、预留 released、绝不触碰任何持仓。"""
    inst = owned_env.instance
    ctx = RuntimeContext.from_instance(inst)
    plan = TradePlan(
        plan_id="plan-owned-sell",
        strategy_version_id=inst.strategy_version_id,
        market="US",
        ts_code="AAPL",
        direction="SELL",
        qty=Decimal("100"),
        ref_price=Decimal("100"),
        user_strategy_id=inst.user_strategy_id,
        account_id=inst.account_id,
    )
    reservation = owned_env.ledger.reserve(
        inst.account_id, inst.user_strategy_id, Decimal("10000"), plan.plan_id
    )
    with pytest.raises(ValueError, match="owned_sell_not_supported"):
        owned_env.svc.run_owned_plan(plan, ctx, reservation.reservation_id)
    resv = owned_env.ledger.get_reservation(reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    assert owned_env.db.list_open_positions("US") == []


def test_owned_warn_risk_forces_manual_pending(owned_env, monkeypatch):
    """[I3] 风控 WARN → 不自动批准（强制人工），停 PENDING、预留 released、不成交。"""
    from zhice_os_v2.application.services.risk_service import RiskService
    from zhice_os_v2.domain.risk.risk import RiskDecision, RiskResult

    inst = owned_env.instance  # auto_execute=True + 市场闸开，但 WARN 须强制人工

    def _warn(self, plan, profile=None, price=None, turnover=None):
        return RiskDecision(
            risk_decision_id=f"risk-{plan.plan_id}",
            trade_plan_id=plan.plan_id,
            result=RiskResult.WARN,
            reasons=["PRICE_HIGH"],
            rule_results={},
        )

    monkeypatch.setattr(RiskService, "check_plan", _warn)
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "PENDING"
    assert result.risk_result == "WARN"
    assert result.position is None
    resv = owned_env.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.cash_balance == Decimal("100000")


def test_owned_circuit_breaker_halts(owned_env, monkeypatch):
    """[I1] 熔断急停激活 → 撮合前 fail-closed：不成交、预留 released、返回 HALTED。"""
    monkeypatch.setattr(
        "zhice_os_v2.api.routers.circuit_breaker.is_tripped", lambda: True
    )
    inst = owned_env.instance
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "HALTED"
    assert result.position is None
    assert owned_env.db.list_open_positions("US") == []
    resv = owned_env.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.cash_balance == Decimal("100000")


def test_owned_outlier_price_rejected(owned_env, monkeypatch):
    """[I2] 撮合基准价偏离昨收 >阈值 → 离群价拦截：不成交、预留 released、返回 REJECTED。"""
    monkeypatch.setattr(
        "zhice_os_v2.api.routers.positions._get_pre_closes",
        lambda codes, market: {"AAPL": 10.0},  # 昨收 10 vs 撮合价 100 → dev 9.0>0.5
    )
    inst = owned_env.instance
    result = owned_env.svc.run_owned_trade(
        inst, market="US", ts_code="AAPL", ref_price=Decimal("100"),
        qty=Decimal("100"),
    )
    assert result.status == "REJECTED"
    assert result.position is None
    assert owned_env.db.list_open_positions("US") == []
    resv = owned_env.ledger.get_reservation(result.reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.cash_balance == Decimal("100000")


def test_enforced_single_chain_no_reservation_orphan(owned_env, monkeypatch):
    """[C1+C2] execute_fanout 单链：建计划→预留→全生命周期，无孤儿预留、无双预留。"""
    import zhice_os_v2.api.routers.positions as pos_mod
    import zhice_os_v2.application.services.position_sizing_service as sizing_mod
    import zhice_os_v2.application.services.strategy_registry_service as reg_mod
    import zhice_os_v2.application.services.workflow_service as wf

    inst = owned_env.instance
    sv = inst.strategy_version_id
    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: sv)
    monkeypatch.setattr(wf, "_make_candidate_id", lambda sv, ts, td: f"cand-{sv}-{ts}")
    monkeypatch.setattr(
        owned_env.svc.user_strategies, "list_enabled_for_market",
        lambda market: [inst],
    )
    monkeypatch.setattr(
        reg_mod.StrategyRegistryService, "run",
        lambda self, st, td, market=None: [
            {"ts_code": "AAPL", "score": 1.0, "reason": "r"}
        ],
    )
    monkeypatch.setattr(
        pos_mod, "_get_latest_prices", lambda codes, market: {"AAPL": 100.0}
    )
    monkeypatch.setattr(
        sizing_mod, "size_new_position",
        lambda db, m, sv, code, price: Decimal("100"),
    )

    out = owned_env.svc.execute_fanout("US", date(2026, 9, 1), ["TREND"])
    # 单链跑完全生命周期 → EXECUTED
    assert len(out["results"]) == 1
    assert out["results"][0]["status"] == "EXECUTED"
    # 同一 plan 只预留一次，且已 consume（无孤儿 RESERVED）
    resvs = owned_env.ledger.list_reservations(inst.account_id)
    assert len(resvs) == 1
    assert resvs[0].status == ReservationStatus.CONSUMED
    # 持仓落库且带归属
    positions = owned_env.db.list_open_positions("US")
    assert len(positions) == 1
    assert positions[0].user_strategy_id == inst.user_strategy_id
    assert positions[0].account_id == inst.account_id


def test_owned_reservation_guaranteed_released_on_error(owned_env, monkeypatch):
    """下游异常 → try/finally 保证预留最终 RELEASED（不泄漏冻结资金）。"""
    inst = owned_env.instance
    ctx = RuntimeContext.from_instance(inst)
    plan = TradePlan(
        plan_id="plan-owned-boom",
        strategy_version_id=inst.strategy_version_id,
        market="US",
        ts_code="AAPL",
        direction="BUY",
        qty=Decimal("100"),
        ref_price=Decimal("100"),
        user_strategy_id=inst.user_strategy_id,
        account_id=inst.account_id,
    )
    reservation = owned_env.ledger.reserve(
        inst.account_id, inst.user_strategy_id, Decimal("10000"), plan.plan_id
    )
    # 强制下游成交步骤抛错

    def _boom(*a, **k):
        raise RuntimeError("broker down")

    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.brokers.simulated.SimulatedBroker.place_order",
        _boom,
    )
    with pytest.raises(RuntimeError, match="broker down"):
        owned_env.svc.run_owned_plan(plan, ctx, reservation.reservation_id)
    resv = owned_env.ledger.get_reservation(reservation.reservation_id)
    assert resv.status == ReservationStatus.RELEASED
    account = owned_env.ledger.get(inst.account_id)
    assert account.reserved_cash == Decimal("0")


# ──────────────────────────────────────────────────────────────────────────────
# owner-scoped 过滤：account_stats / pnl（旧全局调用不变 + owner 过滤正确）
# ──────────────────────────────────────────────────────────────────────────────
def _owned_lot(pos_id, sv, acct, ustrat, ts="AAPL"):
    return PositionLot(
        position_id=pos_id,
        strategy_version_id=sv,
        market="US",
        ts_code=ts,
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("100"),
        user_strategy_id=ustrat,
        account_id=acct,
    )


def test_account_stats_owner_filter(monkeypatch):
    """by_strategy 默认全局（旧行为）；带 account_id 只返回该账户归属持仓。"""
    from zhice_os_v2.application.services.account_stats_service import AccountStatsService

    db = InMemoryDatabase()
    db.save_position(_owned_lot("pos-a", "sv-1", "acct-a", "ustrat-a"))
    db.save_position(_owned_lot("pos-b", "sv-1", "acct-b", "ustrat-b"))

    svc = AccountStatsService(db)
    monkeypatch.setattr(svc, "_latest_prices", lambda s, m: {})
    monkeypatch.setattr(svc, "_pre_closes", lambda s, m: {})
    monkeypatch.setattr(svc, "_latest_trade_date", lambda m: date(2026, 9, 1))
    monkeypatch.setattr(svc, "_realized_pnl_by_strategy", lambda m: {})
    monkeypatch.setattr(
        AccountStatsService, "_strategy_names", staticmethod(lambda market=None: {})
    )

    # 旧全局调用：两持仓合并进 sv-1（position_count=2）
    all_rows = svc.by_strategy("US")
    assert len(all_rows) == 1
    assert all_rows[0]["position_count"] == 2
    # owner 过滤：只看 acct-a
    owned_rows = svc.by_strategy("US", account_id="acct-a")
    assert len(owned_rows) == 1
    assert owned_rows[0]["position_count"] == 1


def test_pnl_calendar_load_lots_owner_filter():
    """_load_lots 默认全局（旧行为）；带 owner 过滤只返回带归属持仓。"""
    import zhice_os_v2.application.services.pnl_calendar_service as pnl_mod
    from zhice_os_v2.infrastructure.db import models as orm

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    now = datetime(2026, 9, 1, tzinfo=UTC)
    s = Session()
    s.add(orm.PositionModel(
        position_id="pos-a", strategy_version_id="sv-1", market="US", ts_code="AAPL",
        direction="BUY", open_qty=Decimal("100"), open_price=Decimal("100"),
        status="OPEN", opened_at=now, user_strategy_id="ustrat-a", account_id="acct-a",
    ))
    s.add(orm.PositionModel(
        position_id="pos-b", strategy_version_id="sv-1", market="US", ts_code="AAPL",
        direction="BUY", open_qty=Decimal("100"), open_price=Decimal("100"),
        status="OPEN", opened_at=now, user_strategy_id="ustrat-b", account_id="acct-b",
    ))
    s.commit()
    s.close()

    session = Session()
    try:
        all_lots = pnl_mod._load_lots(session, "US", None)
        assert len(all_lots) == 2
        owned = pnl_mod._load_lots(session, "US", None, account_id="acct-a")
        assert len(owned) == 1
        owned_us = pnl_mod._load_lots(
            session, "US", None, user_strategy_id="ustrat-b"
        )
        assert len(owned_us) == 1
    finally:
        session.close()
