"""只读订阅观察端点的集成测试。

端点：GET /api/v1/my/subscriptions/{strategy_id}/positions
      GET /api/v1/my/subscriptions/{strategy_id}/trades

订阅校验是唯一授权门：未订阅 → 403 billing.not_subscribed；
已订阅 → 200 + service 数据。
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token


def _headers(uid: str = "u-sub", role: str = "USER") -> dict:
    return {"Authorization": f"Bearer {create_token(uid, 'sub', role)}"}


def test_positions_requires_active_subscription(monkeypatch):
    import zhice_os_v2.api.routers.subscription_observation as mod

    monkeypatch.setattr(mod.SubscriptionService, "is_subscribed", lambda self, u, s: False)
    c = TestClient(app)
    r = c.get("/api/v1/my/subscriptions/v1-cn-strat-4/positions", headers=_headers())
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "billing.not_subscribed"


def test_positions_visible_to_subscriber(monkeypatch):
    import zhice_os_v2.api.routers.subscription_observation as mod

    monkeypatch.setattr(mod.SubscriptionService, "is_subscribed", lambda self, u, s: True)
    monkeypatch.setattr(
        mod.SubscriptionObservationService,
        "list_platform_positions",
        lambda self, sid: [{"ts_code": "000001", "pnl_amount": 5.0}],
    )
    c = TestClient(app)
    r = c.get("/api/v1/my/subscriptions/v1-cn-strat-4/positions", headers=_headers())
    assert r.status_code == 200
    assert r.json()[0]["ts_code"] == "000001"
