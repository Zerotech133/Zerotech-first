# -*- coding: utf-8 -*-
"""只读合成反例：观察 run_backtest 对 stop_pct 的策略分支与阈值行为。"""
import importlib
from pathlib import Path

import pytest


STOCK = "000001.SZ"
DAYS = ("20261013", "20261014", "20261015", "20261016", "20261019")


@pytest.fixture
def engine(monkeypatch):
    # 隔离行情、交易日和档位；不装载真实行情、不调用会落盘的 run_job。
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    module = importlib.import_module("backtest_engine_v2")
    monkeypatch.setattr(module, "cal_idx_all", list(DAYS))
    monkeypatch.setattr(module, "regime_of", lambda day: ("mid", "合成固定档位"))
    return module


def _run(engine, monkeypatch, strategy, stop_pct, closes, *, min_holding_days=1, sig_sell=None, end_day=None):
    prices = dict(zip(DAYS, closes))
    window = DAYS[:DAYS.index(end_day) + 1] if end_day else DAYS
    # 每日开盘固定为入场价，排除跳空、涨跌停及次日开盘价格变化的干扰。
    monkeypatch.setattr(
        engine, "px",
        lambda stock, day, kind: (100.0 if kind == "open" else prices[day]) if stock == STOCK else None,
    )
    cfg = engine.BacktestConfig(
        name="合成参数边界反例", strategy=strategy, d0=DAYS[0], d1=window[-1],
        stop_pct=stop_pct, min_holding_days=min_holding_days,
        cap_mode="fixed", cap_fixed=0.8, top_n=1, slot_pct=0.1, cost=0.0,
    )
    # 双刀信号每天持续存在，排除「信号消失」；无限持有期排除「持有到期」。
    sig_buy = ({DAYS[0]: {STOCK}} if strategy == "chanlun"
               else {day: {STOCK} for day in window})
    return engine.run_backtest(cfg, sig_buy, sig_sell or {}, three_fail=False)


def _sells(result):
    return [(t["day"], t["how"], t["pnl"]) for t in result["trades"] if t["side"] == "SELL"]


# 2026-11-10 契约变更：缠论分支接入硬止损（stop_pct<0 触发、收盘确认、
# 次日开盘成交；引擎注释「参数看着传了没生效」第 4 例修复）。原
# 「两符号都忽略止损」期望过期，2026-11-11 按新契约重写：
#   负值 → 跌穿阈值 stop_loss 离场；非负（含 0=默认关）→ 不触发。
@pytest.mark.parametrize("stop_pct", [-0.125, 0.125])
def test_chanlun_stop_sign_gates_trigger(engine, monkeypatch, stop_pct):
    closes = (100.0, 100.0, 87.5, 87.0, 85.0)
    observed = _run(engine, monkeypatch, "chanlun", stop_pct, closes)

    assert observed["meta"]["config"]["stop_pct"] == stop_pct
    if stop_pct < 0:
        # T+2 收盘 87.5 跌穿 -12.5% → 触发，T+3（DAYS[3]）开盘卖
        assert [(t["day"], t["side"], t["how"]) for t in observed["trades"]] == [
            (DAYS[1], "BUY", "chanlun"),
            (DAYS[3], "SELL", "stop_loss"),
        ]
    else:
        assert [(t["day"], t["side"], t["how"]) for t in observed["trades"]] == [
            (DAYS[1], "BUY", "chanlun"),
        ]
    if stop_pct < 0:
        # 触发后 DAYS[3] 开盘卖出（open 恒为 100 → pnl 0.0），此后不再持仓
        assert [observed["holdings"][day][0]["pnl_pct"] for day in DAYS[2:3]] == [-12.5]
        assert all(observed["days"][day]["n_pos"] == 1 for day in DAYS[1:3])
        assert observed["days"][DAYS[3]]["n_pos"] == 0
        assert _sells(observed) == [(DAYS[3], "stop_loss", 0.0)]
    else:
        assert [observed["holdings"][day][0]["pnl_pct"] for day in DAYS[2:]] == [-12.5, -13.0, -15.0]
        assert all(observed["days"][day]["n_pos"] == 1 for day in DAYS[1:])
        assert _sells(observed) == []


@pytest.mark.parametrize("stop_pct", [-0.125, 0.125])
def test_chanlun_signal_exit_is_next_open_not_stop_exit(engine, monkeypatch, stop_pct):
    observed = _run(engine, monkeypatch, "chanlun", stop_pct,
                    (100.0, 100.0, 87.5, 87.0, 85.0), sig_sell={DAYS[2]: {STOCK}})

    assert observed["days"][DAYS[2]]["n_pos"] == 1
    assert observed["days"][DAYS[3]]["n_pos"] == 0
    assert any("卖点确认→明日开盘卖" in a["note"] for a in observed["days"][DAYS[2]]["actions"])
    assert _sells(observed) == [(DAYS[3], "exit_open", 0.0)]


@pytest.mark.parametrize(
    ("first_close", "sell_pnl"),
    [(87.51, None), (87.5, -12.5), (87.49, -12.51)],
)
def test_double_knife_negative_stop_strictly_above_equal_below(engine, monkeypatch, first_close, sell_pnl):
    observed = _run(engine, monkeypatch, "double_knife", -0.125,
                    (100.0, 100.0, first_close, 87.5, 85.0), end_day=DAYS[2])

    assert observed["meta"]["config"]["stop_pct"] == -0.125
    expected_trades = [(DAYS[1], "BUY")]
    if sell_pnl is not None:
        expected_trades.append((DAYS[2], "SELL"))
    assert [(t["day"], t["side"]) for t in observed["trades"]] == expected_trades
    assert observed["days"][DAYS[2]]["n_pos"] == (1 if sell_pnl is None else 0)
    assert _sells(observed) == ([(DAYS[2], "dk_灾难止损", sell_pnl)] if sell_pnl is not None else [])
    sell_actions = [a for a in observed["days"][DAYS[2]]["actions"] if a["type"] == "sell"]
    if sell_pnl is not None:
        assert len(sell_actions) == 1
        assert sell_actions[0]["note"] == "14:55 灾难止损（收盘价）"
        assert sell_actions[0]["pnl_pct"] == sell_pnl
    else:
        assert sell_actions == []


@pytest.mark.parametrize("stop_pct", [0.0, 0.125])
def test_double_knife_nonnegative_stop_does_not_sell_on_loss(engine, monkeypatch, stop_pct):
    observed = _run(engine, monkeypatch, "double_knife", stop_pct,
                    (100.0, 100.0, 87.5, 87.0, 85.0))

    assert observed["meta"]["config"]["stop_pct"] == stop_pct
    assert _sells(observed) == []
    assert all(observed["days"][day]["n_pos"] == 1 for day in DAYS[1:])
    assert observed["holdings"][DAYS[-1]][0]["pnl_pct"] == -15.0


def test_double_knife_min_holding_precedes_negative_stop(engine, monkeypatch):
    observed = _run(engine, monkeypatch, "double_knife", -0.125,
                    (100.0, 80.0, 80.0, 80.0, 80.0), min_holding_days=1, end_day=DAYS[2])

    assert observed["days"][DAYS[1]]["n_pos"] == 1
    assert observed["holdings"][DAYS[1]][0]["pnl_pct"] == -20.0
    assert not any(a["type"] == "sell" for a in observed["days"][DAYS[1]]["actions"])
    assert _sells(observed) == [(DAYS[2], "dk_灾难止损", -20.0)]
    assert observed["days"][DAYS[2]]["n_pos"] == 0


def test_double_knife_crossing_on_later_close_sells_same_day(engine, monkeypatch):
    observed = _run(engine, monkeypatch, "double_knife", -0.125,
                    (100.0, 100.0, 87.51, 87.49, 85.0), end_day=DAYS[3])

    assert observed["days"][DAYS[2]]["n_pos"] == 1
    assert not any(a["type"] == "sell" for a in observed["days"][DAYS[2]]["actions"])
    # 分段盯市浮点乘法使 87.5 的跨日比值可能略高于精确阈值；用 87.49 验证明确跌破。
    assert _sells(observed) == [(DAYS[3], "dk_灾难止损", -12.51)]
    assert observed["days"][DAYS[3]]["n_pos"] == 0
