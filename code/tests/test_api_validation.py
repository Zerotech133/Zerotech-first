"""deep-test B4：参数校验与 500 批修 + 行情接口卫生。

覆盖清单（每项对应实测复现的 500/静默回退/mock 假行情）：
A1 pnl-calendar market/month 校验 422（不再 500 / 静默回退）
A2 /strategies/{sv}/detail 未知市场前缀 404（不再 500）
A3 /my/accounts 非法 market/account_type/负 capital 422（不再 500）
A4 /my/strategies spec 非法 422（对齐 /versions 的捕获范式）
A5 /my/strategies strategy_name 129+/空白 422（不再溢出 String(128) 500）
A6 /my/strategies market 枚举 + body/spec.market 一致性 422
B7 /market/quotes 非法 market 422、合法无数据 []（删 mock 回退）
B8 /market/quotes 陈旧日线（>7 自然日）pct_change 置 null
B9 /market/overview losers 排序 0.0 不再被当 999 垫底

需 DB 的用例均 monkeypatch 模块级 get_session 到隔离 sqlite（StaticPool），
绝不连真 PG（conftest _guard_real_pg 语义）。
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.base import Base

client = TestClient(app)


def _h(role: str = "ADMIN") -> dict:
    return {"Authorization": f"Bearer {create_token('u-b4', 'b4tester', role)}"}


@pytest.fixture
def sqlite_session(monkeypatch):
    """隔离 sqlite 会话工厂：patch market/strategy_detail/positions 模块级 get_session。"""
    import zhice_os_v2.api.routers.market as market_mod
    import zhice_os_v2.api.routers.positions as pos_mod
    import zhice_os_v2.api.routers.strategy_detail as detail_mod

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    mk = lambda: Session()  # noqa: E731
    monkeypatch.setattr(market_mod, "get_session", mk)
    monkeypatch.setattr(pos_mod, "get_session", mk)
    monkeypatch.setattr(detail_mod, "get_session", mk)
    return mk


# ─── A1：pnl-calendar 入参校验 ────────────────────────────────────────────────


def test_pnl_calendar_invalid_market_422():
    r = client.get("/api/v1/reports/pnl-calendar", params={"market": "XX"}, headers=_h())
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_pnl_calendar_month_13_422():
    r = client.get(
        "/api/v1/reports/pnl-calendar",
        params={"market": "CN", "year": 2026, "month": 13},
        headers=_h(),
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "report.invalid_month"}


def test_pnl_calendar_month_0_422():
    r = client.get(
        "/api/v1/reports/pnl-calendar",
        params={"market": "CN", "year": 2026, "month": 0},
        headers=_h(),
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "report.invalid_month"}


# ─── A2：detail 未知市场前缀 404 ─────────────────────────────────────────────


def test_strategy_detail_unknown_market_prefix_404(sqlite_session):
    r = client.get("/api/v1/strategies/sv-unknown/detail", headers=_h())
    assert r.status_code == 404
    assert r.json()["detail"] == {"code": "strategy.not_found"}


# ─── A3：/my/accounts 入参校验 ───────────────────────────────────────────────


def _account_body(**kw):
    base = {"market": "CN", "account_type": "SHARED_PORTFOLIO", "initial_capital": 10000.0}
    base.update(kw)
    return base


def test_my_accounts_invalid_market_422():
    r = client.post(
        "/api/v1/my/accounts", headers=_h("USER"), json=_account_body(market="XX")
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_my_accounts_invalid_account_type_422():
    r = client.post(
        "/api/v1/my/accounts", headers=_h("USER"), json=_account_body(account_type="FOO")
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "account.invalid_account_type"}


def test_my_accounts_negative_capital_422():
    r = client.post(
        "/api/v1/my/accounts", headers=_h("USER"), json=_account_body(initial_capital=-1)
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "account.invalid_capital"}


# ─── A4/A5/A6：/my/strategies 入参校验 ───────────────────────────────────────


def _spec(market: str = "CN", *, with_universe: bool = True) -> dict:
    spec = {"spec_version": 1, "market": market}
    if with_universe:
        spec["universe"] = {"codes": ["600519.SH"]}
    return spec


def test_my_strategies_invalid_spec_422():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("USER"),
        json={"strategy_name": "ok", "market": "CN", "spec": _spec(with_universe=False)},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "strategy.spec_invalid"}


def test_my_strategies_name_too_long_422():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("USER"),
        json={"strategy_name": "x" * 129, "market": "CN"},
    )
    assert r.status_code == 422


def test_my_strategies_name_blank_422():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("USER"),
        json={"strategy_name": "   ", "market": "CN"},
    )
    assert r.status_code == 422


def test_my_strategies_invalid_market_422():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("USER"),
        json={"strategy_name": "ok", "market": "XX"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_my_strategies_lowercase_market_422():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("USER"),
        json={"strategy_name": "ok", "market": "cn"},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_my_strategies_market_mismatch_422():
    r = client.post(
        "/api/v1/my/strategies",
        headers=_h("USER"),
        json={"strategy_name": "ok", "market": "US", "spec": _spec("CN")},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "strategy.market_mismatch"}


# ─── B7：/market/quotes 卫生 ─────────────────────────────────────────────────


def test_quotes_invalid_market_422():
    r = client.get("/api/v1/market/quotes", params={"market": "XX"})
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "market.invalid"}


def test_quotes_valid_market_no_data_returns_empty_list(sqlite_session):
    r = client.get("/api/v1/market/quotes", params={"market": "CN"})
    assert r.status_code == 200
    assert r.json() == []  # 不再回退 MemoryMarketData 吐 mock 假行情


# ─── B8：quotes 陈旧日线 pct_change 置 null ─────────────────────────────────


def _seed_instrument_with_bars(session_factory, symbol: str, market: str, d1: date, d2: date):
    with session_factory() as s:
        s.add(
            orm.InstrumentModel(
                instrument_id=f"inst-{symbol}",
                market=market,
                symbol=symbol,
                exchange="X",
                currency="USD",
                name="test",
                created_at=datetime.now(UTC),
            )
        )
        for d, close in ((d1, Decimal("1.00")), (d2, Decimal("1.30"))):
            s.add(
                orm.BarDailyModel(
                    instrument_id=f"inst-{symbol}",
                    market=market,
                    symbol=symbol,
                    trade_date=d,
                    close=close,
                    pre_close=None,
                    created_at=datetime.now(UTC),
                )
            )
        s.commit()


def test_quotes_stale_daily_pct_change_null(sqlite_session):
    """HK 00009 场景：最新日线停在 2024（距今 >7 自然日）→ pct_change 必须为 null。"""
    from zhice_os_v2.api.routers.market import _db_quotes

    _seed_instrument_with_bars(
        sqlite_session, "00009.HK", "HK", date(2024, 6, 3), date(2024, 6, 4)
    )
    quotes = _db_quotes(limit=10, market="HK")
    assert len(quotes) == 1
    assert quotes[0]["pct_change"] is None  # 旧口径会算出 +30% 假涨跌
    assert quotes[0]["price"] == 1.3  # price（最新日线 close）保留兜底展示


def test_quotes_fresh_daily_pct_change_kept(sqlite_session):
    """新鲜日线（上一交易日）→ pct_change 正常计算，不误伤。"""
    from zhice_os_v2.api.routers.market import _db_quotes

    today = date.today()
    _seed_instrument_with_bars(
        sqlite_session, "600519.SH", "CN", today - timedelta(days=2), today - timedelta(days=1)
    )
    quotes = _db_quotes(limit=10, market="CN")
    assert len(quotes) == 1
    assert quotes[0]["pct_change"] == 30.0  # (1.30-1.00)/1.00


# ─── B9：overview losers 排序 falsy 陷阱 ────────────────────────────────────


def test_overview_losers_zero_pct_sorts_as_zero(monkeypatch):
    """pct_change=0.0 须按 0.0 参与升序（排正涨幅前），不再被 `or 999` 垫底。"""
    import zhice_os_v2.api.routers.market as market_mod

    rows = [
        {"ts_code": "A", "pct_change": 0.0},
        {"ts_code": "B", "pct_change": 2.5},
        {"ts_code": "C", "pct_change": -1.0},
    ]
    monkeypatch.setattr(
        market_mod, "_db_overview", lambda market=None, limit=500: (rows, len(rows))
    )
    r = client.get("/api/v1/market/overview", params={"market": "CN"})
    assert r.status_code == 200
    assert [x["ts_code"] for x in r.json()["losers"]] == ["C", "A", "B"]


def test_overview_losers_none_still_sinks_last(monkeypatch):
    """pct_change=None（无昨收）保持垫底语义不变。"""
    import zhice_os_v2.api.routers.market as market_mod

    rows = [
        {"ts_code": "A", "pct_change": None},
        {"ts_code": "B", "pct_change": 1.0},
        {"ts_code": "C", "pct_change": -0.5},
    ]
    monkeypatch.setattr(
        market_mod, "_db_overview", lambda market=None, limit=500: (rows, len(rows))
    )
    r = client.get("/api/v1/market/overview", params={"market": "CN"})
    assert [x["ts_code"] for x in r.json()["losers"]] == ["C", "B", "A"]
