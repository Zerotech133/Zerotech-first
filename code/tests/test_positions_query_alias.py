"""deep-test B7 E 组：GET /positions?position_id= query 形态别名（B4 遗留）。

CRYPTO position_id 可含斜杠（ts_code 形如 XRP/USDT 拼进 id），path 模板
`[^/]+` 匹配不了（%2F 路由同样不认）→ 详情须走 query；原 path 形态保留
兼容无斜杠调用（同 B3 范式）。memory 后端（get_db）+ 唯一 position_id
防会话污染。
"""
from __future__ import annotations

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from zhice_os_v2.api.dependencies import get_db
from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus

client = TestClient(app)
_h = {"Authorization": "Bearer " + create_token("u-b7e", "b7e", "ADMIN")}


@pytest.fixture(autouse=True)
def _isolate_list_positions_db(monkeypatch):
    """list_positions 的列表路径（不带 position_id）直查 ORM：positions 模块顶层
    `from ...session import get_session` 绑定，绕过 conftest 的 _guard_real_pg 守卫，
    会连真实 PostgreSQL（本机 venv 缺 psycopg 或真库不可达时 500）。与 conftest
    account_service fixture 同范式：内存 sqlite + create_all，把 positions 模块的
    get_session 指向该库。本文件三个详情用例走 get_db（memory）不受影响。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.api.routers.positions as positions_mod
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(positions_mod, "get_session", lambda: Session())

SLASH_POSITION_ID = "pos-XRP/USDT-fit-20260907-abc"
PLAIN_POSITION_ID = "pos-plain-1"


def _lot(position_id: str, market: str = "CN", ts_code: str = "600519.SH") -> PositionLot:
    return PositionLot(
        position_id=position_id,
        strategy_version_id="v1-cn-sv-1",
        market=market,
        ts_code=ts_code,
        direction="BUY",
        open_qty=Decimal("100"),
        open_price=Decimal("10"),
        status=PositionStatus.OPEN,
    )


def test_position_detail_slash_id_via_query():
    db = get_db()
    db.save_position(_lot(SLASH_POSITION_ID, market="CRYPTO", ts_code="XRP/USDT"))
    r = client.get("/api/v1/positions", params={"position_id": SLASH_POSITION_ID}, headers=_h)
    assert r.status_code == 200, r.text
    body = r.json()
    # query 形态返回与 path 详情一致的 payload（非列表）
    assert body["position_id"] == SLASH_POSITION_ID
    assert body["market"] == "CRYPTO"
    assert body["ts_code"] == "XRP/USDT"


def test_position_detail_plain_id_via_path_compat():
    db = get_db()
    db.save_position(_lot(PLAIN_POSITION_ID))
    r = client.get(f"/api/v1/positions/{PLAIN_POSITION_ID}", headers=_h)
    assert r.status_code == 200, r.text
    assert r.json()["position_id"] == PLAIN_POSITION_ID


def test_position_detail_query_404_when_absent():
    r = client.get("/api/v1/positions", params={"position_id": "pos-nope/x"}, headers=_h)
    assert r.status_code == 404


def test_positions_without_query_returns_list():
    """不带 position_id 时维持列表语义（不破坏存量调用）。"""
    r = client.get("/api/v1/positions", headers=_h)
    assert r.status_code == 200
    assert isinstance(r.json(), list)
