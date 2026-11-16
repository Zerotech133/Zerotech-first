"""DataCenterGateway 解析/单位换算/VWAP 探测测试（纯逻辑，无网络）。"""
from decimal import Decimal

from zhice_os_v2.infrastructure.market_data.gateways.datacenter import DataCenterGateway


def _gw():
    # __init__ 仅建 httpx.Client（离线），不触发网络
    return DataCenterGateway()


def test_daily_standard_units():
    """daily 标准口径：vol 手→×100 股、amount 千元→×1000 元，VWAP≈close 不触发探测。"""
    g = _gw()
    try:
        item = {"tradeDate": "2026-08-20", "open": 1299.8, "high": 1306.88,
                "low": 1291.0, "close": 1291.5, "preClose": 1307.88,
                "vol": 25331.66, "amount": 3280474.23}
        bar = g._parse_daily_bar("600519.SH", item)
        assert bar is not None
        assert bar.volume_shares == Decimal("2533166.00")   # 25331.66 * 100
        assert bar.turnover_cny == Decimal("3280474230.00")  # 3280474.23 * 1000
        vwap = float(bar.turnover_cny) / float(bar.volume_shares)
        assert abs(vwap / float(bar.close) - 1) < 0.01       # VWAP≈close
    finally:
        g.close()


def test_daily_vwap_probe_uses_raw():
    """上游已是 股/元 时：标准换算 VWAP 偏离>30%、原值口径吻合 → 用原值不换算。"""
    g = _gw()
    try:
        # close=10；上游 vol=已是股(1_000_000)、amount=已是元(10_000_000)
        # 标准换算 → vol*100=1e8, amt*1000=1e10 → VWAP=100 偏离 900%；原值 VWAP=10 吻合
        item = {"tradeDate": "2026-08-20", "close": 10.0,
                "vol": 1_000_000, "amount": 10_000_000}
        bar = g._parse_daily_bar("X.SZ", item)
        assert bar.volume_shares == Decimal("1000000")   # 原值，未 ×100
        assert bar.turnover_cny == Decimal("10000000")   # 原值，未 ×1000
    finally:
        g.close()


def test_daily_skip_no_date():
    g = _gw()
    try:
        assert g._parse_daily_bar("X.SZ", {"close": 10}) is None
    finally:
        g.close()

def test_empty_api_key_raises_immediately(monkeypatch):
    """空 datacenter_api_key 必须立刻抛错，不能带空 Key 发请求。

    此前 __init__ 直接 self._api_key = s.datacenter_api_key（settings 默认空串），
    会带着空 X-API-Key 发请求 -> 服务端 401/403 -> 上层容易把「鉴权失败」
    当成「今天这个市场没数据」（同 2026-11-16 抑制令判例：读不到 ≠ 没有）。
    """
    import pytest

    import zhice_os_v2.infrastructure.market_data.gateways.datacenter as dc

    class _S:
        datacenter_base_url = "http://example.invalid:1"
        datacenter_api_key = ""
        datacenter_retry_max = 1
        datacenter_timeout_ms = 1000

    monkeypatch.setattr(dc, "get_settings", lambda: _S())
    with pytest.raises(dc.DataCenterError, match="DATACENTER_API_KEY"):
        dc.DataCenterGateway()


def test_blank_api_key_raises_immediately(monkeypatch):
    """只有空白字符也算未配置（strip 后为空）。"""
    import pytest

    import zhice_os_v2.infrastructure.market_data.gateways.datacenter as dc

    class _S:
        datacenter_base_url = "http://example.invalid:1"
        datacenter_api_key = "   "
        datacenter_retry_max = 1
        datacenter_timeout_ms = 1000

    monkeypatch.setattr(dc, "get_settings", lambda: _S())
    with pytest.raises(dc.DataCenterError, match="DATACENTER_API_KEY"):
        dc.DataCenterGateway()


def test_non_empty_api_key_builds_client(monkeypatch):
    """非空 Key 正常构造，且 X-API-Key 头确实带上（反面用例，防过度收紧）。"""
    import zhice_os_v2.infrastructure.market_data.gateways.datacenter as dc

    class _S:
        datacenter_base_url = "http://example.invalid:1/"
        datacenter_api_key = "  dc_TESTKEY  "
        datacenter_retry_max = 2
        datacenter_timeout_ms = 1000

    monkeypatch.setattr(dc, "get_settings", lambda: _S())
    g = dc.DataCenterGateway()
    try:
        assert g._api_key == "dc_TESTKEY"          # 前后空白被 strip
        assert g._base == "http://example.invalid:1"  # 末尾 / 被去掉
        assert g._client.headers.get("X-API-Key") == "dc_TESTKEY"
    finally:
        g.close()
