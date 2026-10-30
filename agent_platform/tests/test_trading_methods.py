# -*- coding: utf-8 -*-
"""首批交易手法规则/回放测试（WO-METHODS）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from agent_platform import trading_methods as tm  # noqa: E402


def _bars(closes, vols=None, highs=None, lows=None):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        h = highs[i] if highs else max(o, c) * 1.005
        lo = lows[i] if lows else min(o, c) * 0.995
        v = vols[i] if vols else 1000
        out.append(["2026-01-%02d" % (i + 1), o, c, h, lo, v])
    return out


def test_breakout_entry():
    closes = [10.0] * 25 + [10.8]
    vols = [1000] * 25 + [3000]
    highs = [10.2] * 25 + [10.9]
    bars = _bars(closes, vols, highs)
    assert tm.entry_signal("volume_breakout", bars) is True


def test_oversold_entry():
    closes = [10.0] * 25 + [8.0]
    bars = _bars(closes, [1000] * 26)
    assert tm.entry_signal("oversold_rebound", bars) is True


def test_unknown_method_and_simulate():
    try:
        tm.simulate("nope", _bars([1] * 30))
        assert False
    except ValueError:
        pass
    bars = _bars([10.0] * 25 + [10.8, 10.9, 11.0, 10.7, 10.5, 10.4, 10.2, 10.0, 9.8, 9.6],
                 [1000] * 25 + [3000, 1200, 1200, 1200, 1200, 1200, 1200, 1200, 1200, 1200],
                 [10.2] * 25 + [10.9] + [11.1] * 9)
    r = tm.simulate("volume_breakout", bars)
    assert r["stats"]["trades"] >= 1
    assert r["trades"][0]["entry_date"] != r["trades"][0]["exit_date"]


def test_replay_injected_kline():
    bars = _bars([10.0] * 25 + [10.8, 10.9, 11.0, 10.7, 10.5, 10.4, 10.2, 10.0, 9.8],
                 [1000] * 25 + [3000] + [1200] * 8,
                 [10.2] * 25 + [10.9] + [11.1] * 8)
    out = tm.replay(["600519"], days=40, kline_fn=lambda code, n: bars)
    assert "volume_breakout" in out["methods"]
    assert out["methods"]["volume_breakout"]["codes"] == 1
    assert out["per_code"]["600519"]["bars"] == len(bars)


def test_costs_reduce_net_return():
    bars = _bars([10.0] * 25 + [10.8, 10.9, 11.0, 10.7, 10.5, 10.4, 10.2, 10.0, 9.8],
                 [1000] * 25 + [3000] + [1200] * 8,
                 [10.2] * 25 + [10.9] + [11.1] * 8)
    gross = tm.simulate("volume_breakout", bars)
    net = tm.simulate("volume_breakout", bars, costs=tm.DEFAULT_COSTS)
    if gross["trades"] and net["trades"]:
        assert net["trades"][0]["return_pct"] < gross["trades"][0]["return_pct"]
        assert net["trades"][0]["cost_pct"] > 0


def test_market_regime_and_filter():
    idx = _bars([10.0 + i * 0.05 for i in range(30)])
    regime = tm.market_regime(idx)
    assert regime and any(regime.values())
    bars = _bars([10.0] * 25 + [10.8, 10.9, 11.0, 10.7, 10.5, 10.4, 10.2, 10.0, 9.8],
                 [1000] * 25 + [3000] + [1200] * 8,
                 [10.2] * 25 + [10.9] + [11.1] * 8)
    # 全部日期 regime=false → 不交易
    false_map = {str(b[0]): False for b in bars}
    assert tm.simulate("volume_breakout", bars, regime_by_date=false_map)["stats"]["trades"] == 0


def test_portfolio_caps_and_nav():
    bars = _bars([10.0] * 25 + [10.8, 10.9, 11.0, 10.7, 10.5, 10.4, 10.2, 10.0, 9.8],
                 [1000] * 25 + [3000] + [1200] * 8,
                 [10.2] * 25 + [10.9] + [11.1] * 8)
    r = tm.simulate("volume_breakout", bars)
    trades = r["trades"]
    if trades:
        stats = tm.portfolio_stats({"A": trades, "B": trades},
                                   {"A": bars, "B": bars},
                                   costs=tm.DEFAULT_COSTS, limits={"single_max": 0.10, "total_max": 0.20})
        assert stats["limits"]["single_max"] == 0.10
        assert stats["limits"]["total_max"] == 0.20
        assert stats["nav_end"] <= 1.2  # 总仓上限 20%，不杠杆
        assert stats["max_drawdown_pct"] <= 0
