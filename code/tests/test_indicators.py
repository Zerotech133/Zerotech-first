"""技术指标单元测试。"""
from fastapi.testclient import TestClient

from zhice_os_v2.api.main import app

client = TestClient(app)

from zhice_os_v2.domain.strategy.indicators import bollinger_bands, macd


def test_macd_requires_enough_data():
    closes = [float(i) for i in range(40)]
    result = macd(closes)
    assert result is not None
    assert "macd" in result
    assert "signal" in result
    assert "histogram" in result


def test_macd_returns_none_for_short_data():
    closes = [1.0, 2.0, 3.0]
    result = macd(closes)
    assert result is None


def test_macd_values_are_floats():
    closes = [100.0 + i * 0.5 for i in range(40)]
    result = macd(closes)
    assert result is not None
    assert isinstance(result["macd"], float)
    assert isinstance(result["signal"], float)
    assert isinstance(result["histogram"], float)


def test_macd_series_requires_enough_data():
    closes = [float(i) for i in range(50)]
    result = macd(closes, return_series=True)
    assert result is not None
    assert "macd_series" in result
    assert "signal_series" in result
    assert "histogram_series" in result
    assert len(result["macd_series"]) == len(closes)


def test_bollinger_bands_returns_three_lines():
    closes = [100.0 + (i % 5) for i in range(30)]
    result = bollinger_bands(closes)
    assert result is not None
    assert "upper" in result
    assert "middle" in result
    assert "lower" in result


def test_bollinger_bands_returns_none_for_short_data():
    result = bollinger_bands([1.0, 2.0])
    assert result is None


def test_bollinger_upper_above_lower():
    closes = [100.0 + (i % 7) * 0.3 for i in range(30)]
    result = bollinger_bands(closes)
    assert result is not None
    assert result["upper"] > result["lower"]
    assert result["upper"] > result["middle"] > result["lower"]


def test_bollinger_series():
    closes = [100.0 + (i % 5) * 0.5 for i in range(40)]
    result = bollinger_bands(closes, return_series=True)
    assert result is not None
    assert len(result["upper_series"]) == len(closes)
    assert len(result["middle_series"]) == len(closes)
    assert len(result["lower_series"]) == len(closes)


def test_indicators_api_returns_structure():
    resp = client.get("/api/v1/market/indicators/600519.SH?period=day")
    assert resp.status_code == 200
    data = resp.json()
    assert "ts_code" in data
    assert "macd" in data
    assert "bollinger" in data


def test_indicators_api_macd_keys_when_present():
    resp = client.get("/api/v1/market/indicators/600519.SH?period=day")
    assert resp.status_code == 200
    macd_data = resp.json()["macd"]
    if macd_data is not None:
        assert "macd" in macd_data
        assert "signal" in macd_data
        assert "histogram" in macd_data
