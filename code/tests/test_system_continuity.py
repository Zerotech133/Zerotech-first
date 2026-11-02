"""GET /api/v1/system/continuity 500 修复回归测试。

根因：`infrastructure/market_data/continuity._conn()` 直接把 settings.db_main_url
传给 psycopg2.connect；当前环境该 URL 带 SQLAlchemy driver 前缀
（postgresql+psycopg2://...），psycopg2 无法解析，导致接口 500。
修复：SQLAlchemy URL 归一化为 libpq host/port/dbname/user/password；plain URL 原样透传。
接口层：DB/查询异常统一映射 503（system.continuity_unavailable），不再裸 500。
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

import zhice_os_v2.api.routers.system_ext as se
import zhice_os_v2.infrastructure.market_data.continuity as cont


def _fake_settings(**kwargs):
    return SimpleNamespace(**kwargs)


def test_conn_normalizes_sqlalchemy_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """postgresql+psycopg2:// → psycopg2 可解析的 host/port/db/user/password。"""
    captured: dict = {}

    def fake_connect(**kwargs):
        captured.update(kwargs)
        return "conn-sqlalchemy"

    monkeypatch.setattr(cont.psycopg2, "connect", fake_connect)
    monkeypatch.setattr(
        cont,
        "get_settings",
        lambda: _fake_settings(
            db_main_url="postgresql+psycopg2://zhice:pw@localhost:5446/zhice_os_v2"
        ),
    )

    assert cont._conn() == "conn-sqlalchemy"
    assert captured["host"] == "localhost"
    assert captured["port"] == 5446
    assert captured["dbname"] == "zhice_os_v2"
    assert captured["user"] == "zhice"
    assert captured["password"] == "pw"


def test_conn_plain_url_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    """plain postgresql:// 保持原 DSN 行为，不破坏本地/旧配置。"""
    captured: dict = {}

    def fake_connect(dsn=None, **kwargs):
        captured.update({"dsn": dsn, **kwargs})
        return "conn-plain"

    monkeypatch.setattr(cont.psycopg2, "connect", fake_connect)
    monkeypatch.setattr(
        cont,
        "get_settings",
        lambda: _fake_settings(db_main_url="postgresql://u:p@db:5446/zhice_os_v2"),
    )

    assert cont._conn() == "conn-plain"
    assert captured["dsn"] == "postgresql://u:p@db:5446/zhice_os_v2"


def test_continuity_endpoint_returns_gap_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """正常返回：缺口按 symbol 汇总，symbol/window 参数透传。"""
    calls: list[dict] = []
    gap_day = date(2026, 11, 1)

    def fake_find(market, start, end, symbols=None):
        calls.append({"market": market, "start": start, "end": end, "symbols": symbols})
        return {"600519.SH": [gap_day, gap_day - timedelta(days=1)]}

    monkeypatch.setattr(cont, "find_daily_gaps", fake_find)

    result = se.continuity(market="CN", days=5, symbol="600519.SH")

    assert result["market"] == "CN"
    assert result["symbols_with_gaps"] == 1
    assert result["total_missing"] == 2
    assert result["top"] == [
        {"symbol": "600519.SH", "missing_days": 2,
         "first": str(gap_day - timedelta(days=1)), "last": str(gap_day)}
    ]
    assert calls and calls[0]["symbols"] == ["600519.SH"]
    assert calls[0]["start"] == date.today() - timedelta(days=5)
    assert calls[0]["end"] == date.today()


def test_continuity_endpoint_empty_gaps(monkeypatch: pytest.MonkeyPatch) -> None:
    """无缺口：返回 0，不因空数据报错。"""
    monkeypatch.setattr(cont, "find_daily_gaps", lambda market, start, end, symbols=None: {})

    result = se.continuity(market="CRYPTO", days=10)

    assert result["symbols_with_gaps"] == 0
    assert result["total_missing"] == 0
    assert result["top"] == []


def test_continuity_endpoint_query_error_maps_503(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB/查询异常 → 503 + 稳定 code，不再暴露裸 500。"""

    def boom(market, start, end, symbols=None):
        raise RuntimeError("invalid dsn")

    monkeypatch.setattr(cont, "find_daily_gaps", boom)

    with pytest.raises(HTTPException) as ei:
        se.continuity(market="CN", days=30)

    assert ei.value.status_code == 503
    assert ei.value.detail == {"code": "system.continuity_unavailable"}


def test_route_continuity_returns_200_with_gaps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """路由级回归：GET /api/v1/system/continuity 不再 500（鉴权用 override，DB 用桩）。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_current_user
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.security.user import Role, User

    monkeypatch.setattr(
        cont,
        "find_daily_gaps",
        lambda market, start, end, symbols=None: {"600519.SH": [date(2026, 11, 1)]},
    )
    app.dependency_overrides[get_current_user] = lambda: User(
        user_id="u-admin", username="admin", role=Role.ADMIN
    )
    try:
        resp = TestClient(app).get("/api/v1/system/continuity", params={"days": 5})
    finally:
        app.dependency_overrides.pop(get_current_user, None)

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["market"] == "CN"
    assert body["symbols_with_gaps"] == 1
    assert body["total_missing"] == 1
