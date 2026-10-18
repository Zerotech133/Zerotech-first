"""含斜杠 ts_code / replay_id 的 query 参数形态端点测试（B3 修复）。

背景：CRYPTO ts_code 形如 BTC/USDT，replay_id 生成时把 ts_code 拼进 id 也含斜杠；
path 参数模板 `[^/]+` 匹配不了斜杠（%2F 路由同样不认）→ /market/kline/{code}、
/market/snapshot/{code}、/replay/{id}、/replay/{id}/attribution、
/knowledge/replay/explain/{id} 对这些标的系统性 404。

修复形态：各受影响端点新增 query 主形态（?symbol= / ?replay_id=），原 path
形态保留兼容存量无斜杠调用。
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

SLASH_SYMBOL = "BTC/USDT"
PLAIN_SYMBOL = "600519.SH"
SLASH_REPLAY_ID = "replay-sv-UNI/USDT-2026-09-07-d3ac"
PLAIN_REPLAY_ID = "replay-plain-1"


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def no_db_bars(monkeypatch):
    """屏蔽 kline 端点的 DB 取数（fetch_db_*），强制走内存 seed 数据，测试不碰真 PG。"""
    import zhice_os_v2.api.routers.market as market_mod

    monkeypatch.setattr(market_mod, "fetch_db_bars", lambda *a, **k: None)
    monkeypatch.setattr(market_mod, "fetch_db_minute_bars", lambda *a, **k: None)


@pytest.fixture()
def snapshot_db(monkeypatch):
    """snapshot 端点走 ORM：注入隔离 sqlite（含斜杠与无斜杠标的各一根日K）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.api.routers.market as market_mod
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        for market, symbol, close in (
            ("CRYPTO", SLASH_SYMBOL, Decimal("60000")),
            ("CN", PLAIN_SYMBOL, Decimal("1500")),
        ):
            s.add(
                orm.BarDailyModel(
                    instrument_id=symbol,
                    market=market,
                    symbol=symbol,
                    trade_date=dt.date(2026, 9, 4),
                    close=close,
                    pre_close=close - Decimal("100"),
                    created_at=dt.datetime(2026, 9, 4, tzinfo=dt.UTC),
                )
            )
        s.commit()
    monkeypatch.setattr(market_mod, "get_session", lambda: Session())
    return Session


@pytest.fixture()
def attribution_db(monkeypatch):
    """attribution 端点直查 ORM：注入隔离 sqlite（含斜杠与无斜杠 replay 各一条归因）。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.db.session as sess_mod
    from zhice_os_v2.infrastructure.db import models as orm
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    with Session() as s:
        for i, rid in enumerate((SLASH_REPLAY_ID, PLAIN_REPLAY_ID)):
            s.add(
                orm.AttributionRecordModel(
                    # sqlite 下 BIGINT 主键无自增，显式给 id
                    id=i + 1,
                    attribution_id=f"attr-{i}",
                    replay_id=rid,
                    market="CRYPTO",
                    dimension="TIMING",
                    weight=Decimal("0.6"),
                    pnl_amount=Decimal("120.5"),
                    note="n",
                    created_at=dt.datetime(2026, 9, 7, tzinfo=dt.UTC),
                )
            )
        s.commit()
    # 覆盖 conftest._guard_real_pg 的阻断（replay.get_attribution 函数内 import 取此属性）
    monkeypatch.setattr(sess_mod, "get_session", lambda: Session())
    return Session


@pytest.fixture()
def replays_db():
    """内存 PersistencePort 预置含斜杠/无斜杠 replay 各一条，override 进 get_db。"""
    from decimal import Decimal as D

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.domain.replay.replay import ReplayCase, ReviewStatus
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    db = InMemoryDatabase()
    db.save_replay(
        ReplayCase(
            replay_id=SLASH_REPLAY_ID,
            strategy_version_id="sv-1",
            market="CRYPTO",
            ts_code=SLASH_SYMBOL,
            pnl=D("12.5"),
            pnl_pct=D("0.02"),
            holding_period_seconds=3600,
            exit_reason="TAKE_PROFIT",
            status=ReviewStatus.PENDING,
        )
    )
    db.save_replay(
        ReplayCase(
            replay_id=PLAIN_REPLAY_ID,
            strategy_version_id="sv-1",
            market="CN",
            ts_code=PLAIN_SYMBOL,
            pnl=D("-3.5"),
            pnl_pct=D("-0.01"),
            holding_period_seconds=1800,
            exit_reason="STOP_LOSS",
            status=ReviewStatus.PENDING,
        )
    )
    app.dependency_overrides[get_db] = lambda: db
    yield db
    app.dependency_overrides.pop(get_db, None)


# ---------- /market/kline ----------

def test_kline_slash_symbol_via_query(client, no_db_bars):
    resp = client.get(
        "/api/v1/market/kline",
        params={"symbol": SLASH_SYMBOL, "period": "day", "limit": 50},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ts_code"] == SLASH_SYMBOL
    assert len(body["bars"]) > 0


def test_kline_plain_symbol_via_path_compat(client, no_db_bars):
    resp = client.get(
        f"/api/v1/market/kline/{PLAIN_SYMBOL}",
        params={"period": "day", "limit": 50},
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()["bars"]) > 0


# ---------- /market/snapshot ----------

def test_snapshot_slash_symbol_via_query(client, snapshot_db):
    resp = client.get("/api/v1/market/snapshot", params={"symbol": SLASH_SYMBOL})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ts_code"] == SLASH_SYMBOL
    assert float(body["price"]) == 60000.0
    assert body["source"] == "daily"


def test_snapshot_plain_symbol_via_path_compat(client, snapshot_db):
    resp = client.get(f"/api/v1/market/snapshot/{PLAIN_SYMBOL}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["ts_code"] == PLAIN_SYMBOL


# ---------- /replay detail ----------

def test_replay_detail_slash_id_via_query(client, auth_headers, replays_db):
    resp = client.get(
        "/api/v1/replay/detail",
        params={"replay_id": SLASH_REPLAY_ID},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replay_id"] == SLASH_REPLAY_ID
    assert body["ts_code"] == SLASH_SYMBOL


def test_replay_detail_plain_id_via_path_compat(client, auth_headers, replays_db):
    resp = client.get(
        f"/api/v1/replay/{PLAIN_REPLAY_ID}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["replay_id"] == PLAIN_REPLAY_ID


def test_replay_detail_slash_id_via_query_404_when_absent(client, auth_headers):
    resp = client.get(
        "/api/v1/replay/detail",
        params={"replay_id": "replay-nope/USDT-x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404


# ---------- /replay attribution ----------

def test_attribution_slash_id_via_query(client, auth_headers, attribution_db):
    resp = client.get(
        "/api/v1/replay/attribution",
        params={"replay_id": SLASH_REPLAY_ID},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    rows = resp.json()
    assert len(rows) == 1
    assert rows[0]["dimension"] == "TIMING"
    assert float(rows[0]["pnl_amount"]) == 120.5


def test_attribution_plain_id_via_path_compat(client, auth_headers, attribution_db):
    resp = client.get(
        f"/api/v1/replay/{PLAIN_REPLAY_ID}/attribution",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == 1


# ---------- /knowledge/replay/explain ----------

def test_explain_slash_id_via_query(client, auth_headers, replays_db):
    resp = client.get(
        "/api/v1/knowledge/replay/explain",
        params={"replay_id": SLASH_REPLAY_ID},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["replay_id"] == SLASH_REPLAY_ID
    assert body["ts_code"] == SLASH_SYMBOL
    assert "sv-1" in body["explanation"]


def test_explain_plain_id_via_path_compat(client, auth_headers, replays_db):
    resp = client.get(
        f"/api/v1/knowledge/replay/explain/{PLAIN_REPLAY_ID}",
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["replay_id"] == PLAIN_REPLAY_ID


def test_explain_slash_id_via_query_404_when_absent(client, auth_headers):
    resp = client.get(
        "/api/v1/knowledge/replay/explain",
        params={"replay_id": "replay-nope/USDT-x"},
        headers=auth_headers,
    )
    assert resp.status_code == 404
