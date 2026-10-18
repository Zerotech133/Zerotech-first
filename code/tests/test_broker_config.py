from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

client = TestClient(app)


def test_broker_live_toggle(auth_headers):
    resp = client.get("/api/v1/brokers/live", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["live_enabled"] is False

    resp = client.post("/api/v1/brokers/live", json={"enabled": True}, headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["live_enabled"] is True
