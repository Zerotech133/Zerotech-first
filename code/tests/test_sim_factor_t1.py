"""P1-A sim-factor T+1 守卫回归测试（隔离 sqlite，不写生产库）。

覆盖：
1. 手工买入当日登记 t1_locked_qty/opened_at；当日卖出被 409 sim.t1_locked 拒绝；
2. 昨日持仓可卖、可部分卖（混合当日/昨日时只允许可卖部分）；
3. 锁定次日自动过期（无需后台任务）；
4. 无有效价格（停牌/行情缺失）fail-closed → 400 sim.sell_blocked；
5. opened_at 缺失的历史行 fail-closed；
6. T+0 市场（CRYPTO）不受 T+1 限制；
7. auto-build 首次建仓登记锁定；clear=True 含当日锁定 → 409 sim.t1_locked_clear 且不清仓；
8. auto-build clear 在 T+1 满足后允许，并重建持仓同样登记锁定；
9. 既有行为不回归：无 clear 且已有持仓 → 409 sim.positions_exist；卖超持仓 → 400；
10. 迁移草案 0049 结构校验（revision 链、additive、自包含，不执行）。

所有 DB 操作用内存 sqlite + monkeypatch 路由器模块 get_session（conftest _guard_real_pg 语义）。
"""
from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import zhice_os_v2.api.routers.sim_factor as sim_mod
from zhice_os_v2.api.dependencies import get_current_user
from zhice_os_v2.api.main import app
from zhice_os_v2.domain.security.user import Role, User

client = TestClient(app)

# 固定「交易日」：2026-11-02（周一）10:00 Asia/Shanghai = 02:00 UTC
NOW = datetime(2026, 11, 2, 2, 0, tzinfo=UTC)
YESTERDAY = NOW - timedelta(days=1)
STRATEGY = "sim-t1-strat"
CODE = "600519.SH"
CODE2 = "000001.SZ"

_DDL = [
    """
    CREATE TABLE v2_sim_account (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      strategy_id VARCHAR(64) NOT NULL UNIQUE,
      initial_capital NUMERIC(18,2) NOT NULL DEFAULT 1000000,
      cash NUMERIC(18,2) NOT NULL DEFAULT 1000000,
      base_position_pct NUMERIC(5,1) NOT NULL DEFAULT 40,
      max_position_pct NUMERIC(5,1) NOT NULL DEFAULT 100,
      status VARCHAR(16) NOT NULL DEFAULT 'active',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE v2_sim_position (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      strategy_id VARCHAR(64) NOT NULL,
      code VARCHAR(32) NOT NULL,
      name VARCHAR(128) NOT NULL DEFAULT '',
      qty INTEGER NOT NULL DEFAULT 0,
      avg_cost NUMERIC(12,3) NOT NULL DEFAULT 0,
      last_price NUMERIC(12,3) NOT NULL DEFAULT 0,
      updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
      market VARCHAR(16) NOT NULL DEFAULT 'CN',
      opened_at TIMESTAMP NULL,
      t1_locked_qty INTEGER NOT NULL DEFAULT 0,
      UNIQUE (strategy_id, code)
    )
    """,
    """
    CREATE TABLE v2_sim_order (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      strategy_id VARCHAR(64) NOT NULL,
      trade_date DATE NOT NULL,
      code VARCHAR(32) NOT NULL,
      name VARCHAR(128) NOT NULL DEFAULT '',
      side VARCHAR(8) NOT NULL,
      price NUMERIC(12,3) NOT NULL,
      qty INTEGER NOT NULL,
      amount NUMERIC(18,2) NOT NULL,
      reason TEXT NOT NULL DEFAULT '',
      created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE v2_strategy_daily (
      strategy_id VARCHAR(64) NOT NULL,
      data_date DATE NOT NULL,
      market VARCHAR(16) NOT NULL,
      code VARCHAR(32) NOT NULL,
      name VARCHAR(128) NOT NULL DEFAULT '',
      signal VARCHAR(128) NOT NULL DEFAULT '',
      score NUMERIC
    )
    """,
    """
    CREATE TABLE v2_bar_daily (
      symbol VARCHAR(32) NOT NULL,
      trade_date DATE NOT NULL,
      close NUMERIC(12,3) NOT NULL
    )
    """,
]


@pytest.fixture
def sim_env(monkeypatch):
    """隔离 sqlite 环境：建表 + patch 路由器 get_session/_utc_now + 鉴权 override。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.begin() as conn:
        for ddl in _DDL:
            conn.execute(text(ddl))
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(sim_mod, "get_session", lambda: Session())
    monkeypatch.setattr(sim_mod, "_utc_now", lambda: NOW)
    app.dependency_overrides[get_current_user] = lambda: User("u-sim-t1", "simt1", Role.ADMIN)
    try:
        yield SimpleNamespace(engine=engine, Session=Session)
    finally:
        app.dependency_overrides.pop(get_current_user, None)


def _seed_account(session, sid: str = STRATEGY, cash: float = 1_000_000) -> None:
    session.execute(text(
        "INSERT INTO v2_sim_account (strategy_id, initial_capital, cash, status) "
        "VALUES (:s, :c, :c, 'active')"), {"s": sid, "c": cash})
    session.commit()


def _seed_position(
    session,
    *,
    sid: str = STRATEGY,
    code: str = CODE,
    qty: int,
    opened_at: datetime | None,
    locked: int = 0,
    market: str = "CN",
    avg_cost: float = 10.0,
    last_price: float = 10.0,
) -> None:
    session.execute(text(
        "INSERT INTO v2_sim_position (strategy_id, code, name, qty, avg_cost, last_price, "
        "market, opened_at, t1_locked_qty) "
        "VALUES (:s, :c, :n, :q, :ac, :px, :m, :op, :lk)"),
        {"s": sid, "c": code, "n": code, "q": qty, "ac": avg_cost, "px": last_price,
         "m": market, "op": opened_at, "lk": locked})
    session.commit()


def _seed_pick(session, *, sid: str = STRATEGY, code: str = CODE2, close: float = 10.0) -> None:
    session.execute(text(
        "INSERT INTO v2_strategy_daily (strategy_id, data_date, market, code, name, signal, score) "
        "VALUES (:s, '2026-11-02', 'CN', :c, :c, '买点', 100)"), {"s": sid, "c": code})
    session.execute(text(
        "INSERT INTO v2_bar_daily (symbol, trade_date, close) VALUES (:c, '2026-11-02', :px)"),
        {"c": code, "px": close})
    session.commit()


def _pos(env, sid: str = STRATEGY, code: str = CODE):
    with env.Session() as s:
        return s.execute(text(
            "SELECT qty, market, opened_at, t1_locked_qty FROM v2_sim_position "
            "WHERE strategy_id=:s AND code=:c"), {"s": sid, "c": code}).mappings().first()


def _order_count(env, side: str) -> int:
    with env.Session() as s:
        return s.execute(text(
            "SELECT count(*) FROM v2_sim_order WHERE side=:side"), {"side": side}).scalar()


def _post(path: str, body: dict):
    return client.post("/api/v1/sim-factor" + path, json=body)


# --------------------------------------------------------------------------- #
# 1. 手工买入登记锁定；当日卖出被拒
# --------------------------------------------------------------------------- #
def test_buy_today_then_sell_denied(sim_env):
    _seed_account(sim_env.Session())
    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "buy", "price": 10.0, "qty": 100})
    assert r.status_code == 200, r.text

    pos = _pos(sim_env)
    assert int(pos["qty"]) == 100
    assert int(pos["t1_locked_qty"]) == 100, "当日买入必须登记 T+1 锁定数量"
    assert pos["opened_at"] is not None
    assert pos["market"] == "CN"

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 100})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "sim.t1_locked"
    assert int(_pos(sim_env)["qty"]) == 100, "被拒后持仓不得变化"
    assert _order_count(sim_env, "sell") == 0, "被拒后不得写卖出流水"


def test_buy_same_day_accumulates_lock(sim_env):
    _seed_account(sim_env.Session())
    for _ in range(2):
        r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                             "code": CODE, "name": CODE, "side": "buy", "price": 10.0, "qty": 100})
        assert r.status_code == 200, r.text
    pos = _pos(sim_env)
    assert int(pos["qty"]) == 200
    assert int(pos["t1_locked_qty"]) == 200

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 200})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "sim.t1_locked"


# --------------------------------------------------------------------------- #
# 2. 昨日/历史持仓可卖；混合持仓只允许可卖部分
# --------------------------------------------------------------------------- #
def test_sell_yesterday_position_allowed(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=YESTERDAY, locked=0)

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 100})
    assert r.status_code == 200, r.text
    assert _pos(sim_env) is None, "全部卖出后持仓应删除"
    assert _order_count(sim_env, "sell") == 1


def test_sell_mixed_only_available_part(sim_env):
    _seed_account(sim_env.Session())
    # 昨日 100 股 + 当日 100 股（锁定 100）
    _seed_position(sim_env.Session(), qty=200, opened_at=NOW, locked=100)

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 150})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "sim.t1_locked"
    assert int(_pos(sim_env)["qty"]) == 200

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 100})
    assert r.status_code == 200, r.text
    pos = _pos(sim_env)
    assert int(pos["qty"]) == 100
    assert int(pos["t1_locked_qty"]) == 100, "当日锁定数量不得被卖"


def test_lock_expires_next_day(sim_env, monkeypatch):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=NOW, locked=100)

    # 次日：锁定数量按 opened_at 自动过期，无需后台任务。
    monkeypatch.setattr(sim_mod, "_utc_now", lambda: NOW + timedelta(days=1))
    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-03",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 100})
    assert r.status_code == 200, r.text
    assert _pos(sim_env) is None


# --------------------------------------------------------------------------- #
# 3. 停牌/无价格 / opened_at 缺失 fail-closed
# --------------------------------------------------------------------------- #
def test_sell_invalid_price_blocked(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=YESTERDAY, locked=0)

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 0, "qty": 100})
    assert r.status_code == 400, r.text
    assert r.json()["detail"]["code"] == "sim.sell_blocked"
    assert int(_pos(sim_env)["qty"]) == 100


def test_sell_opened_at_missing_fail_closed(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=None, locked=0)

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 100})
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["code"] == "sim.t1_locked"


def test_sell_insufficient_position_unchanged(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=50, opened_at=YESTERDAY, locked=0)

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": CODE, "name": CODE, "side": "sell", "price": 10.5, "qty": 100})
    assert r.status_code == 400
    assert r.json()["detail"]["code"] == "sim.insufficient_position"


# --------------------------------------------------------------------------- #
# 4. T+0 市场不受 T+1 限制
# --------------------------------------------------------------------------- #
def test_sell_crypto_same_day_allowed(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), code="BTC/USDT", qty=2, opened_at=NOW, locked=2,
                   market="CRYPTO", avg_cost=50000, last_price=50000)

    r = _post("/order", {"strategy_id": STRATEGY, "trade_date": "2026-11-02",
                         "code": "BTC/USDT", "name": "BTC", "side": "sell",
                         "price": 51000, "qty": 2})
    assert r.status_code == 200, r.text
    assert _pos(sim_env, code="BTC/USDT") is None


# --------------------------------------------------------------------------- #
# 5. auto-build：首次建仓锁定 / clear 守卫
# --------------------------------------------------------------------------- #
def test_auto_build_first_build_sets_lock(sim_env):
    _seed_account(sim_env.Session())
    _seed_pick(sim_env.Session())

    r = _post("/auto-build", {"strategy_id": STRATEGY, "position_pct": 40, "top_n": 10,
                              "clear": False})
    assert r.status_code == 200, r.text
    assert r.json()["built"] == 1
    pos = _pos(sim_env, code=CODE2)
    assert pos is not None
    qty = int(pos["qty"])
    assert qty > 0
    assert int(pos["t1_locked_qty"]) == qty, "auto-build 当日建仓必须登记 T+1 锁定"
    assert pos["opened_at"] is not None
    assert pos["market"] == "CN"


def test_auto_build_clear_blocked_by_t1(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=NOW, locked=100)
    _seed_pick(sim_env.Session())

    r = _post("/auto-build", {"strategy_id": STRATEGY, "position_pct": 40, "top_n": 10,
                              "clear": True})
    assert r.status_code == 409, r.text
    detail = r.json()["detail"]
    assert detail["code"] == "sim.t1_locked_clear"
    assert detail["blocked"]
    assert detail["blocked"][0]["code"] == CODE
    assert int(detail["blocked"][0]["locked_qty"]) == 100
    assert _pos(sim_env) is not None, "被拒后不得清仓"


def test_auto_build_clear_allowed_after_t1(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=YESTERDAY, locked=0)
    _seed_pick(sim_env.Session())

    r = _post("/auto-build", {"strategy_id": STRATEGY, "position_pct": 40, "top_n": 10,
                              "clear": True})
    assert r.status_code == 200, r.text
    assert _pos(sim_env, code=CODE) is None, "旧持仓应被 clear"
    new_pos = _pos(sim_env, code=CODE2)
    assert new_pos is not None
    assert int(new_pos["t1_locked_qty"]) == int(new_pos["qty"]), "重建仓同样登记当日锁定"


def test_auto_build_positions_exist_unchanged(sim_env):
    _seed_account(sim_env.Session())
    _seed_position(sim_env.Session(), qty=100, opened_at=YESTERDAY, locked=0)

    r = _post("/auto-build", {"strategy_id": STRATEGY, "position_pct": 40, "top_n": 10,
                              "clear": False})
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "sim.positions_exist"
    assert int(_pos(sim_env)["qty"]) == 100


# --------------------------------------------------------------------------- #
# 6. 迁移草案 0049 结构校验（不执行）
# --------------------------------------------------------------------------- #
def test_migration_0049_is_additive_draft():
    path = (Path(__file__).resolve().parents[1]
            / "migrations" / "versions" / "0049_sim_t1.py")
    assert path.exists(), "缺少迁移草案 migrations/versions/0049_sim_t1.py"
    spec = importlib.util.spec_from_file_location("mig_0049_sim_t1", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    assert mod.revision == "0049"
    assert mod.down_revision == "0048"
    assert callable(mod.upgrade) and callable(mod.downgrade)

    src = path.read_text(encoding="utf-8")
    for token in ("market", "opened_at", "t1_locked_qty", "v2_sim_position"):
        assert token in src
    assert "add_column" in src
    assert "UPDATE v2_sim_position SET opened_at" in src
    # additive：升级路径不得删表/删列
    upgrade_src = src.split("def upgrade()", 1)[1].split("def downgrade()", 1)[0]
    assert "drop_column" not in upgrade_src
    assert "drop_table" not in upgrade_src
