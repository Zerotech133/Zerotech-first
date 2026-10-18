"""Task 5: CHAN_V7 三入场门回归 + 单元测试。

对照组红线：默认路径（不传任何门参数）行为必须与改动前逐字节等价。
"""
from datetime import date

import pandas as pd

from zhice_os_v2.domain.strategy.strategies.chan import engine as chan_engine
from zhice_os_v2.domain.strategy.strategies.chan import strategy as chan_strategy


def _mini_bars(codes, n=80):
    frames = []
    for i, code in enumerate(codes):
        frames.append(pd.DataFrame({
            "ts_code": [code] * n,
            "security_id": [i + 1] * n,
            "dt": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": [10.0] * n, "high": [11.0] * n,
            "low": [9.0] * n, "close": [10.5] * n,
            "volume": [1e6] * n, "amount": [1e7] * n,
        }))
    return pd.concat(frames, ignore_index=True)


def _stub_universe(monkeypatch, codes):
    monkeypatch.setattr(chan_strategy, "_load_universe",
                        lambda td, market="CN": codes)
    monkeypatch.setattr(chan_strategy, "_load_ohlcv",
                        lambda td, codes_, market="CN": _mini_bars(codes))

    def fake_analyze(ts_code, df):
        return chan_engine.ChanSnapshot(
            symbol=ts_code, bar_count=len(df), bs_point="b3",
            divergence="medium", prev_zs={"bi_count": 10, "zd": 10.0, "zg": 11.0})
    monkeypatch.setattr(chan_engine, "analyze", fake_analyze)


def test_control_group_default_unchanged(monkeypatch):
    """对照组红线：默认路径不传门参数，两只股票全部返回。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    picks = chan_strategy.select_candidates(date(2025, 1, 2), max_candidates=20)
    assert {p["ts_code"] for p in picks} == {"AAA", "ZZZ"}


def test_market_gate_halts_all(monkeypatch):
    """门①大盘门：market_regime_permits_entry 返回 False → 空列表早退。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    monkeypatch.setattr(chan_strategy, "market_regime_permits_entry",
                        lambda td, market="CN": False)
    picks = chan_strategy.select_candidates(
        date(2025, 1, 2), max_candidates=20, market_gate=True)
    assert picks == []


def test_weekly_gate_filters_per_stock(monkeypatch):
    """门②周线门：只有 ZZZ 通过 weekly_trend_permits_long → picks 只含 ZZZ。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    monkeypatch.setattr(
        chan_strategy, "weekly_trend_permits_long",
        lambda df: bool((df["ts_code"] == "ZZZ").any()))
    picks = chan_strategy.select_candidates(
        date(2025, 1, 2), max_candidates=20, weekly_filter=True)
    assert {p["ts_code"] for p in picks} == {"ZZZ"}


def test_intraday_gate_confirms_head(monkeypatch):
    """门③30分钟确认：只有 ZZZ 通过 intraday_confirms_buy → picks 只含 ZZZ。"""
    _stub_universe(monkeypatch, ["AAA", "ZZZ"])
    monkeypatch.setattr(
        chan_strategy, "intraday_confirms_buy",
        lambda ts_code, td, market="CN": ts_code == "ZZZ")
    picks = chan_strategy.select_candidates(
        date(2025, 1, 2), max_candidates=20, intraday_confirm=True)
    assert {p["ts_code"] for p in picks} == {"ZZZ"}
