from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app
from zhice_os_v2.infrastructure.market_data.kline import resample_bars
from zhice_os_v2.infrastructure.market_data.memory import KlineBar

client = TestClient(app)


def _bars():
    return [
        KlineBar(
            time=f"2026-08-18T09:{m:02d}:00Z",
            open=100 + m,
            high=102 + m,
            low=99 + m,
            close=101 + m,
            volume=1000 + m,
        )
        for m in range(10)
    ]


def test_resample_5m():
    bars = resample_bars(_bars(), "5m")
    assert len(bars) == 2
    assert bars[0]["open"] == 100
    assert bars[0]["high"] == 106
    assert bars[0]["low"] == 99
    assert bars[0]["close"] == 105


def test_kline_endpoint():
    with TestClient(app) as c:
        resp = c.get("/api/v1/market/kline/600519.SH?period=5m")
    assert resp.status_code == 200
    assert len(resp.json()["bars"]) > 0


def test_research_start(auth_headers):
    with TestClient(app) as c:
        resp = c.post("/api/v1/research/start", json={"market": "CN", "topic": "均值回归"}, headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["report_id"]
    assert data["strategy_spec"]["market"] == "CN"
