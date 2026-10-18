"""回归：删除策略前清理引用它的发布申请(source/derived FK)与订阅行，避免 500。

复现的线上故障：DELETE /strategies/{id} 命中
v2_strategy_publication_request_source_strategy_id_fkey → ForeignKeyViolation 500。
后续发现 v2_strategy_subscription.strategy_id 同为无级联 FK，被订阅过的内置策略
删除时会复现同类 500。本测试隔离内存 sqlite，验 StrategyService._purge_strategy_references
在单事务内删除两张引用表中指向该策略的行、保留无关行。
"""
from __future__ import annotations

import types
from datetime import UTC, datetime
from decimal import Decimal

import pytest


def _mk_req(request_id, *, source, derived=None):
    from zhice_os_v2.infrastructure.db import models as orm

    return orm.StrategyPublicationRequestModel(
        request_id=request_id,
        source_strategy_id=source,
        requester_user_id="u-req",
        status="PENDING",
        derived_strategy_id=derived,
        created_at=datetime.now(UTC),
    )


def _mk_sub(user_id, strategy_id):
    from zhice_os_v2.infrastructure.db import models as orm

    return orm.StrategySubscriptionModel(
        user_id=user_id,
        strategy_id=strategy_id,
        valid_until=datetime.now(UTC),
        status="active",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def _sqlite_session(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.config.settings as settings_mod
    import zhice_os_v2.infrastructure.db.session as session_mod
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "get_session", lambda: Session())
    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: types.SimpleNamespace(persistence_backend="sql"),
    )
    return Session


def test_purge_strategy_references_removes_only_referencing(monkeypatch):
    from sqlalchemy import text

    from zhice_os_v2.application.services.strategy_service import StrategyService

    Session = _sqlite_session(monkeypatch)
    s = Session()
    s.add_all(
        [
            _mk_req("r-src", source="strat-X"),  # 发布申请：source 引用
            _mk_req("r-drv", source="strat-Y", derived="strat-X"),  # derived 引用
            _mk_req("r-other", source="strat-Z"),  # 无关，应保留
            _mk_sub("u-1", "strat-X"),  # 订阅：引用（被订阅的内置策略）
            _mk_sub("u-2", "strat-Z"),  # 订阅：无关，应保留
        ]
    )
    s.commit()
    s.close()

    StrategyService._purge_strategy_references("strat-X")

    s2 = Session()
    reqs = [
        r[0]
        for r in s2.execute(
            text(
                "SELECT request_id FROM v2_strategy_publication_request "
                "ORDER BY request_id"
            )
        ).fetchall()
    ]
    subs = [
        r[0]
        for r in s2.execute(
            text("SELECT strategy_id FROM v2_strategy_subscription ORDER BY strategy_id")
        ).fetchall()
    ]
    s2.close()
    assert reqs == ["r-other"], reqs
    assert subs == ["strat-Z"], subs


def test_purge_strategy_references_noop_on_memory_backend(monkeypatch):
    """内存后端跳过（不触 DB），不抛异常。"""
    import zhice_os_v2.config.settings as settings_mod
    from zhice_os_v2.application.services.strategy_service import StrategyService

    monkeypatch.setattr(
        settings_mod,
        "get_settings",
        lambda: types.SimpleNamespace(persistence_backend="memory"),
    )
    StrategyService._purge_strategy_references("whatever")  # 不应抛异常（直接 return）


# ── C-2：策略删除对有支付历史 RESTRICT ──────────────────────────────────

def _mk_order(order_id, strategy_id, *, status="pending", grant_status="pending"):
    from zhice_os_v2.infrastructure.db import models as orm

    return orm.PaymentOrderModel(
        order_id=order_id, user_id="u-1", product_type="subscription",
        product_ref=strategy_id, months=1, amount_usdt=Decimal("9.9"),
        trade_type="usdt.trc20", status=status, grant_status=grant_status,
        product_name_snapshot="缠论", created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_payment_order_ids_lists_all_referencing_orders(monkeypatch):
    from zhice_os_v2.application.services.strategy_service import StrategyService

    Session = _sqlite_session(monkeypatch)
    s = Session()
    s.add_all([
        _mk_order("o-x1", "strat-X", status="pending"),
        _mk_order("o-x2", "strat-X", status="paid", grant_status="granted"),
        _mk_order("o-z", "strat-Z", status="paid", grant_status="granted"),
    ])
    s.commit()
    s.close()
    # 任何状态订单（含已发放 granted）都计入 —— 用户决策：任何支付订单都拒删
    assert StrategyService._payment_order_ids_for_strategy("strat-X") == ["o-x1", "o-x2"]
    assert StrategyService._payment_order_ids_for_strategy("strat-Z") == ["o-z"]
    assert StrategyService._payment_order_ids_for_strategy("strat-none") == []


def test_payment_order_ids_noop_on_memory_backend(monkeypatch):
    import zhice_os_v2.config.settings as settings_mod
    from zhice_os_v2.application.services.strategy_service import StrategyService

    monkeypatch.setattr(
        settings_mod, "get_settings",
        lambda: types.SimpleNamespace(persistence_backend="memory"),
    )
    assert StrategyService._payment_order_ids_for_strategy("whatever") == []


def test_delete_refuses_when_strategy_has_payment_orders(monkeypatch):
    from zhice_os_v2.application.services.strategy_service import StrategyService

    Session = _sqlite_session(monkeypatch)
    s = Session()
    s.add(_mk_order("o-x1", "strat-X", status="paid", grant_status="granted"))
    s.commit()
    s.close()

    fake_db = types.SimpleNamespace(
        list_strategy_versions=lambda: [
            types.SimpleNamespace(strategy_id="strat-X", strategy_version_id="sv-x")
        ],
        list_open_positions=lambda: [],
    )
    svc = StrategyService(fake_db)
    monkeypatch.setattr(svc, "_bound_instance_repo", lambda: None)

    with pytest.raises(PermissionError) as ei:
        svc.delete("strat-X")
    assert ei.value.args[0] == "strategy.has_payment_orders"
    assert "o-x1" in ei.value.args[1]

