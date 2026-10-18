"""EastMoneyHKClient 解析/过滤/口径测试（纯逻辑，mock _get 无网络）。"""
from decimal import Decimal
from unittest.mock import patch

from zhice_os_v2.infrastructure.market_data.gateways.eastmoney import (
    EastMoneyHKClient,
    EastMoneyUSClient,
    _hk5,
    _ymd,
)


def test_hk5_zero_pad():
    assert _hk5("700") == "00700"
    assert _hk5("00700") == "00700"
    assert _hk5(9988) == "09988"


def test_ymd_normalize():
    assert _ymd("2026-08-21") == "20260821"
    assert _ymd("20260821") == "20260821"
    assert _ymd(None) is None
    assert _ymd("bad") is None


def test_enumerate_filters_derivatives_and_pads():
    """正股保留并零填充；code>9999 与名称含窝轮/牛熊关键字剔除。"""
    page = {"data": {"total": 4, "diff": [
        {"f12": "700", "f14": "腾讯控股"},        # 正股 → 00700
        {"f12": "9988", "f14": "阿里巴巴-W"},     # 正股 → 09988
        {"f12": "12345", "f14": "某窝轮"},        # code>9999 剔除
        {"f12": "00001", "f14": "长和购A"},       # 名称含“购” 剔除
    ]}}
    c = EastMoneyHKClient()
    with patch.object(c, "_get", return_value=page):
        out = c.enumerate_symbols()
    codes = {r["code"] for r in out}
    assert codes == {"00700", "09988"}
    assert {r["name"] for r in out} == {"腾讯控股", "阿里巴巴-W"}


def test_daily_parses_hkd_no_scaling():
    """push2his klines：日期/开/收/高/低/量/额，真实 HKD 不缩放。"""
    j = {"data": {"code": "00700", "klines": [
        "2026-08-20,454.600,451.400,457.400,448.800,19621298,8900585984.000",
        "2026-08-21,451.000,449.400,452.600,445.000,12003800,5377148928.000",
    ]}}
    c = EastMoneyHKClient()
    with patch.object(c, "_get", return_value=j):
        bars = c.daily("00700", limit=5)
    assert len(bars) == 2
    last = bars[-1]
    assert str(last.trade_date) == "2026-08-21"
    assert last.open == Decimal("451.000")
    assert last.close == Decimal("449.400")
    assert last.high == Decimal("452.600")
    assert last.low == Decimal("445.000")
    assert last.volume_shares == Decimal("12003800")
    assert last.source_name == "eastmoney"


def test_snapshot_maps_by_code_decimal_price():
    """fltt=2 直接返回小数价；按 code 映射到请求集合。"""
    page = {"data": {"total": 2, "diff": [
        {"f12": "00700", "f2": 449.4, "f15": 452.6, "f16": 445.0,
         "f17": 451.0, "f18": 451.4, "f5": 12003800, "f6": 5377148928.0},
        {"f12": "09988", "f2": 120.5, "f15": 122.0, "f16": 119.0,
         "f17": 121.0, "f18": 121.5, "f5": 5000000, "f6": 600000000.0},
    ]}}
    c = EastMoneyHKClient()
    with patch.object(c, "_get", return_value=page):
        snaps = c.snapshot_batch(["700"])  # 未零填充也应匹配
    assert set(snaps) == {"00700"}
    s = snaps["00700"]
    assert s.price == Decimal("449.4")
    assert s.pre_close == Decimal("451.4")
    assert s.open == Decimal("451.0")
    assert s.source_name == "eastmoney"


def test_hk_minute_tz_to_utc():
    """HK 分钟：交易所本地(HKT)→UTC；klt=1 字段 时间/开/收/高/低/量/额。"""
    j = {"data": {"klines": [
        "2026-08-21 09:31,450.0,451.0,452.0,449.0,1000,450000",
        "2026-08-21 09:32,451.0,452.0,453.0,450.5,1200,540000",
    ]}}
    c = EastMoneyHKClient()
    with patch.object(c, "_get", return_value=j):
        bars = c.minute("00700", "2026-08-21")
    assert len(bars) == 2
    b = bars[0]
    assert str(b.bar_time) == "2026-08-21 01:31:00+00:00"  # 09:31 HKT → 01:31 UTC
    assert str(b.trade_date) == "2026-08-21"
    assert b.close == Decimal("451.0")
    assert b.source_name == "eastmoney"


def test_us_minute_tz_and_prefix():
    """US 分钟：NASDAQ→secid 105，本地(EDT)→UTC。"""
    j = {"data": {"klines": ["2026-08-21 09:31,200.0,201.0,202.0,199.5,5000,1000000"]}}
    c = EastMoneyUSClient()
    captured = {}

    def fake_get(params):
        captured.update(params)
        return j
    with patch.object(c, "_get", side_effect=fake_get):
        bars = c.minute("AAPL", "NASDAQ", "2026-08-21")
    assert captured["secid"] == "105.AAPL"
    assert len(bars) == 1
    assert str(bars[0].bar_time) == "2026-08-21 13:31:00+00:00"  # 09:31 EDT → 13:31 UTC
    assert bars[0].close == Decimal("201.0")


def test_us_minute_prefix_by_exchange():
    c = EastMoneyUSClient()
    for exch, pref in [("NYSE", "106"), ("AMEX", "107"), ("NASDAQ", "105"), ("?", "105")]:
        cap = {}
        with patch.object(c, "_get", side_effect=lambda p, cap=cap: (cap.update(p), {"data": {"klines": []}})[1]):
            c.minute("X", exch, "2026-08-21")
        assert cap["secid"] == f"{pref}.X"


def test_daily_empty_on_none():
    """_get 返回 None（节流/网络失败）→ 空列表，不抛。"""
    c = EastMoneyHKClient(req_interval=0)  # 关闭退避 sleep，避免测试变慢
    with patch.object(c, "_get", return_value=None):
        assert c.daily("00700") == []
        assert c.snapshot_batch(["00700"]) == {}
        assert c.enumerate_symbols() == []
