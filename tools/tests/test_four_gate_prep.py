# -*- coding: utf-8 -*-
"""tools/tests/test_four_gate_prep.py — 四关前稳健性预检（task-154）单元测试。

覆盖（≥3，合成数据）：
  1. 中性化：OLS 残差与行业哑变量/ln 市值正交；对纯行业/市值暴露的因子残差≈0；
  2. 成本：净年化 = 毛年化 − bp×年化换手×2 腿，30/50/80bp 单调递减；
  3. 分段：walk-forward 3 段等分且连续；IC 统计字段完整；
  4. 分层：单调因子 → 单调性≈1、多空为正；
  5. 结论分级：支持/不支持边界（PIT/覆盖率/IC/IR/分段/成本）+ 真实报告 schema。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_four_gate_prep.py
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
import four_gate_prep as fg  # noqa: E402

REPORT = ROOT / "research" / "reports" / "four_gate_prep_20261103.json"


# ------------------------------------------------------------------ 1. 中性化
def test_neutralize_orthogonal():
    rng = np.random.default_rng(7)
    n = 120
    ind = np.array([0] * 40 + [1] * 40 + [2] * 40)
    ln_mv = np.linspace(1.0, 5.0, n)
    # 纯行业+市值暴露因子 → 中性化后残差≈0
    factor = np.where(ind == 0, 1.0, np.where(ind == 1, 2.0, 3.0)) + ln_mv * 0.5
    valid = np.ones(n, dtype=bool)
    resid = fg.neutralize(factor, ln_mv, ind, valid)
    assert np.abs(np.nanmean(resid)) < 1e-8
    assert np.abs(np.nanmax(np.abs(resid))) < 1e-8
    # 与暴露无关的随机因子 → 残差保留大部分方差
    noise = rng.normal(size=n)
    resid2 = fg.neutralize(noise, ln_mv, ind, valid)
    assert np.corrcoef(resid2, noise)[0, 1] > 0.5
    # 残差与行业均值正交（各行业残差均值≈0）
    for g in (0, 1, 2):
        assert abs(np.nanmean(resid2[ind == g])) < 1e-8
    # 少样本返回 NaN
    few = np.zeros(n, dtype=bool)
    few[:5] = True
    assert np.isnan(fg.neutralize(noise, ln_mv, ind, few)).all()


# ------------------------------------------------------------------ 2. 成本
def test_portfolio_cost():
    long_sets = [{0, 1}, {0, 1}, {1, 2}, {2, 3}, {3, 4}]
    short_sets = [{5, 6}, {5, 6}, {6, 7}, {7, 8}, {8, 9}]
    cost = fg.portfolio_cost(long_sets, short_sets, gross_ann_pct=20.0)
    assert cost["ann_turnover_2leg"] == pytest.approx(
        cost["ann_turnover_long"] + cost["ann_turnover_short"], abs=0.01)
    net30 = cost["30bp"]["net_ann_pct"]
    net50 = cost["50bp"]["net_ann_pct"]
    net80 = cost["80bp"]["net_ann_pct"]
    assert net30 > net50 > net80
    assert cost["50bp"]["net_ann_pct"] == pytest.approx(
        20.0 - 50 / 10000.0 * 100 * cost["ann_turnover_2leg"], abs=0.01)


# ------------------------------------------------------------------ 3. 分段 + IC
def test_segments_and_ic():
    dates = [f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(120)]
    segs = fg.segments(dates)
    assert len(segs) == 3
    covered = [i for _, idx in segs for i in idx]
    assert covered == list(range(120)), "3 段应等分且连续覆盖全部交易日"
    # IC：因子=收益 → +1
    rng = np.random.default_rng(3)
    f = rng.normal(size=(10, 80))
    y = f * 0.5 + rng.normal(scale=0.01, size=(10, 80))
    ic = fg.daily_rank_ic(f, y)
    st = fg.ic_stats(ic)
    assert st["n_days"] == 10 and st["mean"] > 0.9 and st["ir"] > 0
    assert "same_sign" in st["split"]
    assert fg.ic_stats(pd.Series(dtype=float))["n_days"] == 0


# ------------------------------------------------------------------ 4. 分层
def test_layer_stats():
    days, codes = 6, 100
    order = np.tile(np.arange(codes, dtype=float), (days, 1))
    y = order * 0.01
    lay = fg.layer_stats(order, y, 5)
    means = [lay["groups"][f"G{g}"]["mean_fwd_pct"] for g in range(1, 6)]
    assert means == sorted(means)
    assert lay["monotonicity"] == pytest.approx(1.0, abs=1e-6)
    assert lay["long_short"]["mean_fwd_pct"] > 0


# ------------------------------------------------------------------ 5. 分级 + 报告
def _ic(mean, ir, n=60):
    return {"n_days": n, "mean": mean, "ir": ir,
            "split": {"first_mean": mean, "second_mean": mean, "same_sign": True}}


def _cost(net50):
    return {f"{bp}bp": {"net_ann_pct": net50 - (bp - 50) * 0.1} for bp in (30, 50, 80)}


def test_grade_candidate_and_report():
    good = fg.grade_candidate(_ic(0.05, 0.5), [0.04, 0.05, 0.06], 0.8,
                             _cost(5.0), 90.0, True)
    assert good[0] == "支持"
    bads = [
        fg.grade_candidate(_ic(0.005, 0.1), [0.01, -0.02, 0.01], 0.3, _cost(5.0), 90.0, True),
        fg.grade_candidate(_ic(0.05, 0.5), [0.04, 0.05, 0.06], 0.8, _cost(-1.0), 90.0, True),
        fg.grade_candidate(_ic(0.05, 0.5), [0.04, 0.05, 0.06], 0.8, _cost(5.0), 30.0, True),
        fg.grade_candidate(_ic(0.05, 0.5), [0.04, 0.05, 0.06], 0.8, _cost(5.0), 90.0, False),
        fg.grade_candidate(_ic(0.05, 0.5), [-0.01, 0.02, -0.03], 0.8, _cost(5.0), 90.0, True),
    ]
    assert all(x[0] == "不支持" for x in bads)
    assert REPORT.exists(), f"报告缺失: {REPORT}"
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    assert rep.get("schema_check") == [], rep.get("schema_check")
    assert fg.validate_report_schema(rep) == []
    assert set(rep["candidates"]) == {"platform_consolidation", "chan_zs_width_reverse"}
    for c in rep["candidates"].values():
        assert c["decision"]["support"] in ("支持", "不支持")
        assert len(c["walk_forward"]) == 3
        for h in fg.HORIZONS:
            assert f"h{h}" in c["neutral_ic"]
        for bp in fg.COSTS_BP:
            assert f"{bp}bp" in c["cost"]
    assert sum(len(rep["summary"][k]) for k in ("支持", "不支持")) == 2
