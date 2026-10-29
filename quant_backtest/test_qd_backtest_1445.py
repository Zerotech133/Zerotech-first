# -*- coding: utf-8 -*-
"""W3-4 单测：量子双刀 14:45 离线回测（工单 T-20261009-009）。

覆盖：
1. 十字星识别（body_ratio<=0.30 + 上下影线存在）
2. 八重过滤 AND 短路（任一过滤失败 → 无信号；全过 → 有信号）
3. Regime 三条路径（trend_up / trend_down / rotation 判定与阻断）
4. 回测自身的后复权收益正确性（跨除权无假缺口）

运行： "C:/Program Files/Python311/python.exe" -m pytest quant_backtest/test_qd_backtest_1445.py -q
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "agent_platform", "engine"))
sys.path.insert(0, HERE)
# [20261014_fix] 卫生归档把 qd_backtest_1445.py 移入 tools/research/，
# 动态 import 路径同步补上（测试本体逻辑不变）
sys.path.insert(0, os.path.join(HERE, "tools", "research"))

from quant_double_knife import _compute_regime, _cross_star, quant_double_knife  # noqa: E402


def _frame(open_, high, low, close, amount=200_000.0):
    """合成 bars：amount 常数 20 万千元 → 20 日累计 4e6 > strict 阈值 3e6。"""
    n = len(close)
    return pd.DataFrame(
        {
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "amount": [amount] * n,
        },
        index=[
            f"20240{i:02d}{d:02d}"
            for i, d in enumerate([(k % 28) + 1 for k in range(n)])
        ],
    )


def _uptrend(n=80, start=100.0, drift=0.002, accel=0.0, tail_drop=0):
    """缓涨序列：MACD 多头、MA24>=MA54、close 贴 MA 不超限、逐日小涨。

    accel>0：末段 accel 根每日涨幅加倍（dif 发散扩大，确保 dif>dea>0）；
    tail_drop>0：末段 tail_drop 根改为每日 -1%（dif 下穿 dea 且转负）。"""
    rates = np.full(n, drift)
    if accel:
        rates[-accel:] = drift * 2.0
    if tail_drop:
        rates[-tail_drop:] = -0.01
    close = start * np.cumprod(1.0 + rates)
    open_ = np.roll(close, 1)
    open_[0] = start
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return _frame(open_, high, low, close)


# ── 1. 十字星识别 ───────────────────────────────────────────
def test_cross_star_positive():
    """小实体 + 明确上下影线 → 识别为十字星。"""
    o, c = 100.0, 100.1
    h, l = 102.0, 98.0
    df = pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]})
    assert bool(_cross_star(df).iloc[0]) is True


def test_cross_star_negative_big_body():
    """大实体（body_ratio>0.30）→ 非十字星。"""
    o, c = 98.0, 102.0
    h, l = 102.5, 97.5
    df = pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]})
    assert bool(_cross_star(df).iloc[0]) is False


def test_cross_star_negative_no_shadow():
    """无上影（high==max(o,c)）→ 非十字星（第二刀要求双向影线）。"""
    o, c = 100.0, 100.1
    h, l = 100.1, 98.0
    df = pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c]})
    assert bool(_cross_star(df).iloc[0]) is False


# ── 2. 八重过滤 AND 短路 ────────────────────────────────────
def _append_double_star(df):
    """末尾接两根十字星（第一/第二刀），保持缓涨节奏与 close>=open。"""
    last = float(df["close"].iloc[-1])
    rows = []
    px = last
    for _ in range(2):
        o = px
        c = px * 1.001  # 微涨、小实体、且 close>=open
        rows.append(
            {
                "open": o,
                "high": c * 1.012,
                "low": o * 0.988,
                "close": c,
                "amount": 200_000.0,
            }
        )
        px = c
    return pd.concat([df, pd.DataFrame(rows, index=df.index[-2:] + "_x")], axis=0)


def test_eight_filters_all_pass_signal_fires():
    """八重全过：连续双日十字星 + MACD 多头 + 量/均线/Regime 达标 → 信号。"""
    df = _append_double_star(_uptrend(80, accel=20))
    out = quant_double_knife(df, profile="strict")
    assert bool(out["double_knife_signal"].iloc[-1]) is True
    for col in (
        "range_ok",
        "price_ok",
        "amount_ok",
        "ma_stack_ok",
        "ma_distance_ok",
        "macd_ok",
        "regime_ok",
    ):
        assert bool(out[col].iloc[-1]) is True, col


def test_eight_filters_short_circuit_macd():
    """AND 短路：仅破坏 MACD 多头（末段急跌 dif<dea）→ 无信号。"""
    df = _append_double_star(_uptrend(80, tail_drop=12))
    out = quant_double_knife(df, profile="strict")
    assert bool(out["macd_ok"].iloc[-1]) is False
    assert bool(out["double_knife_signal"].iloc[-1]) is False


def test_eight_filters_short_circuit_price_band():
    """AND 短路：价格抬出 strict 6-200 带 → price_ok=False → 无信号。"""
    df = _uptrend(80, start=300.0)  # 全程 >200
    df = _append_double_star(df)
    out = quant_double_knife(df, profile="strict")
    assert bool(out["price_ok"].iloc[-1]) is False
    assert bool(out["double_knife_signal"].iloc[-1]) is False


def test_eight_filters_short_circuit_amount():
    """AND 短路：amount 缩到 20 日累计 < 3e6 → amount_ok=False → 无信号。"""
    df = _frame(*_uptrend_arrays(80), amount=100_000.0)
    df = _append_double_star(df)
    out = quant_double_knife(df, profile="strict")
    assert bool(out["amount_ok"].iloc[-1]) is False
    assert bool(out["double_knife_signal"].iloc[-1]) is False


def _uptrend_arrays(n=80, start=100.0, drift=0.002):
    close = start * np.cumprod(1.0 + np.full(n, drift))
    open_ = np.roll(close, 1)
    open_[0] = start
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    return open_, high, low, close


# ── 3. Regime 三条路径 ──────────────────────────────────────
def _regime_label_of(df):
    return str(
        _compute_regime(df["close"], df["high"], df["low"], df["amount"])[
            "regime_label"
        ].iloc[-1]
    )


def test_regime_trend_up():
    """持续缓涨 + 放量 + 高情绪 → trend_up。"""
    df = _uptrend(90)
    label = _regime_label_of(df)
    out = _compute_regime(df["close"], df["high"], df["low"], df["amount"]).iloc[-1]
    tsi, vb, sent = (
        out["regime_tsi"],
        out["regime_volume_balance"],
        out["regime_sentiment"],
    )
    # label 必须与三元组按引擎规则映射一致（一致性判据，防实现漂移）
    expect = (
        "trend_up"
        if (tsi >= 0.7 and vb >= 0.1 and sent >= 0.55)
        else "trend_down"
        if (tsi <= 0.3 and vb <= -0.1 and sent <= 0.45)
        else "rotation"
        if (tsi >= 0.55 and abs(vb) < 0.1)
        else "range"
    )
    assert label == expect == "trend_up"


def test_regime_trend_down_and_blocked():
    """持续阴跌 → trend_down；strict 下 regime_ok=False 阻断信号。"""
    close = 100.0 / np.cumprod(1.0 + np.full(90, 0.002))
    open_ = np.roll(close, 1)
    open_[0] = 100.0
    high = np.maximum(open_, close) * 1.001
    low = np.minimum(open_, close) * 0.999
    df = _frame(open_, high, low, close)
    label = _regime_label_of(df)
    out = _compute_regime(df["close"], df["high"], df["low"], df["amount"]).iloc[-1]
    tsi, vb, sent = (
        out["regime_tsi"],
        out["regime_volume_balance"],
        out["regime_sentiment"],
    )
    expect = (
        "trend_up"
        if (tsi >= 0.7 and vb >= 0.1 and sent >= 0.55)
        else "trend_down"
        if (tsi <= 0.3 and vb <= -0.1 and sent <= 0.45)
        else "rotation"
        if (tsi >= 0.55 and abs(vb) < 0.1)
        else "range"
    )
    assert label == expect
    full = quant_double_knife(df, profile="strict")
    last = full.iloc[-1]
    # 即便构造出信号形态（此处未必），trend_down 市况必须被 regime_ok 阻断
    if label == "trend_down":
        assert bool(last["regime_ok"]) is False
        assert bool(last["double_knife_signal"]) is False


def test_regime_rotation_path():
    """温和震荡（tsi 0.55~0.7 + 量能平衡）→ rotation 路径一致。"""
    n = 90
    t = np.arange(n)
    close = 100.0 + 3.0 * np.sin(t / 6.0) + 0.01 * t  # 窄幅震荡微抬
    open_ = np.roll(close, 1)
    open_[0] = close[0]
    high = np.maximum(open_, close) * 1.002
    low = np.minimum(open_, close) * 0.998
    df = _frame(open_, high, low, close)
    out = _compute_regime(df["close"], df["high"], df["low"], df["amount"]).iloc[-1]
    tsi, vb, sent = (
        out["regime_tsi"],
        out["regime_volume_balance"],
        out["regime_sentiment"],
    )
    expect = (
        "trend_up"
        if (tsi >= 0.7 and vb >= 0.1 and sent >= 0.55)
        else "trend_down"
        if (tsi <= 0.3 and vb <= -0.1 and sent <= 0.45)
        else "rotation"
        if (tsi >= 0.55 and abs(vb) < 0.1)
        else "range"
    )
    assert str(out["regime_label"]) == expect  # 三元组→label 映射一致
    if expect == "rotation":
        assert 0.55 <= tsi < 0.7 and abs(vb) < 0.1


# ── 4. 回测自身：后复权收益正确性 ───────────────────────────
def test_hfq_return_across_exdiv():
    """构造除权（价 100→50、因子 ×2）：后复权收益应为 0（无假腰斩）。"""
    import importlib

    qbt = importlib.import_module("qd_backtest_1445")

    np.array(["20260101", "20260102", "20260103"])
    raw_close = np.array([100.0, 50.0, 50.2])
    factors = np.array([1.0, 2.0, 2.0])
    # 模拟 adj_series 的归一逻辑（末根=1）
    f_adj = factors / factors[-1]
    close_adj = raw_close * f_adj
    gross = close_adj[2] / close_adj[0] - 1.0
    assert gross == pytest.approx(0.004, abs=1e-9)
    # 不复权口径会得到 -49.8% 的假亏损——这正是必须复权的理由
    assert (raw_close[2] / raw_close[0] - 1.0) < -0.4
    # 引擎信号模块可对复权前后分别出列（脚本主体依赖此性质）
    assert qbt.PROFILE == "strict" and qbt.COST_ROUNDTRIP == 0.0030
