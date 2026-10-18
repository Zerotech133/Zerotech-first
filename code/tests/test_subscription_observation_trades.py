"""Test list_platform_trades on SubscriptionObservationService.

Step 1 findings:
- FillRecordModel has NO 'side' field; signal is derived from
  OrderIntentModel.intent_side ("BUY" → "entry", else → "exit").
- Required non-nullable fields must be supplied when constructing ORM rows
  in SQLite (which enforces NOT NULL constraints defined in DDL).
- StrategyVersionModel.strategy_id FK to v2_strategy is NOT enforced by
  SQLite (foreign_keys PRAGMA off by default).
"""
from datetime import datetime

import pytest
from sqlalchemy import BigInteger, create_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


# BigInteger primary keys do not autoincrement in SQLite (only INTEGER does).
# Compile BigInteger as INTEGER for SQLite so all ORM rows can omit explicit id.
# This only affects the SQLite dialect; PostgreSQL still uses BIGINT.
@compiles(BigInteger, "sqlite")
def _compile_bigint_as_integer_sqlite(type_, compiler, **kw):  # noqa: ANN001,ANN202
    return "INTEGER"

from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

_DT = datetime(2026, 1, 1)


@pytest.fixture
def obs_env(monkeypatch):
    import zhice_os_v2.application.services.subscription_observation_service as mod

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mod, "get_session", lambda: Session(), raising=False)
    return Session


def test_list_platform_trades_joins_and_projects_signal(obs_env):
    s = obs_env()
    # StrategyVersionModel: required fields are strategy_version_id, strategy_id,
    # version_no, market, status, spec, created_at.
    # strategy_id FK is not enforced by SQLite.
    s.add(
        orm.StrategyVersionModel(
            strategy_version_id="v1-cn-sv-4",
            strategy_id="v1-cn-strat-4",
            market="CN",
            version_no="v4",
            status="ACTIVE",
            spec={},
            created_at=_DT,
        )
    )
    # OrderIntentModel: required fields include plan_id, strategy_version_id,
    # market, ts_code, intent_type, intent_side, status, created_at.
    # intent_side="BUY" should produce signal="entry".
    s.add(
        orm.OrderIntentModel(
            intent_id="int-1",
            plan_id="plan-1",
            strategy_version_id="v1-cn-sv-4",
            user_strategy_id="ustrat-legacy-v1-cn-sv-4:CN",
            market="CN",
            ts_code="000001",
            intent_type="OPEN",
            intent_side="BUY",
            status="NEW",
            created_at=_DT,
        )
    )
    # OrderRequestModel: required fields are market, broker_channel, status, created_at.
    s.add(
        orm.OrderRequestModel(
            request_id="req-1",
            intent_id="int-1",
            market="CN",
            broker_channel="sim",
            status="PENDING",
            created_at=_DT,
        )
    )
    # ExecutionRecordModel: required fields are market, execution_status, created_at.
    s.add(
        orm.ExecutionRecordModel(
            execution_id="exe-1",
            request_id="req-1",
            market="CN",
            execution_status="FILLED",
            created_at=_DT,
        )
    )
    # FillRecordModel: required fields are market, ts_code, created_at.
    # No 'side' field — signal comes from intent_side above.
    s.add(
        orm.FillRecordModel(
            fill_id="fil-1",
            execution_id="exe-1",
            market="CN",
            ts_code="000001",
            fill_price=10.0,
            fill_qty=100.0,
            fill_amount=1000.0,
            realized_pnl=3.0,
            created_at=_DT,
        )
    )
    s.commit()
    s.close()

    from zhice_os_v2.application.services.subscription_observation_service import (
        SubscriptionObservationService,
    )
    out = SubscriptionObservationService().list_platform_trades("v1-cn-strat-4")
    assert len(out) == 1
    assert out[0]["signal"] == "entry"
    assert out[0]["realized_pnl"] == 3.0
    assert out[0]["ts_code"] == "000001"


def test_list_platform_trades_sell_signal_maps_to_exit(obs_env):
    """SELL intent_side → signal 'exit'."""
    s = obs_env()
    s.add(
        orm.StrategyVersionModel(
            strategy_version_id="v1-cn-sv-4",
            strategy_id="v1-cn-strat-4",
            market="CN",
            version_no="v4",
            status="ACTIVE",
            spec={},
            created_at=_DT,
        )
    )
    s.add(
        orm.OrderIntentModel(
            intent_id="int-sell-1",
            plan_id="plan-sell-1",
            strategy_version_id="v1-cn-sv-4",
            user_strategy_id="ustrat-legacy-v1-cn-sv-4:CN",
            market="CN",
            ts_code="000002",
            intent_type="CLOSE",
            intent_side="SELL",
            status="DONE",
            created_at=_DT,
        )
    )
    s.add(
        orm.OrderRequestModel(
            request_id="req-sell-1",
            intent_id="int-sell-1",
            market="CN",
            broker_channel="sim",
            status="DONE",
            created_at=_DT,
        )
    )
    s.add(
        orm.ExecutionRecordModel(
            execution_id="exe-sell-1",
            request_id="req-sell-1",
            market="CN",
            execution_status="FILLED",
            created_at=_DT,
        )
    )
    s.add(
        orm.FillRecordModel(
            fill_id="fil-sell-1",
            execution_id="exe-sell-1",
            market="CN",
            ts_code="000002",
            fill_price=11.0,
            fill_qty=100.0,
            fill_amount=1100.0,
            realized_pnl=50.0,
            created_at=_DT,
        )
    )
    s.commit()
    s.close()

    from zhice_os_v2.application.services.subscription_observation_service import (
        SubscriptionObservationService,
    )
    out = SubscriptionObservationService().list_platform_trades("v1-cn-strat-4")
    assert len(out) == 1
    assert out[0]["signal"] == "exit"
    assert out[0]["realized_pnl"] == 50.0


def test_list_platform_trades_excludes_non_legacy_intents(obs_env):
    """Fills linked to non-legacy user_strategy_id must be excluded."""
    s = obs_env()
    s.add(
        orm.StrategyVersionModel(
            strategy_version_id="v1-cn-sv-4",
            strategy_id="v1-cn-strat-4",
            market="CN",
            version_no="v4",
            status="ACTIVE",
            spec={},
            created_at=_DT,
        )
    )
    # Intent belongs to a user-owned (non-legacy) instance → should be excluded.
    s.add(
        orm.OrderIntentModel(
            intent_id="int-owned-1",
            plan_id="plan-owned-1",
            strategy_version_id="v1-cn-sv-4",
            user_strategy_id="usi-alice-123",
            market="CN",
            ts_code="000003",
            intent_type="OPEN",
            intent_side="BUY",
            status="NEW",
            created_at=_DT,
        )
    )
    s.add(
        orm.OrderRequestModel(
            request_id="req-owned-1",
            intent_id="int-owned-1",
            market="CN",
            broker_channel="sim",
            status="PENDING",
            created_at=_DT,
        )
    )
    s.add(
        orm.ExecutionRecordModel(
            execution_id="exe-owned-1",
            request_id="req-owned-1",
            market="CN",
            execution_status="FILLED",
            created_at=_DT,
        )
    )
    s.add(
        orm.FillRecordModel(
            fill_id="fil-owned-1",
            execution_id="exe-owned-1",
            market="CN",
            ts_code="000003",
            fill_price=9.0,
            fill_qty=50.0,
            fill_amount=450.0,
            realized_pnl=None,
            created_at=_DT,
        )
    )
    s.commit()
    s.close()

    from zhice_os_v2.application.services.subscription_observation_service import (
        SubscriptionObservationService,
    )
    out = SubscriptionObservationService().list_platform_trades("v1-cn-strat-4")
    assert out == []


def test_list_platform_trades_includes_unowned_live(obs_env):
    """活跃实盘成交 user_strategy_id 为空(管线不赋归属)也应算平台，按版本纳入。"""
    s = obs_env()
    s.add(
        orm.StrategyVersionModel(
            strategy_version_id="v1-cn-sv-4", strategy_id="v1-cn-strat-4", market="CN",
            version_no="v4", status="ACTIVE", spec={}, created_at=_DT,
        )
    )
    s.add(
        orm.OrderIntentModel(
            intent_id="int-live-1", plan_id="plan-live-1", strategy_version_id="v1-cn-sv-4",
            user_strategy_id="", market="CN", ts_code="000004", intent_type="OPEN",
            intent_side="BUY", status="NEW", created_at=_DT,
        )
    )
    s.add(
        orm.OrderRequestModel(
            request_id="req-live-1", intent_id="int-live-1", market="CN",
            broker_channel="sim", status="PENDING", created_at=_DT,
        )
    )
    s.add(
        orm.ExecutionRecordModel(
            execution_id="exe-live-1", request_id="req-live-1", market="CN",
            execution_status="FILLED", created_at=_DT,
        )
    )
    s.add(
        orm.FillRecordModel(
            fill_id="fil-live-1", execution_id="exe-live-1", market="CN", ts_code="000004",
            fill_price=10.0, fill_qty=100.0, fill_amount=1000.0, realized_pnl=7.0, created_at=_DT,
        )
    )
    s.commit()
    s.close()

    from zhice_os_v2.application.services.subscription_observation_service import (
        SubscriptionObservationService,
    )
    out = SubscriptionObservationService().list_platform_trades("v1-cn-strat-4")
    assert len(out) == 1
    assert out[0]["ts_code"] == "000004" and out[0]["signal"] == "entry"
