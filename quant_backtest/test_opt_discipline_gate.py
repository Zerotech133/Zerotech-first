# -*- coding: utf-8 -*-
"""任务A单测：季度调参纪律门（discipline_gate）三条路径。

覆盖：通过(PASS) / 否决(VETO) / 样本不足跳过(SKIP)。
全部用 mock 折窗结果或合成 df，不读真实数据（纪律：不动 data/）。
"""

import os
import sys

import pandas as pd
import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import opt_defaults as od  # noqa: E402


def _synth_df(n, drift=0.001, seed=7):
    """合成价格序列：确定性几何随机游走 + 漂移（不读真实数据）。
    判例：Series 带默认 RangeIndex 时传 index= 会 reindex 成全 NaN 列，
    必须用 list 构造。"""
    import numpy as np

    rng = np.random.RandomState(seed)
    rets = rng.normal(drift, 0.01, n)
    px = (100 * (1 + pd.Series(rets)).cumprod()).tolist()
    return pd.DataFrame(
        {"close": px}, index=pd.date_range("2024-01-01", periods=n, freq="B")
    )


def test_gate_pass(monkeypatch):
    """通过路径：OOS ≥ 0.6×IS → PASS，允许 --apply 写入。"""
    monkeypatch.setattr(od, "_gate_folds", lambda name, params, frames: (1.00, 0.80, 6))
    g = od.discipline_gate("ma_cross", {"fast": 5, "slow": 20}, {})
    assert g["status"] == "PASS"
    assert g["is"] == 1.00 and g["oos"] == 0.80 and g["folds"] == 6
    assert "PASS" in g["msg"]


def test_gate_veto(monkeypatch):
    """否决路径：OOS < 0.6×IS → VETO，msg 含标准否决文案。"""
    monkeypatch.setattr(od, "_gate_folds", lambda name, params, frames: (1.50, 0.50, 5))
    g = od.discipline_gate("trend_follow", {"n": 10, "slope_lookback": 3}, {})
    assert g["status"] == "VETO"
    # 文案格式：纪律门否决：<策略> <参数组> OOS=x.xx < 0.6×IS=y.yy
    assert g["msg"].startswith("纪律门否决：trend_follow")
    assert "OOS=0.50" in g["msg"] and "0.6×IS=0.90" in g["msg"]


def test_gate_skip_insufficient_bars():
    """样本不足路径：合成短序列（100 bar < 252+63×4）→ SKIP 不炸任务。"""
    frames = {("CN", "TESTSYN"): _synth_df(100)}
    g = od.discipline_gate("ma_cross", {"fast": 5, "slow": 20}, frames)
    assert g["status"] == "SKIP"
    assert g["folds"] < od.GATE_MIN_FOLDS
    assert "样本不足未校验" in g["msg"]


def test_gate_skip_zero_folds(monkeypatch):
    """折窗全挂（信号函数异常）→ 0 折 → SKIP，不静默放行。"""
    monkeypatch.setattr(od, "_gate_folds", lambda name, params, frames: (None, None, 0))
    g = od.discipline_gate("ma_cross", {"fast": 5, "slow": 20}, {})
    assert g["status"] == "SKIP"


def test_gate_pass_enough_folds_synthetic():
    """合成 600 bar 强趋势序列走真实折窗：趋势市均线策略 IS/OOS 同向，
    折数应 ≥ 4（600 bar → floor((600-252)/63)+1=6 折可用段）。"""
    frames = {("CN", "TESTSYN"): _synth_df(600, drift=0.002)}
    is_mean, oos_sh, n_folds = od._gate_folds(
        "ma_cross", {"fast": 5, "slow": 20}, frames
    )
    assert n_folds >= 4
    assert is_mean is not None and oos_sh is not None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
