# -*- coding: utf-8 -*-
"""tools/tests/test_lowturn_attribution.py — 支线 J low_turnover 逐年/regime 归因单元测试。

覆盖（任务验收 ≥4 项，全部合成数据、不跑全量回测）：
  1. regime 状态：trailing 20 日市场收益三分位 → 上涨/震荡/下跌；前 lookback 不贴标签；
  2. regime 统计：按状态聚合的日频超额（天数/累计/日均 bp/t/胜率/净超额）；
  3. 峰谷段：某年等权市场最大回撤的峰→谷识别（峰在谷前、区间收益正确）；
  4. 贡献拆解：Brinson 式 Σ holdings×(个股−市场) 按组累计 = 组内合计、总合计正确；
     持仓权重按组汇总；
  5. IC 与拥挤度相关：完美排序 IC=1 / 反向 -1；因子与代理同向 ρ=1、反向 -1；
  6. 报告 schema：JSON 可序列化、变体注册表/指标键齐全。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_lowturn_attribution.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_validate as fv  # noqa: E402
import lowturn_attribution as la  # noqa: E402


def _nav_series(returns) -> dict:
    """第 i 日累计净值 → {8 位序号字符串: nav}（函数按日期字符串字典序索引）。"""
    nav = {f"{0:08d}": 1.0}
    v = 1.0
    for i, r in enumerate(returns, start=1):
        v *= (1.0 + r)
        nav[f"{i:08d}"] = v
    return nav


# ------------------------------------------------------------------ 1. regime 状态
def test_regime_state_series():
    # 30 天平盘 + 40 天 +1%/日 + 30 天 -1%/日 → 末段应标「下跌」、中段「上涨」
    rets = [0.0] * 30 + [0.01] * 40 + [-0.01] * 30
    nav = _nav_series(rets)
    states = la.regime_state_series(nav, lookback=20)
    dates = sorted(nav)
    assert len(states) == len(dates) - 21  # 前 lookback+1 天不贴标签
    assert states[dates[-1]] == "下跌"      # 下跌段末（trailing 20 日持续为负）
    assert states[dates[70]] == "上涨"      # 上涨段内（trailing 20 日全为 +1%）
    assert states[dates[25]] == "震荡"      # 平盘段内（trailing 20 日收益=0）
    assert dates[0] not in states
    # 空/过短输入安全
    assert la.regime_state_series({}) == {}
    assert la.regime_state_series({f"{0:08d}": 1.0, f"{1:08d}": 1.1}) == {}


# ------------------------------------------------------------------ 2. regime 统计
def test_regime_stats():
    idx = [f"2025010{i}" for i in range(1, 7)]
    excess = pd.Series([0.01, -0.02, 0.03, 0.04, -0.01, 0.02], index=idx)
    net = pd.Series([0.009, -0.021, 0.028, 0.038, -0.011, 0.019], index=idx)
    states = {idx[0]: "上涨", idx[1]: "上涨", idx[2]: "震荡", idx[3]: "震荡",
              idx[4]: "下跌", idx[5]: "下跌"}
    out = la.regime_stats(excess, states, net)
    assert out["上涨"]["n_days"] == 2
    assert out["上涨"]["gross_excess_sum"] == pytest.approx(0.01 - 0.02, abs=1e-12)
    assert out["上涨"]["gross_excess_mean_bp"] == pytest.approx(-50.0, abs=1e-6)
    assert out["上涨"]["win_rate"] == pytest.approx(0.5)
    assert out["上涨"]["net_excess_sum"] == pytest.approx(0.009 - 0.021, abs=1e-12)
    assert out["震荡"]["gross_excess_sum"] == pytest.approx(0.07, abs=1e-12)
    assert out["下跌"]["gross_t"] is not None
    # 无数据状态返回占位
    out2 = la.regime_stats(excess, {d: "上涨" for d in idx})
    assert out2["下跌"]["n_days"] == 0 and out2["下跌"]["gross_excess_sum"] is None


# ------------------------------------------------------------------ 3. 峰谷段
def test_peak_trough_segment():
    nav = {
        "20250102": 1.00, "20250103": 1.10, "20250106": 1.05,
        "20250107": 0.90, "20250108": 0.95,
        "20260105": 1.20, "20260106": 0.80,
    }
    seg = la.peak_trough_segment(nav, "2025")
    assert seg is not None
    assert seg["peak_date"] == "20250103"
    assert seg["trough_date"] == "20250107"
    assert seg["market_ret"] == pytest.approx(0.90 / 1.10 - 1.0, abs=1e-6)
    assert seg["max_drawdown"] == pytest.approx(0.90 / 1.10 - 1.0, abs=1e-6)
    # 无回撤年份 / 过短 → None
    assert la.peak_trough_segment({"20240102": 1.0, "20240103": 1.1}, "2024") is None
    assert la.peak_trough_segment(nav, "2029") is None


# ------------------------------------------------------------------ 4. 贡献拆解
def test_contribution_by_group():
    # T=3 天、N=4 只、2 组；W[t] 为 t 日收盘权重，ret[t+1] 实现 t 日后的收益
    W = np.array([
        [0.5, 0.5, 0.0, 0.0],
        [0.0, 0.0, 0.5, 0.5],
        [0.25, 0.25, 0.25, 0.25],
    ])
    ret = np.array([
        [np.nan, np.nan, np.nan, np.nan],
        [0.10, 0.00, 0.02, 0.04],
        [0.04, 0.02, 0.02, 0.00],
    ])
    mkt = np.array([np.nan, 0.05, 0.02])
    group = np.array([0, 0, 1, 1])
    contrib = la.contribution_by_group(W, ret, mkt, group, 2)
    # t=0（W[0]，ret[1]）：组 0 = 0.5×(0.10−0.05)+0.5×(0−0.05) = 0；组 1 = 0
    # t=1（W[1]，ret[2]）：组 0 = 0；组 1 = 0.5×(0.02−0.02)+0.5×(0−0.02) = -0.01
    assert contrib[0] == pytest.approx(0.0, abs=1e-12)
    assert contrib[1] == pytest.approx(-0.01, abs=1e-12)
    assert contrib.sum() == pytest.approx(-0.01, abs=1e-12)
    # 权重按组汇总：三天平均（总天数为 3）
    ws = la.weight_sum_by_group(W, group, 2) / 3
    assert ws[0] == pytest.approx((0.5 + 0.5 + 0.0 + 0.0 + 0.25 + 0.25) / 3, abs=1e-12)
    assert ws[1] == pytest.approx((0.0 + 0.0 + 0.5 + 0.5 + 0.25 + 0.25) / 3, abs=1e-12)


# ------------------------------------------------------------------ 5. IC 与拥挤度相关
def _toy_panel_ic():
    dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2025-01-02", "2025-02-28")]
    n = 60
    rng = np.random.default_rng(42)
    car = rng.normal(0, 0.05, size=(len(dates), n))
    fmat = car.copy()                      # 完美正相关 → IC=1
    elig = np.ones_like(car, dtype=bool)
    return dates, fmat, car, elig


def test_ic_and_crowding_corr():
    dates, fmat, car, elig = _toy_panel_ic()
    out = la.ic_by_year(fmat, car, elig, dates, years=("2025", "2024"))
    assert out["2025"]["n_days"] == len(dates)   # 每个交易日都算 IC
    assert out["2025"]["mean"] == pytest.approx(1.0, abs=1e-9)
    assert out["2024"]["n_days"] == 0 and out["2024"]["mean"] is None
    # 反向：IC=-1
    out_neg = la.ic_by_year(-fmat, car, elig, dates, years=("2025",))
    assert out_neg["2025"]["mean"] == pytest.approx(-1.0, abs=1e-9)
    # 拥挤度相关：同向 ρ=1、反向 ρ=-1
    sample = {"2025": list(range(len(dates)))}
    c1 = la.cross_sectional_corr_by_year(fmat, fmat.copy(), elig, dates, sample)
    c2 = la.cross_sectional_corr_by_year(fmat, -fmat.copy(), elig, dates, sample)
    assert c1["2025"]["mean"] == pytest.approx(1.0, abs=1e-9)
    assert c2["2025"]["mean"] == pytest.approx(-1.0, abs=1e-9)


# ------------------------------------------------------------------ 6. 报告 schema
def test_attribution_report_schema():
    rep = {
        "meta": {"schema_version": la.SCHEMA_VERSION, "factor": la.FACTOR,
                 "years": list(la.YEARS), "cost_bp": la.COST_BP},
        "factors": {"weekly_baseline": {"yearly": {}, "regime": {}, "crowding": {}}},
        "conclusion": {"tradeable_after_cost": False},
        "notes": la.build_notes(),
    }
    txt = json.dumps(fv._clean(rep), ensure_ascii=False)
    assert json.loads(txt)["meta"]["schema_version"] == "lowturn_attribution.v1"
    for v in la.DEFAULT_VARIANTS:
        assert la.VARIANTS[v]["schedule"] in {"weekly", "monthly", "every60"}
    assert la.STATES == ("上涨", "震荡", "下跌")
    assert len(la.STATES) == 3
    assert "年" in la.build_notes()[0] or len(la.build_notes()) >= 5
