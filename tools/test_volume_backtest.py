# -*- coding: utf-8 -*-
"""量能信号回放测试（2026-11-16 WO-VOL-BACKTEST）。

用构造序列验证：突破/回调判定、后续收益、基准计算。不发网络请求。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tools"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "volume_backtest_t", REPO / "tools" / "volume_backtest.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vb = _load()


def _series(n=30, base=10.0, vol=1000.0):
    return [{"date": "2026%04d" % (1000 + i), "close": base, "pct": 0.0, "vol": vol}
            for i in range(n)]


def test_breakout_detected_and_forward_return():
    s = _series(25)
    s[20]["pct"] = 5.0
    s[20]["vol"] = 2000.0          # 量比 2.0
    s[20]["close"] = 11.5          # 破前 20 日高（10.0）
    s[21]["close"] = 12.0
    s[25 - 1]["close"] = 12.65     # r5 ≈ +10%
    hits = vb.scan("X", s)
    assert len(hits) == 1
    h = hits[0]
    assert h["signal"] == "breakout_with_volume" and h["vol_ratio"] == 2.0
    assert h["r1"] == round((12.0 - 11.5) / 11.5 * 100, 2)


def test_shrink_pullback_detected():
    s = _series(25)
    s[20]["pct"] = -2.0
    s[20]["vol"] = 700.0           # 量比 0.7
    hits = vb.scan("X", s)
    assert [h["signal"] for h in hits] == ["shrink_pullback"]


def test_no_signal_when_volume_flat():
    assert vb.scan("X", _series(25)) == []


def test_breakout_requires_above_high20():
    s = _series(25)
    s[20].update({"pct": 5.0, "vol": 2000.0, "close": 9.8})   # 涨但没破前高
    kinds = [h["signal"] for h in vb.scan("X", s)]
    assert "breakout_with_volume" not in kinds               # 不构成放量突破
    assert "break_low20" in kinds                            # 反而触发破 20 日低


def test_baseline_shape():
    s = _series(25)
    for i, x in enumerate(s):
        x["close"] = 10.0 + i * 0.1        # 单调上涨
    b = vb.baseline(s)
    assert b["r1"]["n"] == 4                # 从第 20 日起共 25 天 → 索引 20..23
    assert b["r1"]["win_rate"] == 100.0
    assert b["r1"]["mean"] > 0


def test_summarize_empty_signal():
    out = vb.summarize([], "breakout_with_volume")
    assert out["n"] == 0
    assert out["r1"]["mean"] is None and out["r1"]["win_rate"] is None
