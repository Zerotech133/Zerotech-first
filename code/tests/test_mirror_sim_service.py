from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest


def _mk_engine():
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool

    from zhice_os_v2.infrastructure.db.base import Base

    eng = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


def test_mirror_orm_roundtrip():
    from sqlalchemy.orm import sessionmaker

    from zhice_os_v2.infrastructure.db import models as orm

    Session = sessionmaker(bind=_mk_engine(), expire_on_commit=False)
    s = Session()
    s.add(orm.MirrorSimAccountModel(
        mirror_account_id="msim-x", user_id="u1", strategy_id="v1-crypto-strat-11",
        market="CRYPTO", initial_capital=Decimal("10000"), cash_balance=Decimal("10000"),
        status="active", created_at=datetime.now(UTC), updated_at=datetime.now(UTC),
    ))
    s.commit()
    row = s.query(orm.MirrorSimAccountModel).filter_by(mirror_account_id="msim-x").one()
    assert row.status == "active" and row.cash_balance == Decimal("10000")
    s.close()


def test_target_weights_and_rebalance_math():
    from zhice_os_v2.domain.mirror.mirror_sim import compute_target_mv, rebalance_orders

    plat_mv = {"A": 600.0, "B": 400.0}
    tracked = {"A", "B"}
    total_plat = 1000.0
    equity = 10000.0
    target = compute_target_mv(plat_mv, tracked, total_plat, equity)
    assert target == {"A": 6000.0, "B": 4000.0}

    orders = rebalance_orders(
        target_mv=target, user_qty={}, prices={"A": 100.0, "B": 50.0},
        cash=10000.0, equity=equity, churn_threshold=0.01,
    )
    buys = {o.symbol: o for o in orders if o.side == "BUY"}
    assert abs(buys["A"].quantity - 60.0) < 1e-6
    assert abs(buys["B"].quantity - 80.0) < 1e-6


def test_rebalance_cash_capped_and_threshold():
    from zhice_os_v2.domain.mirror.mirror_sim import rebalance_orders
    orders = rebalance_orders(target_mv={"A": 6000.0}, user_qty={}, prices={"A": 100.0},
                              cash=1000.0, equity=1000.0, churn_threshold=0.01)
    assert len(orders) == 1 and abs(orders[0].quantity - 10.0) < 1e-6
    none_orders = rebalance_orders(target_mv={"A": 5050.0}, user_qty={"A": 50.0}, prices={"A": 100.0},
                                   cash=5000.0, equity=10000.0, churn_threshold=0.01)
    assert none_orders == []


@pytest.fixture()
def repo_env(monkeypatch):
    from sqlalchemy.orm import sessionmaker

    import zhice_os_v2.infrastructure.repositories.mirror_sim_repository as mod

    Session = sessionmaker(bind=_mk_engine(), expire_on_commit=False)
    monkeypatch.setattr(mod, "get_session", lambda: Session())
    return Session, mod


def test_repo_create_and_get(repo_env):
    from datetime import UTC, datetime
    from decimal import Decimal
    Session, mod = repo_env
    repo = mod.MirrorSimRepository()
    now = datetime.now(UTC)
    repo.create_account(mirror_account_id="msim-1", user_id="u1", strategy_id="s1",
                        market="CRYPTO", initial_capital=Decimal("10000"), now=now)
    acc = repo.get_by_user_strategy("u1", "s1")
    assert acc is not None and acc.cash_balance == Decimal("10000") and acc.status == "active"
    assert repo.get_by_user_strategy("u1", "nope") is None


def test_create_account_gating(repo_env, monkeypatch):
    from decimal import Decimal

    import pytest as _pytest

    import zhice_os_v2.application.services.mirror_sim_service as svc_mod

    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: uid == "u1" and sid == "s1")
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: sid == "s1")
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")

    svc = svc_mod.MirrorSimService()
    err = svc_mod.MirrorSimError

    class U:
        user_id = "u1"

    with _pytest.raises(err) as e:
        svc.create_account(U(), strategy_id="other", initial_capital=Decimal("10000"))
    assert e.value.code == "mirror.subscription_required"

    with _pytest.raises(err) as e:
        svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("0"))
    assert e.value.code == "mirror.invalid_capital"

    view = svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    assert view["strategy_id"] == "s1" and view["status"] == "active"

    with _pytest.raises(err) as e:
        svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    assert e.value.code == "mirror.account_exists"


def _seed_platform(Session, *, strategy_id="s1", svid="sv1"):
    """建策略/版本 + 两条平台开仓（A 早于账户、B 晚于账户）。返回 t0。"""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    from zhice_os_v2.infrastructure.db import models as orm
    s = Session()
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    s.add(orm.StrategyModel(strategy_id=strategy_id, strategy_name="x", market="CRYPTO",
                            status="ACTIVE", owner_user_id=None, visibility="PUBLIC",
                            publication_status="PUBLISHED", created_at=t0, updated_at=t0))
    s.add(orm.StrategyVersionModel(strategy_version_id=svid, strategy_id=strategy_id,
                                   version_no="1", market="CRYPTO", status="ACTIVE", spec={}, created_at=t0))
    s.add(orm.PositionModel(position_id="pA", strategy_version_id=svid, market="CRYPTO", ts_code="A",
                            direction="LONG", open_qty=Decimal("6"), open_price=Decimal("100"),
                            status="OPEN", opened_at=t0, user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO"))
    s.add(orm.PositionModel(position_id="pB", strategy_version_id=svid, market="CRYPTO", ts_code="B",
                            direction="LONG", open_qty=Decimal("8"), open_price=Decimal("50"),
                            status="OPEN", opened_at=t0 + timedelta(days=10),
                            user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO"))
    s.commit()
    s.close()
    return t0


def test_sweep_forward_only_and_convergence(repo_env, monkeypatch):
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    import zhice_os_v2.application.services.mirror_sim_service as svc_mod

    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: True)
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: True)
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: {"A": 100.0, "B": 50.0})

    _seed_platform(Session)
    created = datetime(2026, 9, 6, tzinfo=UTC)  # A(9-1)之后、B(9-11)之前 → 只跟 B
    monkeypatch.setattr(svc_mod, "_now", lambda: created)
    svc = svc_mod.MirrorSimService()

    class U:
        user_id = "u1"
    svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))

    monkeypatch.setattr(svc_mod, "_now", lambda: created + timedelta(days=11))
    svc.sweep()
    pos = {p["symbol"]: p for p in svc.detail(U(), strategy_id="s1")["positions"]}
    assert "A" not in pos
    assert abs(float(pos["B"]["quantity"]) - 80.0) < 1e-6

    svc.sweep()  # 收敛幂等
    pos2 = {p["symbol"]: p for p in svc.detail(U(), strategy_id="s1")["positions"]}
    assert abs(float(pos2["B"]["quantity"]) - 80.0) < 1e-6


def test_freeze_on_revoke_and_resume(repo_env, monkeypatch):
    from datetime import UTC, datetime
    from decimal import Decimal

    import zhice_os_v2.application.services.mirror_sim_service as svc_mod
    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    sub = {"v": True}
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: sub["v"])
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: True)
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: {"A": 100.0, "B": 50.0})
    monkeypatch.setattr(svc_mod, "_now", lambda: datetime(2026, 9, 6, tzinfo=UTC))
    _seed_platform(Session)
    svc = svc_mod.MirrorSimService()

    class U:
        user_id = "u1"
    svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    sub["v"] = False
    svc.sweep()
    assert svc.detail(U(), strategy_id="s1")["status"] == "frozen"
    sub["v"] = True
    svc.sweep()
    assert svc.detail(U(), strategy_id="s1")["status"] == "active"


def test_sweep_skips_account_when_held_symbol_price_missing(repo_env, monkeypatch):
    """持仓标的缺价 → 本轮整账户跳过（不误动持仓），下轮重试。"""
    from datetime import UTC, datetime, timedelta
    from decimal import Decimal

    import zhice_os_v2.application.services.mirror_sim_service as svc_mod

    Session, _ = repo_env
    monkeypatch.setattr(svc_mod, "get_session", lambda: Session(), raising=False)
    monkeypatch.setattr(svc_mod, "_is_subscription_active", lambda uid, sid: True)
    monkeypatch.setattr(svc_mod, "_is_observable_builtin", lambda sid: True)
    monkeypatch.setattr(svc_mod, "_strategy_market", lambda sid: "CRYPTO")
    prices = {"A": 100.0, "B": 50.0}
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: dict(prices))

    _seed_platform(Session)
    created = datetime(2026, 9, 6, tzinfo=UTC)
    monkeypatch.setattr(svc_mod, "_now", lambda: created)
    svc = svc_mod.MirrorSimService()

    class U:
        user_id = "u1"
    svc.create_account(U(), strategy_id="s1", initial_capital=Decimal("10000"))
    monkeypatch.setattr(svc_mod, "_now", lambda: created + timedelta(days=11))
    svc.sweep()  # 建 B=80
    b0 = {p["symbol"]: p for p in svc.detail(U(), strategy_id="s1")["positions"]}["B"]
    assert abs(float(b0["quantity"]) - 80.0) < 1e-6

    # 持仓标的 B 现价缺失 → 守卫使该账户本轮跳过，B 持仓不被改动
    prices.pop("B")
    stats = svc.sweep()
    assert stats["orders"] == 0
    b1 = {p["symbol"]: p for p in svc.detail(U(), strategy_id="s1")["positions"]}["B"]
    assert abs(float(b1["quantity"]) - 80.0) < 1e-6


def test_platform_open_positions_ownership_predicate(repo_env):
    """平台开仓判据：活跃实盘开仓 user_strategy_id 为空(管线不赋归属)算平台；
    历史 ustrat-legacy-* 算平台；真实私有实例 usi-/ustrat-{hex} 不算。"""
    from datetime import UTC, datetime
    from decimal import Decimal

    from zhice_os_v2.infrastructure.db import models as orm
    Session, mod = repo_env
    s = Session()
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    s.add(orm.StrategyVersionModel(strategy_version_id="sv9", strategy_id="s9", version_no="1",
                                   market="CRYPTO", status="ACTIVE", spec={}, created_at=t0))
    s.add(orm.PositionModel(position_id="p_live", strategy_version_id="sv9", market="CRYPTO",
                            ts_code="BNB/USDT", direction="LONG", open_qty=Decimal("3"),
                            open_price=Decimal("100"), status="OPEN", opened_at=t0, user_strategy_id=""))
    s.add(orm.PositionModel(position_id="p_legacy", strategy_version_id="sv9", market="CRYPTO",
                            ts_code="SOL/USDT", direction="LONG", open_qty=Decimal("2"),
                            open_price=Decimal("100"), status="OPEN", opened_at=t0,
                            user_strategy_id="ustrat-legacy-sv9:CRYPTO"))
    s.add(orm.PositionModel(position_id="p_priv", strategy_version_id="sv9", market="CRYPTO",
                            ts_code="ETH/USDT", direction="LONG", open_qty=Decimal("1"),
                            open_price=Decimal("100"), status="OPEN", opened_at=t0,
                            user_strategy_id="usi-abc123"))
    s.commit()
    s.close()
    syms = {p["symbol"] for p in mod.MirrorSimRepository().platform_open_positions("s9")}
    assert "BNB/USDT" in syms       # 空归属=平台实盘开仓
    assert "SOL/USDT" in syms       # 历史 legacy
    assert "ETH/USDT" not in syms   # 私有实例不算平台
