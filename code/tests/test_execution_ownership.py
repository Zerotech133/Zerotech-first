"""Phase B Task 1：交易链归属 schema/字段的持久化 round-trip 测试。

只覆盖 Task 1（纯 additive nullable 归属列 + 领域字段 + repository 双向读写）。
不含 OwnershipResolver（Task 2）。

⚠️ 本仓历史上 save_* 曾是 no-op 导致页面空 —— 本测试对五类交易对象逐一断言
「存进去再读出来，两归属字段相等」，并验证 legacy（不带归属字段）仍能读回 None。

归属字段：
- user_strategy_id -> v2_user_strategy_instance.user_strategy_id
- account_id       -> v2_user_account.account_id

OrderIntent 无领域 dataclass、亦无 repository save/get（由 workflow_service 直接 ORM
upsert 写入），故其 round-trip 在 ORM 层直接断言两新列可写可读、缺省为 NULL。
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import BigInteger, create_engine, select
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.domain.approval.approval import ApprovalRequest


# v2_trade_plan / v2_order_intent 用 BigInteger 主键；SQLite 仅 INTEGER PRIMARY KEY
# 走 rowid 自增，BigInteger 编译成 BIGINT 不自增 → 插入报 NOT NULL。测试内把
# BigInteger 在 sqlite 方言下编译为 INTEGER（SQLite 整数存储类唯一，语义等价），
# 让 save_trade_plan 无需显式 id 即可 round-trip。仅影响 sqlite，PG 仍为 BIGINT。
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001, ANN202
    return "INTEGER"
from dataclasses import dataclass

from zhice_os_v2.application.services.ownership_resolver import OwnershipResolver
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.replay.replay import ReplayCase
from zhice_os_v2.infrastructure.db import models as orm  # noqa: F401
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase

_USER_STRATEGY_ID = "usr-inst-001"
_ACCOUNT_ID = "acct-001"


@pytest.fixture
def sql_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


@pytest.fixture
def sql_session_factory(sql_engine):
    return sessionmaker(bind=sql_engine, expire_on_commit=False)


@pytest.fixture
def sql_db(sql_session_factory):
    return SqlAlchemyDatabase(sql_session_factory())


def test_new_plan_persists_runtime_ownership(sql_db):
    plan = TradePlan(
        plan_id="plan-own-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        qty=Decimal("100"),
        ref_price=Decimal("1700"),
        user_strategy_id=_USER_STRATEGY_ID,
        account_id=_ACCOUNT_ID,
    )
    sql_db.save_trade_plan(plan)
    stored = sql_db.get_trade_plan("plan-own-1")
    assert stored is not None
    assert stored.user_strategy_id == _USER_STRATEGY_ID
    assert stored.account_id == _ACCOUNT_ID


def test_position_persists_runtime_ownership(sql_db):
    lot = PositionLot(
        position_id="pos-own-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("1700"),
        user_strategy_id=_USER_STRATEGY_ID,
        account_id=_ACCOUNT_ID,
    )
    sql_db.save_position(lot)
    stored = sql_db.get_position("pos-own-1")
    assert stored is not None
    assert stored.user_strategy_id == _USER_STRATEGY_ID
    assert stored.account_id == _ACCOUNT_ID


def test_approval_persists_runtime_ownership(sql_db):
    request = ApprovalRequest(
        request_id="apr-own-1",
        order_intent_id="oi-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        qty=100,
        price=1700.0,
        reason="test",
        user_strategy_id=_USER_STRATEGY_ID,
        account_id=_ACCOUNT_ID,
    )
    sql_db.save_approval(request)
    stored = sql_db.get_approval("apr-own-1")
    assert stored is not None
    assert stored.user_strategy_id == _USER_STRATEGY_ID
    assert stored.account_id == _ACCOUNT_ID
    # list 路径也须带出归属
    listed = {a.request_id: a for a in sql_db.list_approvals()}
    assert listed["apr-own-1"].user_strategy_id == _USER_STRATEGY_ID
    assert listed["apr-own-1"].account_id == _ACCOUNT_ID


def test_replay_persists_runtime_ownership(sql_db):
    replay = ReplayCase(
        replay_id="rep-own-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        pnl=Decimal("100"),
        pnl_pct=Decimal("0.01"),
        holding_period_seconds=3600,
        exit_reason="TAKE_PROFIT",
        user_strategy_id=_USER_STRATEGY_ID,
        account_id=_ACCOUNT_ID,
    )
    sql_db.save_replay(replay)
    stored = {r.replay_id: r for r in sql_db.list_replays()}["rep-own-1"]
    assert stored.user_strategy_id == _USER_STRATEGY_ID
    assert stored.account_id == _ACCOUNT_ID


def test_order_intent_orm_persists_runtime_ownership(sql_session_factory):
    """OrderIntent 无领域对象/无 repo save-get：ORM 层直接验证两新列可写可读。"""
    session = sql_session_factory()
    session.add(
        orm.OrderIntentModel(
            intent_id="oi-own-1",
            plan_id="plan-own-1",
            strategy_version_id="sv-1",
            market="CN",
            ts_code="600519.SH",
            intent_type="OPEN",
            intent_side="BUY",
            intent_qty=Decimal("100"),
            intent_price=Decimal("1700"),
            status="FILLED",
            created_at=datetime.now(UTC),
            user_strategy_id=_USER_STRATEGY_ID,
            account_id=_ACCOUNT_ID,
        )
    )
    session.commit()
    row = session.execute(
        select(orm.OrderIntentModel).where(
            orm.OrderIntentModel.intent_id == "oi-own-1"
        )
    ).scalar_one()
    assert row.user_strategy_id == _USER_STRATEGY_ID
    assert row.account_id == _ACCOUNT_ID


def test_legacy_save_without_ownership_reads_none(sql_db, sql_session_factory):
    """legacy 兼容：旧式 save（不带归属字段）仍能正常 get，两字段为 None。"""
    plan = TradePlan(
        plan_id="plan-legacy-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        qty=Decimal("100"),
        ref_price=Decimal("1700"),
    )
    sql_db.save_trade_plan(plan)
    stored = sql_db.get_trade_plan("plan-legacy-1")
    assert stored is not None
    assert stored.user_strategy_id is None
    assert stored.account_id is None

    lot = PositionLot(
        position_id="pos-legacy-1",
        strategy_version_id="sv-1",
        market="CN",
        ts_code="600519.SH",
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("1700"),
    )
    sql_db.save_position(lot)
    stored_lot = sql_db.get_position("pos-legacy-1")
    assert stored_lot is not None
    assert stored_lot.user_strategy_id is None
    assert stored_lot.account_id is None

    # ORM intent legacy：不设归属 → NULL
    session = sql_session_factory()
    session.add(
        orm.OrderIntentModel(
            intent_id="oi-legacy-1",
            plan_id="plan-legacy-1",
            strategy_version_id="sv-1",
            market="CN",
            ts_code="600519.SH",
            intent_type="OPEN",
            intent_side="BUY",
            intent_qty=Decimal("100"),
            intent_price=Decimal("1700"),
            status="FILLED",
            created_at=datetime.now(UTC),
        )
    )
    session.commit()
    row = session.execute(
        select(orm.OrderIntentModel).where(
            orm.OrderIntentModel.intent_id == "oi-legacy-1"
        )
    ).scalar_one()
    assert row.user_strategy_id is None
    assert row.account_id is None


# ---------------------------------------------------------------------------
# Task 2：OwnershipResolver —— 交易链 owner 解析（纯读）。
#
# 解析链：
#   plan/approval/intent/position/replay  → 直接读该行 user_strategy_id
#   request  → intent（order_request.intent_id → order_intent.user_strategy_id）
#   execution→ request → intent
#   fill     → execution → request → intent
# 拿到 user_strategy_id 后，以 v2_user_strategy_instance 为权威补齐
# user_id/account_id/strategy_version_id。
# legacy/缺失/断链/实例查不到 → None，**绝不**按 strategy_version_id 猜 owner。
# ---------------------------------------------------------------------------

_GRAPH_USER_ID = "u-graph"
_GRAPH_USTRAT_ID = "ustrat-graph"
_GRAPH_ACCOUNT_ID = "acct-graph"
_GRAPH_VERSION_ID = "sv-graph"


@dataclass(frozen=True)
class _OwnedGraph:
    user_id: str
    user_strategy_id: str
    account_id: str
    strategy_version_id: str
    ids: dict[str, str]

    def id_for(self, entity_type: str) -> str:
        return self.ids[entity_type]


@pytest.fixture
def ownership_resolver(sql_session_factory):
    return OwnershipResolver(session_factory=sql_session_factory)


@pytest.fixture
def owned_graph(sql_session_factory) -> _OwnedGraph:
    """插入 1 个运行实例 + 每类交易对象各一条（含完整 request/execution/fill 链）。

    直接归属对象（plan/approval/intent/position/replay）都写 user_strategy_id=实例；
    request→intent(oi-g)、execution→request(req-g)、fill→execution(exec-g) 靠外键串联，
    三张表本身**不**带归属列，全靠链路解析。
    """
    now = datetime.now(UTC)
    usid, acct, sv = _GRAPH_USTRAT_ID, _GRAPH_ACCOUNT_ID, _GRAPH_VERSION_ID
    session = sql_session_factory()
    session.add(
        orm.UserStrategyInstanceModel(
            user_strategy_id=usid,
            user_id=_GRAPH_USER_ID,
            strategy_version_id=sv,
            account_id=acct,
            created_at=now,
            updated_at=now,
        )
    )
    session.add(
        orm.TradePlanModel(
            plan_id="plan-g",
            candidate_id="",
            strategy_version_id=sv,
            market="CN",
            ts_code="600519.SH",
            plan_status="NEW",
            created_at=now,
            user_strategy_id=usid,
            account_id=acct,
        )
    )
    session.add(
        orm.ApprovalRequestModel(
            request_id="apr-g",
            order_intent_id="oi-g",
            strategy_version_id=sv,
            market="CN",
            ts_code="600519.SH",
            direction="BUY",
            qty=Decimal("100"),
            status="PENDING",
            channel="WEB",
            token="tok",
            created_at=now,
            user_strategy_id=usid,
            account_id=acct,
        )
    )
    session.add(
        orm.OrderIntentModel(
            intent_id="oi-g",
            plan_id="plan-g",
            strategy_version_id=sv,
            market="CN",
            ts_code="600519.SH",
            intent_type="OPEN",
            intent_side="BUY",
            intent_qty=Decimal("100"),
            intent_price=Decimal("1700"),
            status="NEW",
            created_at=now,
            user_strategy_id=usid,
            account_id=acct,
        )
    )
    session.add(
        orm.PositionModel(
            position_id="pos-g",
            strategy_version_id=sv,
            market="CN",
            ts_code="600519.SH",
            direction="BUY",
            open_qty=Decimal("100"),
            open_price=Decimal("1700"),
            status="OPEN",
            opened_at=now,
            user_strategy_id=usid,
            account_id=acct,
        )
    )
    session.add(
        orm.ReplayModel(
            replay_id="rep-g",
            strategy_version_id=sv,
            market="CN",
            ts_code="600519.SH",
            pnl=Decimal("1"),
            pnl_pct=Decimal("0.01"),
            holding_period_seconds=3600,
            exit_reason="TAKE_PROFIT",
            created_at=now,
            user_strategy_id=usid,
            account_id=acct,
        )
    )
    # 订单链（不带归属列，靠外键解析）：request→intent、execution→request、fill→execution
    session.add(
        orm.OrderRequestModel(
            request_id="req-g",
            intent_id="oi-g",
            market="CN",
            broker_channel="SIM",
            status="SENT",
            created_at=now,
        )
    )
    session.add(
        orm.ExecutionRecordModel(
            execution_id="exec-g",
            request_id="req-g",
            market="CN",
            execution_status="FILLED",
            created_at=now,
        )
    )
    session.add(
        orm.FillRecordModel(
            fill_id="fill-g",
            execution_id="exec-g",
            market="CN",
            ts_code="600519.SH",
            created_at=now,
        )
    )
    session.commit()
    session.close()
    return _OwnedGraph(
        user_id=_GRAPH_USER_ID,
        user_strategy_id=usid,
        account_id=acct,
        strategy_version_id=sv,
        ids={
            "plan": "plan-g",
            "approval": "apr-g",
            "intent": "oi-g",
            "position": "pos-g",
            "replay": "rep-g",
            "request": "req-g",
            "execution": "exec-g",
            "fill": "fill-g",
        },
    )


@pytest.mark.parametrize(
    "entity_type",
    ["plan", "approval", "intent", "request", "execution", "fill", "position", "replay"],
)
def test_resolve_owner_for_trade_entity(ownership_resolver, owned_graph, entity_type):
    owner = ownership_resolver.resolve(entity_type, owned_graph.id_for(entity_type))
    assert owner is not None
    assert owner.user_id == owned_graph.user_id
    assert owner.user_strategy_id == owned_graph.user_strategy_id
    assert owner.account_id == owned_graph.account_id
    assert owner.strategy_version_id == owned_graph.strategy_version_id


def test_for_fill_convenience_wrapper(ownership_resolver, owned_graph):
    owner = ownership_resolver.for_fill(owned_graph.id_for("fill"))
    assert owner is not None
    assert owner.user_id == owned_graph.user_id
    assert owner.user_strategy_id == owned_graph.user_strategy_id
    assert owner.account_id == owned_graph.account_id
    assert owner.strategy_version_id == owned_graph.strategy_version_id


def test_missing_entity_returns_none(ownership_resolver):
    assert ownership_resolver.resolve("plan", "no-such-plan") is None
    assert ownership_resolver.for_fill("no-such-fill") is None


def test_unknown_entity_type_raises(ownership_resolver):
    with pytest.raises(ValueError, match="entity_type"):
        ownership_resolver.resolve("candidate", "whatever")


def test_legacy_null_owner_never_guesses_by_version(
    ownership_resolver, sql_session_factory, owned_graph
):
    """legacy 行 user_strategy_id=NULL：即使与实例共享同一 strategy_version_id，
    也**绝不**据版本回猜 owner（同版本多用户共享，猜必错）→ None。"""
    now = datetime.now(UTC)
    session = sql_session_factory()
    session.add(
        orm.TradePlanModel(
            plan_id="plan-legacy-owner",
            candidate_id="",
            strategy_version_id=owned_graph.strategy_version_id,  # 共享版本，诱导误猜
            market="CN",
            ts_code="600519.SH",
            plan_status="NEW",
            created_at=now,
            # 故意不设 user_strategy_id / account_id → NULL
        )
    )
    session.commit()
    session.close()
    assert ownership_resolver.resolve("plan", "plan-legacy-owner") is None


def test_broken_order_chain_returns_none(ownership_resolver, sql_session_factory):
    """链路断裂（request 指向不存在的 intent）→ None。"""
    now = datetime.now(UTC)
    session = sql_session_factory()
    session.add(
        orm.OrderRequestModel(
            request_id="req-orphan",
            intent_id="intent-missing",
            market="CN",
            broker_channel="SIM",
            status="SENT",
            created_at=now,
        )
    )
    session.commit()
    session.close()
    assert ownership_resolver.resolve("request", "req-orphan") is None


def test_instance_lookup_miss_returns_none(ownership_resolver, sql_session_factory):
    """归属列有值但实例查不到（幽灵 user_strategy_id）→ None，不臆造 owner。"""
    now = datetime.now(UTC)
    session = sql_session_factory()
    session.add(
        orm.PositionModel(
            position_id="pos-ghost",
            strategy_version_id="sv-x",
            market="CN",
            ts_code="600519.SH",
            direction="BUY",
            open_qty=Decimal("100"),
            open_price=Decimal("1700"),
            status="OPEN",
            opened_at=now,
            user_strategy_id="ustrat-ghost",
            account_id="acct-ghost",
        )
    )
    session.commit()
    session.close()
    assert ownership_resolver.resolve("position", "pos-ghost") is None
