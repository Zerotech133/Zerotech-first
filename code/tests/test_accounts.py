from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

client = TestClient(app)


def test_create_and_list_account(auth_headers):
    resp = client.post(
        "/api/v1/accounts",
        json={"account_id": "acc-1", "market": "CN", "initial_capital": 1000000},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["cash"] == "1000000.0"

    resp = client.get("/api/v1/accounts", headers=auth_headers)
    assert resp.status_code == 200
    assert len(resp.json()) == 1
