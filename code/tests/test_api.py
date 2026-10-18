from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

client = TestClient(app)


def test_health():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    assert resp.json()["app"] == "zhice-os-v2"


def test_register_strategy_and_approve(auth_headers):
    payload = {
        "strategy_name": "BTC RSI MeanRev",
        "market": "CRYPTO",
        "spec": {
            "spec_version": 1,
            "market": "CRYPTO",
            "universe": {"codes": ["BTC/USDT"]},
            "factors": [{"name": "rsi", "params": {"window": 14}}],
            "rules": [
                {"feature": "rsi", "op": "<", "value": 30, "action": "enter"},
                {"feature": "rsi", "op": ">", "value": 70, "action": "exit"},
            ],
        },
    }
    resp = client.post("/api/v1/strategies/register", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    version_id = data["strategy_version_id"]
    assert data["status"] == "PENDING_REVIEW"

    resp = client.post(f"/api/v1/strategies/{version_id}/approve", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "STAGING"


def test_approval_flow(auth_headers):
    payload = {
        "market": "CN",
        "strategy_version_id": 1,
        "order_intent_id": "oi-100",
        "ts_code": "600519.SH",
        "direction": "BUY",
        "qty": 100,
        "price": 1700.0,
        "reason": "test",
    }
    resp = client.post("/api/v1/approvals", json=payload, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    request_id = data["request_id"]
    assert data["status"] == "PENDING"

    resp = client.post(f"/api/v1/approvals/{request_id}/approve", json={"actor": "user"}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] in ("APPROVED", "EXECUTED")
