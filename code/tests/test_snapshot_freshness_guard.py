"""P1-F 快照新鲜度护栏回归：护栏本体正向 + 网关 snapshot_time 用源自带时间戳。

缺陷：护栏（market_rules.can_buy/can_sell 的 age>STALE_MINUTES 拒绝）本身正确，
但多数网关把 snapshot_time 写成 datetime.now()（抓取时刻）而非行情源 tick 时间戳，
导致 age≈0 恒 PASS，护栏形同虚设。本测试：
  1) 护栏本体（此前 staleness 分支零覆盖）：陈旧→拒、新鲜→放。
  2) 网关本体：注入源时间戳为陈旧值的假响应，断言 DTO.snapshot_time==源时间戳（非≈now）。
  3) 端到端：陈旧源时间→upsert_snapshot→读库→can_buy 拒绝（真库，PG 不可达则 skip）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from zhice_os_v2.domain.execution.market_rules import STALE_MINUTES, can_buy, can_sell

# ---------------------------------------------------------------------------
# 1) 护栏本体正向测试（直接测 market_rules，不打桩）
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 8, 21, 6, 0, 0, tzinfo=UTC)


def test_can_buy_rejects_stale_snapshot():
    """快照 31 分钟未更新（>STALE_MINUTES=30）→ 疑似停牌，买不进。"""
    st = _NOW - timedelta(minutes=STALE_MINUTES + 1)
    ok, why = can_buy(10.0, 9.0, "600000", "浦发银行", snapshot_time=st, now=_NOW)
    assert ok is False
    assert "疑似停牌" in why


def test_can_buy_allows_fresh_snapshot():
    """快照 5 分钟内（<STALE_MINUTES）且价格正常 → 放行。"""
    st = _NOW - timedelta(minutes=5)
    ok, why = can_buy(10.0, 10.0, "600000", "浦发银行", snapshot_time=st, now=_NOW)
    assert ok is True
    assert why == "可买"


def test_can_sell_rejects_stale_snapshot():
    st = _NOW - timedelta(minutes=STALE_MINUTES + 1)
    ok, why = can_sell(10.0, 9.0, "600000", "浦发银行", snapshot_time=st, now=_NOW)
    assert ok is False
    assert "疑似停牌" in why


def test_can_sell_allows_fresh_snapshot():
    st = _NOW - timedelta(minutes=5)
    ok, why = can_sell(10.0, 10.0, "600000", "浦发银行", snapshot_time=st, now=_NOW)
    assert ok is True
    assert why == "可卖"


# ---------------------------------------------------------------------------
# 2) 网关本体：snapshot_time 取源自带时间戳（陈旧值），非 ≈now
# ---------------------------------------------------------------------------

# 一个明确"陈旧"的源时间（远早于任何测试运行时刻）——若网关误用 now()，断言必失败。
_STALE_SRC_UTC = datetime(2026, 8, 21, 2, 0, 0, tzinfo=UTC)


class _FakeResp:
    def __init__(self, text: str = "", payload=None) -> None:
        self.text = text
        self.encoding = None
        self._payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self):
        return self._payload


def _fresh(dt: datetime) -> bool:
    """离 now 很近（判定网关误用了 now()）。"""
    return abs((datetime.now(UTC) - dt).total_seconds()) < 120


def test_hk_tencent_snapshot_uses_source_time(monkeypatch):
    """腾讯 gtimg：payload 含日期时间字段（p[30]，Asia/Shanghai）→ 解析为 UTC。"""
    from zhice_os_v2.infrastructure.market_data.gateways import hk

    fields = ["0"] * 40
    fields[1] = "腾讯控股"
    fields[2] = "00700"
    fields[3] = "450.000"   # price
    fields[4] = "451.000"   # pre_close
    fields[5] = "452.000"   # open
    fields[6] = "1000"      # volume
    fields[30] = "2026/08/21 10:00:00"  # 源 tick 时间（HKT/CST UTC+8）→ 02:00 UTC
    fields[33] = "455.000"  # high
    fields[34] = "448.000"  # low
    fields[37] = "8900"     # turnover
    text = 'v_hk00700="' + "~".join(fields) + '";'
    monkeypatch.setattr(hk.requests, "get", lambda *a, **k: _FakeResp(text=text))

    snaps = hk.HKTencentClient().snapshot_batch(["00700"])
    assert set(snaps) == {"00700"}
    st = snaps["00700"].snapshot_time
    assert st == _STALE_SRC_UTC, f"应取源时间 {_STALE_SRC_UTC}，实得 {st}"
    assert not _fresh(st), "网关误用了 now()（RED）"


def _fake_yf_module(bar_ts, close=1.5):
    """构造一个假 yfinance 模块：Ticker(...).history(...) 返回带指定时间戳的单行 df。"""
    import pandas as pd

    class _Ticker:
        def __init__(self, *_a, **_k):
            pass

        def history(self, *_a, **_k):
            return pd.DataFrame(
                {"Open": [1.0], "High": [2.0], "Low": [0.5],
                 "Close": [close], "Volume": [100]},
                index=pd.DatetimeIndex([bar_ts]),
            )

    class _Mod:
        Ticker = _Ticker

    return _Mod()


def test_hk_yfinance_snapshot_uses_bar_time(monkeypatch):
    """yfinance 兜底快照：用 df.index[-1]（bar 时间戳），非 now()。"""
    import sys

    import pandas as pd

    from zhice_os_v2.infrastructure.market_data.gateways import hk

    bar_ts = pd.Timestamp("2026-08-21 10:00:00", tz="Asia/Hong_Kong")  # → 02:00 UTC
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf_module(bar_ts))

    snap = hk._yf_snapshot("00700")
    assert snap is not None
    assert snap.snapshot_time == _STALE_SRC_UTC
    assert not _fresh(snap.snapshot_time)


def test_us_yfinance_snapshot_uses_bar_time(monkeypatch):
    """US yfinance 兜底快照：用 df.index[-1]，非 now()。"""
    import sys

    import pandas as pd

    from zhice_os_v2.infrastructure.market_data.gateways import us

    bar_ts = pd.Timestamp("2026-08-20 22:00:00", tz="America/New_York")  # → 次日 02:00 UTC
    monkeypatch.setitem(sys.modules, "yfinance", _fake_yf_module(bar_ts, close=200.0))

    snap = us._yf_snapshot("AAPL")
    assert snap is not None
    assert snap.snapshot_time == _STALE_SRC_UTC
    assert not _fresh(snap.snapshot_time)


def test_crypto_binance_snapshot_uses_close_time(monkeypatch):
    """Binance 24hr：用 resp 的 closeTime(ms epoch)→UTC，非 now()。"""
    from zhice_os_v2.infrastructure.market_data.gateways import crypto

    close_ms = int(_STALE_SRC_UTC.timestamp() * 1000)
    rows = [{
        "symbol": "BTCUSDT", "lastPrice": "60000", "prevClosePrice": "59000",
        "openPrice": "59500", "highPrice": "61000", "lowPrice": "58000",
        "volume": "1000", "quoteVolume": "60000000", "closeTime": close_ms,
    }]
    c = crypto.BinanceSpotClient()
    monkeypatch.setattr(c, "tickers_24hr", lambda: rows)

    snaps = c.snapshot_batch(["BTC/USDT"])
    assert set(snaps) == {"BTC/USDT"}
    st = snaps["BTC/USDT"].snapshot_time
    assert st == _STALE_SRC_UTC
    assert not _fresh(st)


def test_eastmoney_snapshot_uses_source_time():
    """东财 clist：取时间字段 f124(秒 epoch)→UTC；取不到回退 now()。"""
    from unittest.mock import patch

    from zhice_os_v2.infrastructure.market_data.gateways.eastmoney import EastMoneyHKClient

    f124 = int(_STALE_SRC_UTC.timestamp())
    page = {"data": {"total": 1, "diff": [
        {"f12": "00700", "f2": 449.4, "f15": 452.6, "f16": 445.0,
         "f17": 451.0, "f18": 451.4, "f5": 12003800, "f6": 5377148928.0,
         "f124": f124},
    ]}}
    c = EastMoneyHKClient()
    with patch.object(c, "_get", return_value=page):
        snaps = c.snapshot_batch(["00700"])
    assert set(snaps) == {"00700"}
    st = snaps["00700"].snapshot_time
    assert st == _STALE_SRC_UTC
    assert not _fresh(st)


def test_eastmoney_snapshot_falls_back_now_when_no_time():
    """f124 缺失/无效 → 回退 now()（不硬造陈旧值）。"""
    from unittest.mock import patch

    from zhice_os_v2.infrastructure.market_data.gateways.eastmoney import EastMoneyHKClient

    page = {"data": {"total": 1, "diff": [
        {"f12": "00700", "f2": 449.4, "f18": 451.4},  # 无 f124
    ]}}
    c = EastMoneyHKClient()
    with patch.object(c, "_get", return_value=page):
        snaps = c.snapshot_batch(["00700"])
    assert _fresh(snaps["00700"].snapshot_time), "无源时间应回退 now()"


def test_gate_snapshot_keeps_now(monkeypatch):
    """Gate /spot/tickers 无逐标的时间戳 → 保留 now()（CRYPTO 7×24 无停牌语义）。"""
    from zhice_os_v2.infrastructure.market_data.gateways import crypto

    rows = [{"currency_pair": "BTC_USDT", "last": "60000",
             "change_percentage": "1.0", "high_24h": "61000",
             "low_24h": "58000", "quote_volume": "60000000"}]
    monkeypatch.setattr(crypto.requests, "get", lambda *a, **k: _FakeResp(payload=rows))

    snaps = crypto.GateSpotClient().snapshot_batch(["BTC/USDT"])
    assert set(snaps) == {"BTC/USDT"}
    assert _fresh(snaps["BTC/USDT"].snapshot_time)


# ---------------------------------------------------------------------------
# 3) 端到端：陈旧源时间 → upsert_snapshot → 读库 → can_buy 拒绝（真库）
# ---------------------------------------------------------------------------

def test_e2e_stale_source_time_rejected_by_guard():
    import psycopg2

    from zhice_os_v2.config.settings import get_settings
    from zhice_os_v2.infrastructure.market_data.gateways.dto import LatestSnapshotDTO
    from zhice_os_v2.infrastructure.market_data.ingest import upsert_snapshot

    dsn = get_settings().db_main_url
    try:
        probe = psycopg2.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"真库不可达，跳过端到端：{exc!r}")
    probe.close()

    market = "CN"
    symbol = "__FRESHTEST__"
    stale_src = datetime.now(UTC) - timedelta(hours=2)  # 陈旧源时间（>30min）
    dto = LatestSnapshotDTO(
        ts_code=symbol, snapshot_time=stale_src,
        price=Decimal("10.00"), pre_close=Decimal("10.00"), source_name="test",
    )
    conn = psycopg2.connect(dsn)
    try:
        upsert_snapshot([dto], market)
        # 读库拿回落库的 snapshot_time
        with conn.cursor() as cur:
            cur.execute(
                "SELECT price, pre_close, snapshot_time FROM v2_latest_snapshot "
                "WHERE market=%s AND symbol=%s", (market, symbol),
            )
            row = cur.fetchone()
        assert row is not None
        price, pre_close, db_st = row
        ok, why = can_buy(price, pre_close, symbol, None, snapshot_time=db_st)
        assert ok is False
        assert "疑似停牌" in why
    finally:
        with conn, conn.cursor() as cur:
            cur.execute("DELETE FROM v2_latest_snapshot WHERE market=%s AND symbol=%s",
                        (market, symbol))
        conn.close()
