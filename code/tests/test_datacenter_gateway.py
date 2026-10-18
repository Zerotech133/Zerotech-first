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
