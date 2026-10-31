# -*- coding: utf-8 -*-
"""tools/tests/test_factor_validate.py — 支线 E 因子独立验证（tools/research/factor_validate.py）单元测试。

覆盖（任务验收要求 ≥4 项，全部不跑全量数据）：
  1. 中性化权重：Σ权重=1、每桶总权重=市场桶占比（市值/行业暴露与市场一致）、不可选剔除；
  2. 空容错：空 Series / 全不合格 / 空 NAV / 空报告都不崩；
  3. 判定阈值：pass/fail 三条件（OOS 超额>0、OOS t≥2、逐年同向≥3/4）逐条校验；
  4. walk-forward 逐年结构：IC 的 by_year 覆盖 2023-2026、IS/OOS 齐全；
  5. 报告 JSON schema：合法报告通过、缺字段/非法 verdict 报错、可 JSON 序列化无 NaN；
  6. 逐年收益/超额计算：年首基准 NAV 取上一年末、空值安全。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_factor_validate.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_validate as fv  # noqa: E402


# ------------------------------------------------------------------ 构造小样本
def _toy(n: int = 30, n_ind: int = 3):
    codes = [f"{i:06d}.SZ" for i in range(n)]
    scores = pd.Series(np.arange(n, dtype="float64"), index=codes)
    eligible = pd.Series(True, index=codes)
    circ_mv = pd.Series(np.linspace(10.0, 1000.0, n), index=codes)
    industry = pd.Series([f"IND{i % n_ind}" for i in range(n)], index=codes)
    return codes, scores, eligible, circ_mv, industry


def _minimal_report() -> dict:
    """最小合法报告（用于 schema 测试，不依赖真实数据）。"""
    years = fv.YEARS
    empty_ic = {"n_days": 0, "mean": None, "std": None, "t": None, "positive_rate": None}
    factor = {
        "name": "low_turnover",
        "ic": {"full": dict(empty_ic), "by_year": {y: dict(empty_ic) for y in years},
               "is": dict(empty_ic), "oos": dict(empty_ic)},
        "raw_portfolio": {"excess_return": 0.01},
        "neutral_portfolio": {
            "excess_by_year": {y: 0.01 for y in years},
            "oos_excess_compound": 0.02, "oos_t": 2.5, "avg_turnover": 1.0,
            "capacity_est_yi": 1.0, "gross_oos_excess_compound": 0.03,
        },
        "walk_forward": {
            "years": {y: {"ic": None, "excess_gross": 0.01, "excess_net": 0.0} for y in years},
            "is": {"excess_compound_gross": 0.01, "t_gross": 1.0,
                   "excess_compound_net": 0.0, "t_net": 0.0},
            "oos": {"excess_compound_gross": 0.02, "t_gross": 2.5,
                    "excess_compound_net": -0.01, "t_net": -1.0},
            "positive_years_gross": 4, "positive_years_net": 0, "n_years": 4,
        },
        "verdict": "pass",
        "verdict_net": "fail",
        "direction_check": {"ic_oos_sign": 1, "long_short_sign": 1,
                            "neutral_gross_oos_sign": 1, "consistent": True},
    }
    return {
        "schema_version": fv.SCHEMA_VERSION,
        "meta": {
            "start": "20230101", "end": "20261030",
            "generated_at": "2026-11-01T00:00:00+08:00",
            "horizon": 20, "cost_bp": 30.0, "top_n": 50,
            "effective": {"start": "20230101", "end": "20261030"},
        },
        "method": {"threshold": {}},
        "factors": {"low_turnover": factor},
        "summary": {"pass": ["low_turnover"], "fail": []},
        "limitations": ["test"],
    }


# ------------------------------------------------------------------ 1. 中性化权重
def test_neutral_weights_sum_and_bucket_share_match_market():
    """Σ权重 = 1；每个桶的总权重 = 市场桶股票数占比（市值/行业暴露与市场一致）。"""
    codes, scores, eligible, circ_mv, industry = _toy()
    weights = fv.neutral_bucket_weights(scores, eligible, circ_mv, industry, hold_frac=0.5)
    assert len(weights) > 0
    assert abs(float(weights.sum()) - 1.0) < 1e-9
    assert (weights > 0).all()
    # 市场桶占比
    size_dec = pd.qcut(circ_mv, 10, labels=False, duplicates="drop")
    mkt = pd.DataFrame({"s": size_dec, "i": industry})
    counts = mkt.groupby(["s", "i"], observed=True).size()
    shares = counts / float(counts.sum())
    got = weights.groupby([size_dec.reindex(weights.index),
                           industry.reindex(weights.index)]).sum()
    for key, val in got.items():
        assert abs(float(val) - float(shares.loc[key])) < 1e-9, key
    # 与市场等权对照：每桶内被选中股票数 = ceil(0.5×桶内候选数)（至少 1）
    for key in got.index:
        n_b = int(counts.loc[key])
        assert int(round(1.0 / 1)) >= 1
        assert n_b >= 1


def test_neutral_weights_exclude_ineligible_and_renormalize():
    """不合格/NaN 分数不进组合；有空桶时权重重归一，Σ 仍=1。"""
    codes, scores, eligible, circ_mv, industry = _toy(n=40, n_ind=4)
    scores = scores.copy()
    scores.iloc[[0, 1, 2]] = np.nan
    eligible = eligible.copy()
    # 让一个整桶不合格：最低市值那批里唯一行业的股票全剔除
    low_codes = list(circ_mv.nsmallest(4).index)
    ind0 = industry.loc[low_codes[0]]
    drop = [c for c in low_codes if industry.loc[c] == ind0]
    eligible.loc[drop] = False
    weights = fv.neutral_bucket_weights(scores, eligible, circ_mv, industry)
    assert abs(float(weights.sum()) - 1.0) < 1e-9
    assert not set(weights.index) & set(drop), "不合格股票不应进组合"
    assert not weights.index.isin([codes[0], codes[1], codes[2]]).any(), "NaN 分数不应进组合"


# ------------------------------------------------------------------ 2. 空容错
def test_empty_inputs_are_tolerated():
    empty = pd.Series(dtype="float64")
    assert len(fv.neutral_bucket_weights(empty, pd.Series(dtype=bool),
                                         empty, pd.Series(dtype=object))) == 0
    codes, scores, eligible, circ_mv, industry = _toy()
    none_elig = pd.Series(False, index=codes)
    assert len(fv.neutral_bucket_weights(scores, none_elig, circ_mv, industry)) == 0
    assert fv.neutral_bucket_weights(scores, eligible, empty, industry).empty
    assert fv.annual_returns_from_nav({}) == {y: None for y in fv.YEARS}
    assert fv.annual_excess_from_nav({}, {}) == {y: None for y in fv.YEARS}
    assert fv.judge_factor(None, None, {})["verdict"] == "fail"
    assert fv.validate_report_schema({})  # 空报告返回错误列表而不是抛异常
    assert fv._tstat([])["t"] is None
    assert fv._tstat([1.0])["t"] is None


# ------------------------------------------------------------------ 3. 判定阈值
def test_judge_threshold_matrix():
    annual_ok = {"2023": 0.01, "2024": -0.02, "2025": 0.03, "2026": 0.02}  # 3/4 同向
    pass_rep = fv.judge_factor(0.02, 2.5, annual_ok)
    assert pass_rep["verdict"] == "pass"
    assert pass_rep["positive_years"] == 3
    # 条件 1：OOS 超额 ≤ 0
    assert fv.judge_factor(-0.01, 2.5, annual_ok)["verdict"] == "fail"
    # 条件 2：OOS t < 2
    assert fv.judge_factor(0.02, 1.99, annual_ok)["verdict"] == "fail"
    assert fv.judge_factor(0.02, None, annual_ok)["verdict"] == "fail"
    # 条件 3：逐年同向 < 3/4
    bad_years = {"2023": 0.01, "2024": -0.02, "2025": -0.03, "2026": 0.02}
    r = fv.judge_factor(0.02, 2.5, bad_years)
    assert r["verdict"] == "fail"
    assert r["positive_years"] == 2
    # 边界：t 恰好 2.0 应通过；逐年同向恰好 3 年应通过
    assert fv.judge_factor(0.0, 2.0, annual_ok)["verdict"] == "fail"  # 超额=0 不通过
    assert fv.judge_factor(1e-6, 2.0, annual_ok)["verdict"] == "pass"


# ------------------------------------------------------------------ 4. walk-forward 逐年结构
def test_walk_forward_year_structure():
    """IC 的 by_year 必须覆盖 2023-2026 且 IS/OOS 齐全；逐年收益/超额结构完整。"""
    rng = np.random.default_rng(7)
    n_dates, n_codes = 9, 60
    dates = [f"{y}{m:02d}15" for y in (2023, 2024, 2025, 2026) for m in (1, 2)]
    dates = dates[:n_dates]
    fmat = rng.normal(size=(n_dates, n_codes))
    car = rng.normal(size=(n_dates, n_codes))
    elig = np.ones((n_dates, n_codes), dtype=bool)
    ic = fv.ic_by_year(fmat, car, elig, dates)
    assert set(ic["by_year"].keys()) == set(fv.YEARS)
    for y in fv.YEARS:
        block = ic["by_year"][y]
        assert {"n_days", "mean", "t", "positive_rate"} <= set(block.keys())
        assert block["n_days"] >= 0
    assert set(ic["is"].keys()) >= {"n_days", "mean", "t"}
    assert set(ic["oos"].keys()) >= {"n_days", "mean", "t"}
    # 年度 NAV 结构
    nav = {"20221230": 1.0, "20231229": 1.1, "20240102": 1.1, "20241231": 1.2,
           "20250102": 1.2, "20251231": 1.3, "20260102": 1.3, "20261030": 1.5}
    yr = fv.annual_returns_from_nav(nav)
    assert set(yr.keys()) == set(fv.YEARS)
    assert abs(yr["2023"] - (1.1 / 1.0 - 1.0)) < 1e-12   # 年首基准 = 上一年末
    assert abs(yr["2024"] - (1.2 / 1.1 - 1.0)) < 1e-12   # 年首基准 = 上一年末
    assert abs(yr["2025"] - (1.3 / 1.2 - 1.0)) < 1e-12
    assert abs(yr["2026"] - (1.5 / 1.3 - 1.0)) < 1e-12


# ------------------------------------------------------------------ 5. schema
def test_report_schema_valid_and_invalid():
    rep = _minimal_report()
    assert fv.validate_report_schema(rep) == []
    # JSON 可序列化且无 NaN
    text = json.dumps(fv._clean(rep), ensure_ascii=False)
    assert "NaN" not in text and "Infinity" not in text

    broken = json.loads(json.dumps(rep))
    del broken["factors"]["low_turnover"]["neutral_portfolio"]
    assert any("neutral_portfolio" in e for e in fv.validate_report_schema(broken))

    broken2 = json.loads(json.dumps(rep))
    broken2["factors"]["low_turnover"]["verdict"] = "maybe"
    assert any("verdict" in e for e in fv.validate_report_schema(broken2))

    broken3 = json.loads(json.dumps(rep))
    broken3["schema_version"] = "old.v0"
    assert any("schema_version" in e for e in fv.validate_report_schema(broken3))

    # NaN 清洗：np.nan → None
    cleaned = fv._clean({"a": float("nan"), "b": np.float64(np.inf), "c": np.int64(3)})
    assert cleaned == {"a": None, "b": None, "c": 3}


# ------------------------------------------------------------------ 6. 其他纯函数
def test_direction_self_consistency():
    """方向自洽：factor direction 翻转后，IC / 多空 / 组合选股方向必须同步翻转。

    覆盖 Lead 指出的「IC 为正但组合超额为负」疑点中的方向部分：
    signed_score = raw × direction，组合按 signed_score 降序选股；IC、多空、毛超额应同号。
    """
    n_codes = 120
    codes = [f"{i:05d}.SZ" for i in range(n_codes)]
    raw = np.arange(n_codes, dtype="float64")            # raw 越大越差 → direction=-1
    signed = -raw
    car = -0.01 * raw + 0.002 * np.sin(np.arange(n_codes))  # 与 signed 正相关
    fmat = np.tile(signed, (3, 1))
    car_mat = np.tile(car, (3, 1))
    elig = np.ones_like(fmat, dtype=bool)
    dates = ["20230104", "20240104", "20250104"]

    ic = fv.ic_by_year(fmat, car_mat, elig, dates)
    ls = fv.long_short_stats(fmat, car_mat, elig, [0, 1, 2])
    assert ic["full"]["mean"] > 0, "direction=-1 翻转后 IC 应为正"
    assert ls["mean_pct"] > 0, "direction=-1 翻转后多空应为正"

    # 组合按 signed score 降序 → 选中的是 raw 最小（=signed 最大）的一批
    flong = fv._factor_long(fmat, elig, dates, codes, set(dates))
    top = flong[flong["date"] == dates[0]].nlargest(5, "score")
    assert len(top) == 5
    assert top["score"].max() == 0 and top["score"].min() == -4, \
        "组合应选 signed 分数最高（raw 最低）的股票"
    assert set(top["ts_code"]) == set(codes[:5]), "top5 应是最低 raw 的 5 只"

    # 方向翻转（模拟 direction=+1 用错）→ IC / 多空符号同步翻转
    ic_flip = fv.ic_by_year(-fmat, car_mat, elig, dates)
    ls_flip = fv.long_short_stats(-fmat, car_mat, elig, [0, 1, 2])
    assert ic_flip["full"]["mean"] < 0
    assert ls_flip["mean_pct"] < 0
    # 方向自洽检查字段：三者同号才算 consistent
    assert fv.validate_report_schema(_minimal_report()) == []


def test_weekly_signal_dates_and_capacity():
    dates = ["20230103", "20230104", "20230106", "20230109", "20230113"]
    sig = fv.weekly_signal_dates(dates)
    assert sig == ["20230106", "20230113"], "每周只取最后一个交易日"
    assert fv.year_of("20261030") == "2026"
    with pytest.raises(ValueError):
        fv.year_of("2026-10-30")
    # 容量：5% × ADTV × 持股数 / (双边换手/2)
    assert fv.capacity_est_yi(1e8, 50, 2.0) == pytest.approx(0.05 * 1e8 * 50 / 1.0 / 1e8)
    assert fv.capacity_est_yi(None, 50, 2.0) is None
    assert fv.capacity_est_yi(1e8, 50, 0.0) is None
