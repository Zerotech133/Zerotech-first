"""real_pg：sweep 对真实平台开仓建仓 + 撤订冻结（需 REAL_PG=1 + PG 已迁到 0032）。"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytestmark = pytest.mark.real_pg


def _pg_ok() -> bool:
    try:
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session
        s = get_session()
        try:
            s.execute(text("select 1"))
            return True
        finally:
            s.close()
    except Exception:
        return False


@pytest.fixture()
def pg():
    if not (os.getenv("REAL_PG") == "1" and _pg_ok()):
        pytest.skip("需 REAL_PG=1 且真 PG 到 0032")
    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.session import get_session
    sfx = datetime.now(UTC).strftime("%H%M%S%f")
    uid, sid, svid = f"zm_u_{sfx}", f"zm_s_{sfx}", f"zm_sv_{sfx}"
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    acct_id = f"zm_acct_{sfx}"
    s = get_session()
    try:
        # flush 顺序遵循 FK 依赖（ORM 无关系定义，需手动分批）：
        # user → strategy → subscription
        #   → account → version → user_strategy_instance → position
        s.add(orm.UserModel(user_id=uid, username=uid, password_hash="x", role="USER",
                            is_active=True, locale="zh", created_at=t0))
        s.add(orm.StrategyModel(strategy_id=sid, strategy_name=sid, market="CRYPTO", status="ACTIVE",
                                owner_user_id=None, visibility="PUBLIC", publication_status="PUBLISHED",
                                created_at=t0, updated_at=t0))
        s.flush()  # user + strategy 先落库
        s.add(orm.StrategySubscriptionModel(user_id=uid, strategy_id=sid, status="active",
                                            is_permanent=True, version=1, created_at=t0, updated_at=t0))
        # v2_position.user_strategy_id 有 FK → v2_user_strategy_instance；
        # "ustrat-legacy-" 前缀是 platform_open_positions 的过滤条件。
        # 需先建 account → version → user_strategy_instance，再建 position。
        s.add(orm.UserAccountModel(
            account_id=acct_id, user_id=uid, market="CRYPTO", currency="USDT",
            account_type="STRATEGY_DEDICATED", initial_capital=Decimal("10000"),
            cash_balance=Decimal("10000"), created_at=t0, updated_at=t0,
        ))
        s.flush()  # subscription + account 依赖 user
        s.add(orm.StrategyVersionModel(strategy_version_id=svid, strategy_id=sid, version_no="1",
                                       market="CRYPTO", status="ACTIVE", spec={}, created_at=t0))
        s.flush()  # version 依赖 strategy
        s.add(orm.UserStrategyInstanceModel(
            user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO",
            user_id=uid, strategy_version_id=svid, account_id=acct_id,
            created_at=t0, updated_at=t0,
        ))
        s.flush()  # user_strategy_instance 依赖 user + version + account
        s.add(orm.PositionModel(position_id=f"p_{sfx}", strategy_version_id=svid, market="CRYPTO",
                                ts_code="BTCUSDT", direction="LONG", open_qty=Decimal("2"),
                                open_price=Decimal("100"), status="OPEN", opened_at=t0 + timedelta(days=10),
                                user_strategy_id=f"ustrat-legacy-{svid}:CRYPTO"))
        s.commit()
    finally:
        s.close()
    yield {"uid": uid, "sid": sid, "svid": svid}
    c = get_session()
    try:
        c.execute(text("DELETE FROM v2_mirror_sim_fill WHERE mirror_account_id IN "
                       "(SELECT mirror_account_id FROM v2_mirror_sim_account WHERE user_id=:u)"), {"u": uid})
        c.execute(text("DELETE FROM v2_mirror_sim_position WHERE mirror_account_id IN "
                       "(SELECT mirror_account_id FROM v2_mirror_sim_account WHERE user_id=:u)"), {"u": uid})
        c.execute(text("DELETE FROM v2_mirror_sim_account WHERE user_id=:u"), {"u": uid})
        c.execute(text("DELETE FROM v2_position WHERE strategy_version_id=:v"), {"v": svid})
        c.execute(text("DELETE FROM v2_user_strategy_instance WHERE strategy_version_id=:v"), {"v": svid})
        c.execute(text("DELETE FROM v2_user_account WHERE account_id=:a"), {"a": acct_id})
        c.execute(text("DELETE FROM v2_strategy_version WHERE strategy_version_id=:v"), {"v": svid})
        c.execute(text("DELETE FROM v2_strategy_subscription WHERE user_id=:u"), {"u": uid})
        c.execute(text("DELETE FROM v2_strategy WHERE strategy_id=:s"), {"s": sid})
        c.execute(text("DELETE FROM v2_user WHERE user_id=:u"), {"u": uid})
        c.commit()
    finally:
        c.close()


def test_sweep_builds_position_then_freeze(pg, monkeypatch):
    import zhice_os_v2.application.services.mirror_sim_service as svc_mod
    monkeypatch.setattr(svc_mod, "_latest_prices", lambda syms, market: {"BTCUSDT": 100.0})
    monkeypatch.setattr(svc_mod, "_now", lambda: datetime(2026, 9, 6, tzinfo=UTC))

    class U:
        user_id = pg["uid"]
    svc = svc_mod.MirrorSimService()
    svc.create_account(U(), strategy_id=pg["sid"], initial_capital=Decimal("10000"))
    svc.sweep()
    d = svc.detail(U(), strategy_id=pg["sid"])
    pos = {p["symbol"]: p for p in d["positions"]}
    assert "BTCUSDT" in pos and float(pos["BTCUSDT"]["quantity"]) > 0

    from sqlalchemy import text

    from zhice_os_v2.infrastructure.db.session import get_session
    s = get_session()
    # 撤销订阅：需同时设 revoked_at + revoked_by_user_id 满足 PG 约束：
    # ck_v2_strategy_subscription_revoked_status: (status='revoked') = (revoked_at IS NOT NULL)
    # ck_v2_strategy_subscription_revocation_pair: (revoked_at IS NULL) = (revoked_by_user_id IS NULL)
    # 另：is_subscription_active 域函数靠 revoked_at 判断（非 status 字段）
    s.execute(text(
        "UPDATE v2_strategy_subscription "
        "SET status='revoked', revoked_at=now(), revoked_by_user_id=:u "
        "WHERE user_id=:u"
    ), {"u": pg["uid"]})
    s.commit()
    s.close()
    svc.sweep()
    assert svc.detail(U(), strategy_id=pg["sid"])["status"] == "frozen"
