"""deep-test B4 C 组：GET /api/v1/trades 成交明细端点。

访问控制与 /positions 同形态（登录 + deny_viewer）；USER 可读全局是 legacy
已知边界。测试经隔离 sqlite（monkeypatch trades 模块级 get_session），不连真 PG。
"""
from __future__ import annotations

from datetime import datetime
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
    return {"Authorization": f"Bearer {create_token('u-b4c', 'b4c', role)}"}


def _mk_session(monkeypatch):
    import zhice_os_v2.api.routers.trades as trades_mod

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(trades_mod, "get_session", lambda: Session())
    return Session


def _seed(Session):
    """3 笔 CN + 1 笔 US；全局 fill_time 倒序应为 fill-3 → fill-4 → fill-2 → fill-1。

    id 显式给值：v2_fill_record.id 是 BigInteger autoincrement（PG 自增），
    sqlite 不认 BigInteger 自增，须手工赋主键。
    """
    rows = [
        (1, "fill-1", "CN", "600519.SH", "2026-09-01T10:00:00"),
        (2, "fill-2", "CN", "600000.SH", "2026-09-03T14:30:00"),
        (3, "fill-3", "CN", "600519.SH", "2026-09-05T09:35:00"),
        (4, "fill-4", "US", "AAPL", "2026-09-04T21:00:00"),
    ]
    with Session() as s:
        for pk, fill_id, market, ts_code, ts in rows:
            s.add(
                orm.FillRecordModel(
                    id=pk,
                    fill_id=fill_id,
                    execution_id=f"exec-{fill_id}",
                    market=market,
                    ts_code=ts_code,
                    fill_price=Decimal("10.5"),
                    fill_qty=Decimal("100"),
                    fill_amount=Decimal("1050"),
                    fill_time=datetime.fromisoformat(ts),
                    created_at=datetime(2026, 9, 6),
                )
            )
        s.commit()


@pytest.fixture
def seeded(monkeypatch):
    Session = _mk_session(monkeypatch)
    _seed(Session)
    return Session


def test_trades_openapi_visible():
    """注册进路由树：openapi paths 含 /api/v1/trades。"""
    paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/trades" in paths


def test_trades_list_sorted_desc_and_filters(seeded):
    r = client.get("/api/v1/trades", headers=_h())
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 4
    assert [i["fill_id"] for i in body["items"]] == ["fill-3", "fill-4", "fill-2", "fill-1"]

    # market 过滤
    r = client.get("/api/v1/trades", params={"market": "US"}, headers=_h())
    assert [i["fill_id"] for i in r.json()["items"]] == ["fill-4"]

    # ts_code 过滤（同标的两笔，倒序）
    r = client.get("/api/v1/trades", params={"ts_code": "600519.SH"}, headers=_h())
    assert [i["fill_id"] for i in r.json()["items"]] == ["fill-3", "fill-1"]

    # 日期闭区间：09-01~09-03 含两端 → fill-2, fill-1
    r = client.get(
        "/api/v1/trades",
        params={"market": "CN", "start_date": "2026-09-01", "end_date": "2026-09-03"},
        headers=_h(),
    )
    assert [i["fill_id"] for i in r.json()["items"]] == ["fill-2", "fill-1"]


def test_trades_pagination(seeded):
    r = client.get(
        "/api/v1/trades", params={"limit": 2, "offset": 1}, headers=_h()
    )
    body = r.json()
    assert body["total"] == 4  # total 为过滤集全量，不受分页影响
    assert body["limit"] == 2 and body["offset"] == 1
    assert [i["fill_id"] for i in body["items"]] == ["fill-4", "fill-2"]


def test_trades_empty_result(seeded):
    r = client.get("/api/v1/trades", params={"market": "HK"}, headers=_h())
    assert r.status_code == 200
    assert r.json() == {"total": 0, "limit": 50, "offset": 0, "items": []}


def test_trades_auth(monkeypatch):
    """鉴权：无 token 401；VIEWER 被 deny_viewer 403（与 /positions 同形态）。"""
    _mk_session(monkeypatch)  # 401/403 均在 DB 之前，但仍保证不触真库
    assert client.get("/api/v1/trades").status_code == 401
    r = client.get("/api/v1/trades", headers=_h("VIEWER"))
    assert r.status_code == 403
    assert r.json()["detail"] == {"code": "auth.viewer_forbidden"}
