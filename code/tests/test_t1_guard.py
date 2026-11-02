"""P0-1 统一 A 股卖出守卫测试（t1_guard + /positions/close 接入）。

覆盖：
- CN 当日买入不可卖（T+1 锁）；
- T+1 可卖（昨仓/更早批次）与可卖量聚合；
- opened_at 缺失 fail-closed；
- 停牌（无价）/跌停封死 fail-closed；
- 非 CN 市场（US）当日可卖（T+0）；
- /positions/close：整单拒绝、禁止任意 close_price、按最新行情价成交、账户范围过滤。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.api.main import app
from zhice_os_v2.domain.execution.t1_guard import (
    available_qty,
    check_sell,
    t_plus_one_locked,
)
from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

_CN_TZ = ZoneInfo("Asia/Shanghai")
# 固定"现在"：2026-11-02（周一）10:00 上海，避免跨日/跨时区抖动。
_NOW = datetime(2026, 11, 2, 10, 0, tzinfo=_CN_TZ)
_TODAY_OPEN = datetime(2026, 11, 2, 9, 35, tzinfo=_CN_TZ)
_YESTERDAY_OPEN = datetime(2026, 11, 1, 14, 30, tzinfo=_CN_TZ)

_ADMIN = User(user_id="u-admin", username="admin", role=Role.ADMIN)

CN_CODE = "600519.SH"


def _lot(
    opened_at: datetime | None,
    qty: str = "100",
    market: str = "CN",
    ts_code: str = CN_CODE,
) -> SimpleNamespace:
    return SimpleNamespace(
        market=market,
        ts_code=ts_code,
        open_qty=Decimal(qty),
        opened_at=opened_at,
    )


# --------------------------------------------------------------------------- #
# 纯函数：T+1 / 可卖量 / 停牌 / 跌停                                            #
# --------------------------------------------------------------------------- #


def test_cn_same_day_buy_locked() -> None:
    """CN 当日买入：T+1 锁，可卖量 0，并给出明确原因。"""
    decision = check_sell(
        "CN", CN_CODE, qty=100, lots=[_lot(_TODAY_OPEN)],
        price=10.0, pre_close=10.0, now=_NOW,
    )
    assert not decision.ok
    assert decision.locked is True
    assert "T+1" in decision.reason and "当日" in decision.reason
    assert decision.available_qty == Decimal("0")
    assert t_plus_one_locked(_TODAY_OPEN, "CN", now=_NOW) is True


def test_cn_yesterday_lot_sellable() -> None:
    """T+1 可卖：昨仓可用=开仓量，价格有效即通过。"""
    decision = check_sell(
        "CN", CN_CODE, qty=100, lots=[_lot(_YESTERDAY_OPEN)],
        price=10.0, pre_close=10.0, now=_NOW,
    )
    assert decision.ok, decision.reason
    assert decision.available_qty == Decimal("100")
    assert decision.locked is False
    assert t_plus_one_locked(_YESTERDAY_OPEN, "CN", now=_NOW) is False


def test_cn_missing_opened_at_fail_closed() -> None:
    """opened_at 缺失：无法证明非当日，CN fail-closed。"""
    decision = check_sell(
        "CN", CN_CODE, qty=100, lots=[_lot(None)],
        price=10.0, pre_close=10.0, now=_NOW,
    )
    assert not decision.ok
    assert "opened_at" in decision.reason
    assert decision.available_qty == Decimal("0")
    assert t_plus_one_locked(None, "CN", now=_NOW) is True
    assert available_qty([_lot(None)], "CN", now=_NOW) == Decimal("0")


def test_available_qty_excludes_same_day_lot() -> None:
    """可卖量 = 非当日批次之和；请求超过可卖量必须拒绝。"""
    lots = [_lot(_TODAY_OPEN, "100"), _lot(_YESTERDAY_OPEN, "200")]
    assert available_qty(lots, "CN", now=_NOW) == Decimal("200")

    blocked = check_sell(
        "CN", CN_CODE, qty=300, lots=lots, price=10.0, pre_close=10.0, now=_NOW,
    )
    assert not blocked.ok
    assert "可卖量不足" in blocked.reason
    assert blocked.available_qty == Decimal("200")

    ok = check_sell(
        "CN", CN_CODE, qty=200, lots=lots, price=10.0, pre_close=10.0, now=_NOW,
    )
    assert ok.ok, ok.reason


def test_limit_down_fail_closed() -> None:
    """跌停封死（现价≤跌停价）不可卖。"""
    decision = check_sell(
        "CN", CN_CODE, qty=100, lots=[_lot(_YESTERDAY_OPEN)],
        price=9.0, pre_close=10.0, now=_NOW,  # CN 主板跌停价=9.00
    )
    assert not decision.ok
    assert "跌停" in decision.reason


def test_suspension_or_missing_price_fail_closed() -> None:
    """无有效卖价（None/≤0）：停牌/数据失联 fail-closed。"""
    for bad_price in (None, 0, -1.0):
        decision = check_sell(
            "CN", CN_CODE, qty=100, lots=[_lot(_YESTERDAY_OPEN)],
            price=bad_price, pre_close=10.0, now=_NOW,
        )
        assert not decision.ok, f"price={bad_price} 应拒绝"
        assert "无有效卖价" in decision.reason


def test_non_cn_market_same_day_allowed() -> None:
    """US 等 T+0 市场：当日买入可卖（same_day_sell_allowed=True）。"""
    decision = check_sell(
        "US", "AAPL", qty=10, lots=[_lot(_TODAY_OPEN, "10", market="US", ts_code="AAPL")],
        price=100.0, pre_close=100.0, now=_NOW,
    )
    assert decision.ok, decision.reason
    assert decision.available_qty == Decimal("10")
    assert t_plus_one_locked(_TODAY_OPEN, "US", now=_NOW) is False
    assert t_plus_one_locked(None, "US", now=_NOW) is False


# --------------------------------------------------------------------------- #
# /positions/close 接入（内存 DB + 行情打桩）                                    #
# --------------------------------------------------------------------------- #


def _position(
    position_id: str,
    *,
    opened_at: datetime | None,
    account_id: str | None = None,
) -> PositionLot:
    return PositionLot(
        position_id=position_id,
        strategy_version_id="sv-t1",
        market="CN",
        ts_code=CN_CODE,
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("10.0"),
        opened_at=opened_at,
        account_id=account_id,
    )


@pytest.fixture
def close_env(monkeypatch: pytest.MonkeyPatch):
    """内存 DB + 行情打桩 + ADMIN 覆盖；返回 configure() 与记录容器。"""
    import zhice_os_v2.api.routers.positions as pos_mod
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    db = InMemoryDatabase()
    fill_calls: list[dict] = []
    state = SimpleNamespace(db=db, fill_calls=fill_calls)

    def configure(prices: dict[str, float], pre_closes: dict[str, float]) -> TestClient:
        monkeypatch.setattr(
            pos_mod, "_get_latest_prices", lambda symbols, market: dict(prices)
        )
        monkeypatch.setattr(
            pos_mod, "_get_pre_closes", lambda symbols, market: dict(pre_closes)
        )
        monkeypatch.setattr(
            WorkflowService, "_record_fill",
            lambda self, **kw: fill_calls.append(kw),
        )
        app.dependency_overrides[get_db] = lambda: db
        app.dependency_overrides[get_current_user] = lambda: _ADMIN
        return TestClient(app)

    state.configure = configure
    try:
        yield state
    finally:
        app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


def test_close_blocks_same_day_cn_position(close_env) -> None:
    """当日 CN 仓位：409 整单拒绝、仓位不变、不写卖出流水。"""
    pos = _position("pos-t1-today", opened_at=utc_now())
    close_env.db.save_position(pos)
    client = close_env.configure({CN_CODE: 10.0}, {CN_CODE: 10.0})

    resp = client.post("/api/v1/positions/close", json={"market": "CN"})

    assert resp.status_code == 409, resp.text
    detail = resp.json()["detail"]
    assert detail["code"] == "positions.close_blocked"
    assert "T+1" in detail["blocked"][0]["reason"]
    assert close_env.db.get_position("pos-t1-today").status == "OPEN"
    assert close_env.fill_calls == []


def test_close_closes_yesterday_cn_at_market_price(close_env) -> None:
    """T+1 已满足：按最新行情价成交（不再使用调用方任意价）。"""
    pos = _position("pos-t1-yest", opened_at=utc_now() - timedelta(days=1))
    close_env.db.save_position(pos)
    client = close_env.configure({CN_CODE: 10.0}, {CN_CODE: 10.0})

    resp = client.post("/api/v1/positions/close", json={"market": "CN"})

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["count"] == 1 and body["price_source"] == "latest_market"
    closed = close_env.db.get_position("pos-t1-yest")
    assert closed.status == "CLOSED"
    assert closed.close_price == Decimal("10.0")
    assert len(close_env.fill_calls) == 1
    kw = close_env.fill_calls[0]
    assert kw["side"] == "SELL"
    assert kw["price"] == Decimal("10.0")
    assert kw["qty"] == Decimal("100")
    assert "T+1守卫" in kw["sell_reason"]


def test_close_rejects_arbitrary_close_price(close_env) -> None:
    """禁止任意价：传 close_price（即使 T+1 可卖）→ 400 且不落任何仓位变更。"""
    pos = _position("pos-t1-price", opened_at=utc_now() - timedelta(days=1))
    close_env.db.save_position(pos)
    client = close_env.configure({CN_CODE: 10.0}, {CN_CODE: 10.0})

    resp = client.post(
        "/api/v1/positions/close",
        json={"market": "CN", "close_price": 12.5},
    )

    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"]["code"] == "positions.close_price_not_allowed"
    assert close_env.db.get_position("pos-t1-price").status == "OPEN"
    assert close_env.fill_calls == []


def test_close_missing_quote_fail_closed(close_env) -> None:
    """无行情（停牌/数据缺失）：fail-closed，仓位不变。"""
    pos = _position("pos-t1-noquote", opened_at=utc_now() - timedelta(days=1))
    close_env.db.save_position(pos)
    client = close_env.configure({}, {})

    resp = client.post("/api/v1/positions/close", json={"market": "CN"})

    assert resp.status_code == 409, resp.text
    assert "无有效卖价" in resp.json()["detail"]["blocked"][0]["reason"]
    assert close_env.db.get_position("pos-t1-noquote").status == "OPEN"


def test_close_missing_opened_at_fail_closed(close_env) -> None:
    """opened_at 缺失：fail-closed，仓位不变。"""
    pos = _position("pos-t1-null-open", opened_at=None)
    close_env.db.save_position(pos)
    client = close_env.configure({CN_CODE: 10.0}, {CN_CODE: 10.0})

    resp = client.post("/api/v1/positions/close", json={"market": "CN"})

    assert resp.status_code == 409, resp.text
    assert "opened_at" in resp.json()["detail"]["blocked"][0]["reason"]
    assert close_env.db.get_position("pos-t1-null-open").status == "OPEN"


def test_close_is_all_or_nothing(close_env) -> None:
    """混仓：一票可卖一票 T+1 锁 → 整单拒绝，两票都不平。"""
    sellable = _position("pos-t1-mixed-ok", opened_at=utc_now() - timedelta(days=1))
    locked = _position("pos-t1-mixed-lock", opened_at=utc_now())
    close_env.db.save_position(sellable)
    close_env.db.save_position(locked)
    client = close_env.configure({CN_CODE: 10.0}, {CN_CODE: 10.0})

    resp = client.post("/api/v1/positions/close", json={"market": "CN"})

    assert resp.status_code == 409, resp.text
    assert close_env.db.get_position("pos-t1-mixed-ok").status == "OPEN"
    assert close_env.db.get_position("pos-t1-mixed-lock").status == "OPEN"
    assert close_env.fill_calls == []


def test_close_account_scope_filters_positions(close_env) -> None:
    """account_id 过滤生效：只平指定账户（此前 body.account_id 被忽略）。"""
    acc_a = _position(
        "pos-t1-acc-a", opened_at=utc_now() - timedelta(days=1), account_id="acc-a"
    )
    acc_b = _position(
        "pos-t1-acc-b", opened_at=utc_now() - timedelta(days=1), account_id="acc-b"
    )
    close_env.db.save_position(acc_a)
    close_env.db.save_position(acc_b)
    client = close_env.configure({CN_CODE: 10.0}, {CN_CODE: 10.0})

    resp = client.post(
        "/api/v1/positions/close",
        json={"market": "CN", "account_id": "acc-a"},
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 1
    assert close_env.db.get_position("pos-t1-acc-a").status == "CLOSED"
    assert close_env.db.get_position("pos-t1-acc-b").status == "OPEN"


def test_close_empty_market_ok_even_with_legacy_price(close_env) -> None:
    """空市场：兼容旧调用（含 close_price）返回 count=0；不做无意义行情请求。"""
    client = close_env.configure({}, {})
    resp = client.post(
        "/api/v1/positions/close",
        json={"market": "CN", "close_price": 12.5},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["count"] == 0
    assert close_env.fill_calls == []
