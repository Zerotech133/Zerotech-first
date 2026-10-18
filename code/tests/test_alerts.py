from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

client = TestClient(app)


def test_create_and_ack_alert(auth_headers):
    resp = client.post(
        "/api/v1/alerts",
        json={"level": "CRITICAL", "title": "风控熔断", "message": "触发熔断"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    alert_id = resp.json()["alert_id"]

    resp = client.get("/api/v1/alerts", headers=auth_headers)
    assert len(resp.json()) == 1

    resp = client.post(f"/api/v1/alerts/{alert_id}/ack", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json()["status"] == "ACKED"
