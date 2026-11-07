# -*- coding: utf-8 -*-
"""策略中台只读 API 的注册/错误语义测试（不连接真实 PG）。"""
from __future__ import annotations

from fastapi.testclient import TestClient


def test_strategy_center_routes_registered():
    from zhice_os_v2.api.main import app

    paths = app.openapi()["paths"]
    for p in (
        "/api/v1/strategy-center/snapshot",
        "/api/v1/strategy-center/strategies",
        "/api/v1/strategy-center/strategies/{sid}",
        "/api/v1/strategy-center/health",
        "/api/v1/strategy-center/pool-candidates",
    ):
        assert p in paths, p


def test_strategy_center_snapshot_returns_service_payload(monkeypatch, auth_headers):
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services import qengine_sync_service as svc

    expected = {
        "meta": {"asof": "20261105"},
        "counts": {"strategy": 27},
        "strategies": [],
        "latest_picks": [],
    }
    monkeypatch.setattr(svc, "fetch_snapshot", lambda: expected)
    with TestClient(app) as client:
        resp = client.get("/api/v1/strategy-center/snapshot", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["counts"]["strategy"] == 27


def test_strategy_center_unavailable_is_503(monkeypatch, auth_headers):
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services import qengine_sync_service as svc

    def _raise():
        raise svc.QengineSyncUnavailable("qengine_sync 测试库不可连接")

    monkeypatch.setattr(svc, "fetch_snapshot", _raise)
    with TestClient(app) as client:
        resp = client.get("/api/v1/strategy-center/snapshot", headers=auth_headers)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "qengine_sync_unavailable"


def test_strategy_center_detail_404(monkeypatch, auth_headers):
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services import qengine_sync_service as svc

    def _raise(sid: str):
        raise KeyError(sid)

    monkeypatch.setattr(svc, "fetch_strategy_detail", _raise)
    with TestClient(app) as client:
        resp = client.get(
            "/api/v1/strategy-center/strategies/UNKNOWN", headers=auth_headers
        )
    assert resp.status_code == 404


def test_strategy_center_requires_auth():
    """无 token 必须 401，不得泄露只读数据。"""
    from zhice_os_v2.api.main import app

    with TestClient(app) as client:
        resp = client.get("/api/v1/strategy-center/snapshot")
    assert resp.status_code == 401


def test_strategy_center_requires_admin():
    """前端入口仅 ADMIN 可见，API 必须同口径拒绝 VIEWER。"""
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.token_service import create_token

    token = create_token(user_id="u-viewer", username="viewer", role="VIEWER")
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app) as client:
        resp = client.get("/api/v1/strategy-center/snapshot", headers=headers)
    assert resp.status_code == 403
