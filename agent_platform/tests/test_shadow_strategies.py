# -*- coding: utf-8 -*-
"""军团 shadows 三策略插件测试（trend/momentum/mean-reversion，K-ORG-167）。

覆盖：三插件方向性行为（趋势/动量/回归）、样本不足降级 hold、
Registry 装配、产出全部过 Verdict 白名单、零 pandas 依赖。
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))

from shadow_strategies import (  # noqa: E402
    MeanReversionAgent, MomentumAgent, TrendFollowAgent,
    build_default_registry)
from strategy_registry import Verdict  # noqa: E402


def _closes(*vals):
    return list(vals)


def _uptrend(n=25, start=100.0, step=1.0):
    return [start + i * step for i in range(n)]


def _downtrend(n=25, start=200.0, step=-1.5):
    return [start + i * step for i in range(n)]


def _flat(n=25, price=100.0, jitter=0.1):
    return [price + (jitter if i % 2 else -jitter) for i in range(n)]


def _ctx(closes, symbol="600519"):
    return {"symbol": symbol, "closes": closes}


# ---------------------------------------------------------------- TrendFollow

def test_trend_uptrend_buys():
    v = TrendFollowAgent().propose(_ctx(_uptrend()))
    assert v.recommendation == "buy" and v.stance == "bull"
    assert v.confidence > 0.5


def test_trend_downtrend_sells():
    v = TrendFollowAgent().propose(_ctx(_downtrend()))
    assert v.recommendation == "sell" and v.stance == "bear"


def test_trend_flat_holds():
    v = TrendFollowAgent().propose(_ctx(_flat()))
    assert v.recommendation == "hold" and v.stance == "neutral"


def test_trend_insufficient_sample_degrades_hold():
    v = TrendFollowAgent().propose(_ctx([100.0, 101.0]))
    assert v.recommendation == "hold" and v.confidence == 0.0


# ---------------------------------------------------------------- Momentum

def test_momentum_strong_up():
    # 20 日 +10% → strong_buy
    v = MomentumAgent().propose(_ctx(_uptrend(n=21, start=100.0, step=0.5)))
    assert v.recommendation == "strong_buy" and v.stance == "bull"


def test_momentum_strong_down():
    v = MomentumAgent().propose(_ctx(_downtrend(n=21, start=150.0, step=-1.0)))
    assert v.recommendation in ("sell", "strong_sell") and v.stance == "bear"


def test_momentum_flat_holds():
    v = MomentumAgent().propose(_ctx(_flat()))
    assert v.recommendation == "hold"


# ---------------------------------------------------------------- MeanReversion

def test_meanrev_spike_sells():
    # 平稳 25 天后单日暴涨 30% → z 超阈值 → 超买回归 sell
    closes = _flat(n=24) + [130.0]
    v = MeanReversionAgent().propose(_ctx(closes))
    assert v.recommendation == "sell" and v.stance == "bear"


def test_meanrev_crash_buys():
    closes = _flat(n=24, price=100.0) + [60.0]
    v = MeanReversionAgent().propose(_ctx(closes))
    assert v.recommendation == "buy" and v.stance == "bull"


def test_meanrev_flat_holds():
    v = MeanReversionAgent().propose(_ctx(_flat()))
    assert v.recommendation == "hold"


# ---------------------------------------------------------------- 装配与契约

def test_registry_has_three_strategies():
    reg = build_default_registry()
    assert reg.list_ids() == ["shadow_meanrev", "shadow_momentum",
                              "shadow_trend"]


def test_all_outputs_pass_verdict_whitelist():
    reg = build_default_registry()
    for closes in (_uptrend(), _downtrend(), _flat(), [1.0, 2.0]):
        for sid in reg.list_ids():
            v = reg.run(sid, _ctx(closes))  # run() 内部强制 validate
            assert isinstance(v, Verdict)


def test_determinism():
    a = TrendFollowAgent().propose(_ctx(_uptrend()))
    b = TrendFollowAgent().propose(_ctx(_uptrend()))
    assert (a.recommendation, a.confidence, a.score) == \
           (b.recommendation, b.confidence, b.score)


def test_symbol_carried_into_verdict():
    v = TrendFollowAgent().propose(_ctx(_uptrend(), symbol="300750"))
    assert v.symbol == "300750"
