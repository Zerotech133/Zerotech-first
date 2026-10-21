# -*- coding: utf-8 -*-
"""只读合成反例：观察 v2 缠论分支对正数 stop_pct 的现有行为。"""
import importlib
from pathlib import Path

import pytest


STOCK = "000001.SZ"
DAYS = ("20261013", "20261014", "20261015", "20261016")
CLOSES = (100.0, 94.0, 90.0, 87.0)


@pytest.fixture
def engine(monkeypatch):
    # 仅替换测试进程内的价格、日历和 regime；不触碰行情文件或全局服务。
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    module = importlib.import_module("backtest_engine_v2")
    monkeypatch.setattr(module, "cal_idx_all", list(DAYS))
    monkeypatch.setattr(module, "regime_of", lambda day: ("mid", "合成固定档位"))
    closes = {day: close for day, close in zip(DAYS, CLOSES)}
    opens = dict(closes, **{DAYS[1]: 100.0})
    monkeypatch.setattr(
        module, "px",
        lambda stock, day, kind: (opens if kind == "open" else closes)[day] if stock == STOCK else None,
    )
    return module


def _run(engine, stop_pct, sig_sell=None):
    cfg = engine.BacktestConfig(
        name="合成止损只读反例",
        strategy="chanlun",
        d0=DAYS[0],
        d1=DAYS[-1],
        stop_pct=stop_pct,
        cap_mode="fixed",
        cap_fixed=0.8,
        top_n=1,
        slot_pct=0.1,
        cost=0.0,
    )
    return engine.run_backtest(cfg, {DAYS[0]: {STOCK}}, sig_sell or {}, three_fail=False)


def test_chanlun_positive_stop_pct_does_not_exit_falling_position(engine):
    """T+1 以 100 入场，收盘跌至 94/90/87；5% 固定止损未产生卖单。"""
    baseline = _run(engine, 0.0)
    observed = _run(engine, 0.05)

    assert observed["meta"]["config"]["stop_pct"] == 0.05
    assert observed["trades"] == baseline["trades"] == [
        {"day": DAYS[1], "stock": STOCK, "side": "BUY", "how": "chanlun", "note": "", "pnl": None}
    ]
    assert [observed["holdings"][day][0]["pnl_pct"] for day in DAYS[1:]] == [-6.0, -10.0, -13.0]
    assert [observed["days"][day]["n_pos"] for day in DAYS[1:]] == [1, 1, 1]
    assert observed["days"] == baseline["days"]
    assert observed["holdings"] == baseline["holdings"]
    assert not any(action["type"] == "sell" for day in DAYS for action in observed["days"][day]["actions"])


def test_chanlun_sell_signal_still_exits_at_next_open(engine):
    """对照：同一跌价夹具提供卖点后才在次日开盘离场。"""
    observed = _run(engine, 0.05, {DAYS[2]: {STOCK}})

    assert [(trade["day"], trade["side"], trade["how"]) for trade in observed["trades"]] == [
        (DAYS[1], "BUY", "chanlun"),
        (DAYS[3], "SELL", "exit_open"),
    ]
    assert observed["days"][DAYS[2]]["n_pos"] == 1
    assert observed["days"][DAYS[3]]["n_pos"] == 0
