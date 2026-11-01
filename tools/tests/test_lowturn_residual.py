# -*- coding: utf-8 -*-
"""tools/tests/test_lowturn_residual.py — 支线 M 双重中性化残差检验（tools/research/lowturn_residual.py）。

覆盖（任务验收 ≥4 项，全部合成数据、不跑全量回测）：
  1. OLS 残差：残差与设计矩阵各列正交、均值≈0；完全线性关系时残差≈0；
  2. 设计矩阵：[1, z(logMV), z(vol20), 行业哑变量(去首类)] 结构正确；
  3. 月频残差面板：signed 对市值/波动/行业回归后，残差与 logMV/vol20 截面相关≈0、
     行业均值≈0（风格已剥离）、样本不足时跳过、回归统计（n/R²）输出；
  4. 前向填充（PIT）：调仓日只使用 ≤ 它的最近月末残差面板，早于首个回归日的行全 NaN；
  5. 信号日 IC：完美排序 IC=1、反向 -1，且只统计给定年份/信号日；
  6. 判定与 schema：pass/fail 阈值映射、注册表/JSON 可序列化。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_lowturn_residual.py
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
import lowturn_residual as lr  # noqa: E402


# ------------------------------------------------------------------ 1. OLS 残差
def test_ols_residual_orthogonal():
    rng = np.random.default_rng(0)
    n = 200
    x1 = rng.normal(size=n)
    x2 = rng.normal(size=n)
    y = 2.0 * x1 - 3.0 * x2 + rng.normal(scale=0.1, size=n)
    X = np.column_stack([np.ones(n), x1, x2])
    resid = lr.ols_residual(y, X)
    assert abs(float(np.mean(resid))) < 1e-10
    for j in range(X.shape[1]):
        assert abs(float(np.dot(X[:, j], resid))) < 1e-8
    # 完全线性可解释 → 残差≈0
    y2 = 1.0 + 5.0 * x1 - 2.0 * x2
    assert np.max(np.abs(lr.ols_residual(y2, X))) < 1e-10


# ------------------------------------------------------------------ 2. 设计矩阵
def test_build_design_matrix_structure():
    n = 30
    logmv = np.linspace(1.0, 5.0, n)
    vol = np.linspace(0.1, 0.6, n)
    ind = np.array(["A"] * 10 + ["B"] * 10 + ["C"] * 10, dtype=object)
    X = lr.build_design_matrix(logmv, vol, ind)
    # 1 截距 + 2 控制变量 + (3-1) 行业哑变量
    assert X.shape == (n, 1 + 2 + 2)
    assert np.allclose(X[:, 0], 1.0)
    assert abs(float(np.mean(X[:, 1]))) < 1e-10 and abs(float(np.std(X[:, 1]) - 1)) < 1e-8
    assert abs(float(np.mean(X[:, 2]))) < 1e-10
    # 哑变量是 0/1，每行至多 1 个非零
    dums = X[:, 3:]
    assert set(np.unique(dums)) <= {0.0, 1.0}
    assert np.max(dums.sum(axis=1)) <= 1.0
    # 收益率仅由行业决定 → 残差归零（行业效应被吸收）
    y = np.where(ind == "A", 1.0, np.where(ind == "B", 2.0, 3.0))
    resid = lr.ols_residual(y, X)
    assert np.max(np.abs(resid)) < 1e-8


# ------------------------------------------------------------------ 3. 月频残差面板
def _style_panel(n=400, n_ind=8):
    rng = np.random.default_rng(7)
    codes = [f"{i:06d}.SZ" for i in range(n)]
    ind = np.array([f"IND{i % n_ind}" for i in range(n)], dtype=object)
    logmv = rng.normal(size=n)
    circ_mv = np.exp(3.0 + logmv)          # 万元量级
    vol = rng.uniform(0.1, 0.8, size=n)
    ind_effect = np.array([float((i * 37) % 7) * 0.1 for i in range(n_ind)])
    ind_map = {f"IND{k}": ind_effect[k] for k in range(n_ind)}
    signed = 0.8 * logmv + 0.5 * vol + np.array([ind_map[x] for x in ind]) \
        + rng.normal(scale=0.3, size=n)
    dates = ["20221101", "20230131"]
    date_index = {d: i for i, d in enumerate(dates)}
    signed_mat = np.full((len(dates), n), np.nan)
    vol_mat = np.full((len(dates), n), np.nan)
    signed_mat[1] = signed
    vol_mat[1] = vol
    cmv = {dates[1]: pd.Series(circ_mv, index=[str(c) for c in codes])}
    industry = pd.Series(ind, index=[str(c) for c in codes])
    return codes, signed_mat, vol_mat, cmv, industry, date_index, logmv, vol


def test_monthly_residuals_orthogonalizes_style():
    codes, signed_mat, vol_mat, cmv, industry, date_index, logmv, vol = _style_panel()
    panels, stats = lr.monthly_residuals(
        signed_mat, vol_mat, cmv, ["20230131"], industry, codes, date_index)
    assert len(panels) == 1 and len(stats) == 1
    assert stats[0]["n"] == len(codes) and 0.0 < stats[0]["r2"] <= 1.0
    r = panels["20230131"].to_numpy()
    # 残差与 logMV / vol20 截面相关≈0（风格被剥离）
    assert abs(float(np.corrcoef(r, logmv)[0, 1])) < 0.05
    assert abs(float(np.corrcoef(r, vol)[0, 1])) < 0.05
    # 行业均值≈0（哑变量吸收）
    ind = industry.reindex(panels["20230131"].index).to_numpy()
    for name in sorted(set(ind.tolist())):
        assert abs(float(np.mean(r[ind == name]))) < 1e-8
    # 样本不足（< MIN_REGRESSION_STOCKS）→ 跳过
    small = pd.Series(np.exp(np.linspace(1, 3, 50)),
                      index=[str(c) for c in codes[:50]])
    panels2, stats2 = lr.monthly_residuals(
        signed_mat, vol_mat, {"20230131": small}, ["20230131"], industry,
        codes, date_index)
    assert panels2 == {} and stats2 == []


# ------------------------------------------------------------------ 4. 前向填充 PIT
def test_carry_forward_scores_pit():
    codes = ["a", "b", "c"]
    dates = ["20230115", "20230228", "20230315", "20230331", "20230410"]
    date_index = {d: i for i, d in enumerate(dates)}
    panel = {
        "20230228": pd.Series({"a": 1.0, "b": 2.0}),
        "20230331": pd.Series({"a": 3.0, "c": 4.0}),
    }
    mat = lr.carry_forward_scores(panel, ["20230315", "20230410", "20230115"],
                                  date_index, len(dates), codes)
    # 20230315 → 用 20230228 的面板
    assert np.allclose(mat[2, :2], [1.0, 2.0]) and np.isnan(mat[2, 2])
    # 20230410 → 用 20230331 的面板
    assert mat[4, 0] == 3.0 and np.isnan(mat[4, 1]) and mat[4, 2] == 4.0
    # 早于首个回归日 → 无信号（不可用未来数据）
    assert np.all(np.isnan(mat[0]))
    # 其余日期不被填充
    assert np.all(np.isnan(mat[1])) and np.all(np.isnan(mat[3]))


# ------------------------------------------------------------------ 5. 信号日 IC
def test_ic_by_year_signal():
    n = 60
    n_dates = 40
    dates = [d.strftime("%Y%m%d") for d in pd.bdate_range("2025-01-02", periods=n_dates)]
    rng = np.random.default_rng(3)
    car = rng.normal(0, 0.05, size=(n_dates, n))
    fmat = car.copy()
    elig = np.ones_like(car, dtype=bool)
    out = lr.ic_by_year_signal(fmat, car, elig, dates, [0, 2, 4, 6, 8])
    assert out["2025"]["n_days"] == 5
    assert out["2025"]["mean"] == pytest.approx(1.0, abs=1e-9)
    assert out["2026"]["n_days"] == 0
    out_neg = lr.ic_by_year_signal(-fmat, car, elig, dates, [0, 1])
    assert out_neg["2025"]["mean"] == pytest.approx(-1.0, abs=1e-9)


# ------------------------------------------------------------------ 6. 判定与 schema
def test_residual_verdict_and_schema():
    assert lr.residual_verdict({"verdict_net": "pass", "verdict_gross": "pass"})[
        "independent_alpha"] is True
    assert lr.residual_verdict({"verdict_net": "fail", "verdict_gross": "pass"})[
        "independent_alpha"] is False
    assert lr.residual_verdict({"verdict_net": "fail", "verdict_gross": "pass"})[
        "independent_alpha_gross"] is True
    # 注册表
    assert set(lr.SIGNALS) == {"original", "residual"}
    assert set(lr.FREQUENCIES) == {"weekly", "hold60"}
    assert lr.signal_dates_for("weekly", ["20230103", "20230104", "20230109"]) != []
    with pytest.raises(ValueError):
        lr.signal_dates_for("bad", ["20230103"])
    # JSON 序列化
    rep = {"meta": {"schema_version": lr.SCHEMA_VERSION}, "comparisons": [],
           "conclusion": {"recommendation": "stop"}, "notes": lr.build_notes()}
    txt = json.dumps(fv._clean(rep), ensure_ascii=False)
    assert json.loads(txt)["meta"]["schema_version"] == "lowturn_residual.v1"
    assert len(lr.build_notes()) >= 5
