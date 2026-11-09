# -*- coding: utf-8 -*-
"""tools/tests/test_industry_rs_factor.py · 行业相对强度因子（RS）单元测试。

覆盖（任务验收：≥5）：
  1. 因子构建：k 日收益 + 行业 leave-one-out 相对强度公式正确；
  2. PIT：RS 因子只用 T 日及以前数据（截断不变性）；模块源码无负 lag；
  3. IC：逐日横截面 Spearman IC 与 pandas 参考实现一致；
  4. 分层：因子与标签正相关 → 十分位单调、多空为正；负相关 → 反号；换手/成本敏感性正确；
  5. 安慰剂：强相关因子的经验 p 值显著，随机因子不显著（p∈(0,1]）；BH-FDR q 值单调正确；
  6. 端到端 schema：evaluate_core 输出 JSON schema 完整可序列化、空面板容错；
  7. 市值/行业分层输出结构。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_industry_rs_factor.py
"""
from __future__ import annotations

import ast
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

import factor_lib as fl  # noqa: E402
import industry_rs_factor as irs  # noqa: E402

MODULE_PATH = ROOT / "tools" / "research" / "industry_rs_factor.py"


# ------------------------------------------------------------------ 合成面板
def make_panel(n_dates: int = 90, n_codes: int = 60, seed: int = 7) -> fl.Panel:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2026-01-05", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    ret = rng.normal(0.0005, 0.02, size=(n_dates, n_codes))
    close = 10.0 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    pre_close = np.vstack([close[0:1], close[:-1]])
    pct = (close / pre_close - 1.0) * 100.0
    open_ = pre_close * (1.0 + rng.normal(0.0, 0.005, close.shape))
    high = np.maximum(open_, close) * 1.005
    low = np.minimum(open_, close) * 0.995
    vol = rng.lognormal(11.0, 0.5, size=close.shape)
    amount = vol * close * 0.1

    def frame(arr):
        return pd.DataFrame(arr, index=dates, columns=codes)

    fields = {"open": frame(open_), "high": frame(high), "low": frame(low),
              "close": frame(close), "pre_close": frame(pre_close), "pct_chg": frame(pct),
              "vol": frame(vol), "amount": frame(amount), "adj_close": frame(close),
              "pe_ttm": frame(rng.uniform(8, 80, close.shape)),
              "turnover_rate": frame(rng.uniform(0.2, 8.0, close.shape)),
              "roe": frame(rng.normal(8.0, 4.0, close.shape))}
    return fl.Panel(fields)


def make_industry(codes: pd.Index, n_groups: int = 3) -> pd.Series:
    """把代码轮流分配到 n_groups 个行业。"""
    names = [f"行业{i}" for i in range(n_groups)]
    return pd.Series([names[i % n_groups] for i in range(len(codes))], index=codes)


# ------------------------------------------------------------------ 1. 因子构建
def test_stock_return_and_industry_loo():
    # k 日收益：复权价 T/T-k - 1
    dates = pd.bdate_range("2026-01-05", periods=6).strftime("%Y%m%d")
    adj = pd.DataFrame({"000001.SZ": [1.0, 1.1, 1.2, 1.32, 1.5, 1.6],
                        "000002.SZ": [2.0, 2.0, 2.0, 2.0, 2.0, 2.0]}, index=dates)
    fields = {name: adj.copy() for name in ("close", "adj_close", "open", "pre_close")}
    fields["pct_chg"] = adj.pct_change().fillna(0.0) * 100.0
    fields["vol"] = adj * 0 + 100.0
    fields["amount"] = adj * 0 + 1e5
    panel = fl.Panel(fields)
    r2 = irs.stock_return(panel, 2)
    np.testing.assert_allclose(r2[:, 0], adj["000001.SZ"].to_numpy() / np.concatenate([[np.nan, np.nan], adj["000001.SZ"].to_numpy()[:-2]]) - 1.0,
                               equal_nan=True)

    # RS_k = 个股收益 − 同行业其他成员等权收益（leave-one-out）
    ret = np.array([[0.10, 0.02, 0.00, 0.05, 0.05, 0.07]], dtype=float)   # 1 日 × 6 只
    ind = np.array([0, 0, 0, 1, 1, 1], dtype=np.int32)
    valid = np.ones_like(ret, dtype=bool)
    rs = irs.rs_from_industry(ret, ind, valid, n_ind=2)
    # 行业0：others0=(0.02+0.00)/2=0.01 → 0.09；others1=(0.10+0.00)/2=0.05 → -0.03；others2=0.06 → -0.06
    np.testing.assert_allclose(rs[0, :3], [0.09, -0.03, -0.06], atol=1e-12)
    # 行业1：others3=(0.05+0.07)/2=0.06 → -0.01；others4=0.06 → -0.01；others5=0.05 → +0.02
    np.testing.assert_allclose(rs[0, 3:], [-0.01, -0.01, 0.02], atol=1e-12)
    # 成员不足 2 只 → NaN（行业中仅 1 只成员）
    rs_small = irs.rs_from_industry(ret, np.array([0, 0, 0, 1, 1, 2], dtype=np.int32),
                                    valid, n_ind=3)
    assert np.isnan(rs_small[0, 5])
    assert np.isfinite(rs_small[0, 4])


def test_build_rs_factors_pit_truncation():
    """PIT：截断到 T 后重算，RS_5/10/20 在 T 日取值与全样本重算一致。"""
    panel = make_panel()
    industry = make_industry(panel.codes)
    full, names = irs.build_rs_factors(panel, industry, windows=(5, 10, 20))
    assert len(names) == 4 and "其他" in names   # 3 个行业 + 缺省「其他」组
    cut = str(panel.dates[40])
    part_panel = panel.subset(max_date=cut)
    part, _ = irs.build_rs_factors(part_panel, industry, windows=(5, 10, 20))
    for name in full:
        a = full[name][40]
        b = part[name][-1]
        np.testing.assert_allclose(a, b, rtol=1e-12, atol=1e-14, equal_nan=True,
                                   err_msg=f"{name} 在 {cut} 日存在未来数据依赖")


def test_module_has_no_negative_shift():
    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    bad = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(node):
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == "shift" and sub.args):
                try:
                    v = ast.literal_eval(sub.args[0])
                except (ValueError, SyntaxError):
                    continue
                if isinstance(v, (int, float)) and not isinstance(v, bool) and v < 0:
                    bad.add(node.name)
    assert bad == set(), f"模块出现负 lag（疑似前视）: {sorted(bad)}"


# ------------------------------------------------------------------ 2. IC / 分层 / 换手 / 成本
def test_daily_ic_matches_pandas():
    rng = np.random.default_rng(11)
    n_dates, n_codes = 8, 80
    factor = rng.normal(size=(n_dates, n_codes))
    label = 0.4 * factor + rng.normal(size=(n_dates, n_codes))
    factor[rng.random(factor.shape) < 0.1] = np.nan
    dates = [f"2026{(i % 12) + 1:02d}{(i % 27) + 1:02d}" for i in range(n_dates)]
    ic = irs.daily_ic(factor, label, np.ones_like(factor, dtype=bool), dates, min_n=20)
    for i in range(n_dates):
        both = np.isfinite(factor[i]) & np.isfinite(label[i])
        expected = pd.Series(factor[i][both]).corr(pd.Series(label[i][both]), method="spearman")
        np.testing.assert_allclose(ic.iloc[i], expected, rtol=1e-10, atol=1e-12)


def test_decile_layering_and_direction():
    rng = np.random.default_rng(5)
    n_dates, n_codes = 60, 200
    factor = rng.normal(size=(n_dates, n_codes))
    date_list = [f"2026{(i // 27) + 1:02d}{(i % 27) + 1:02d}" for i in range(n_dates)]
    # 正向：label = 2*factor + 噪声 → 分层单调递增、多空为正
    label_pos = 0.02 * factor + rng.normal(0, 0.01, size=factor.shape)
    elig = np.ones_like(factor, dtype=bool)
    dec_pos = irs.decile_eval(factor, label_pos, elig, date_list)
    means = [dec_pos["groups"][f"D{i}"]["mean_car_pct"] for i in range(1, 11)]
    assert means[-1] > means[0]
    assert dec_pos["monotonicity"] is not None and dec_pos["monotonicity"] > 0.8
    assert dec_pos["long_short"]["ann_pct"] > 0
    # 反向：label = -2*factor → 单调性为负、多空为负
    label_neg = -0.02 * factor + rng.normal(0, 0.01, size=factor.shape)
    dec_neg = irs.decile_eval(factor, label_neg, elig, date_list)
    assert dec_neg["monotonicity"] < -0.8
    assert dec_neg["long_short"]["ann_pct"] < 0


def test_turnover_and_cost_sensitivity():
    n_dates, n_codes = 30, 100
    factor = np.tile(np.arange(n_codes, dtype=float), (n_dates, 1))   # 每只股票常数 → 名单不变
    turn = irs.turnover_eval(factor, np.ones_like(factor, dtype=bool))
    assert turn["top_decile"]["avg_one_way_turnover"] == pytest.approx(0.0)
    assert turn["bottom_decile"]["avg_one_way_turnover"] == pytest.approx(0.0)
    # 成本：毛 10%、年化换手 2 次、30/50/80bp → 净 10-1.2 / 10-2.0 / 10-3.2
    cs = irs.cost_sensitivity(10.0, 2.0)
    assert cs["30bp"]["cost_drag_ann_pct"] == pytest.approx(1.2)
    assert cs["30bp"]["net_ann_pct"] == pytest.approx(8.8)
    assert cs["50bp"]["net_ann_pct"] == pytest.approx(8.0)
    assert cs["80bp"]["net_ann_pct"] == pytest.approx(6.8)


# ------------------------------------------------------------------ 3. 安慰剂 / FDR
def test_placebo_and_bh_fdr():
    rng = np.random.default_rng(3)
    n_dates, n_codes = 40, 120
    dates = [f"2026{(i // 27) + 1:02d}{(i % 27) + 1:02d}" for i in range(n_dates)]
    factor = rng.normal(size=(n_dates, n_codes))
    elig = np.ones_like(factor, dtype=bool)
    label_strong = 0.05 * factor + rng.normal(0, 0.01, size=factor.shape)
    pl_strong = irs.placebo_eval(factor, label_strong, elig, dates, n_perm=60, seed=1)
    assert pl_strong["n_perm"] == 60 and pl_strong["n_cohorts"] > 0
    assert pl_strong["p_value"] is not None and pl_strong["p_value"] < 0.05
    assert 0.0 < pl_strong["null"]["std"] > 0
    label_rand = rng.normal(size=factor.shape)
    pl_rand = irs.placebo_eval(factor, label_rand, elig, dates, n_perm=60, seed=1)
    assert 0.0 < pl_rand["p_value"] <= 1.0

    fdr = irs.bh_q([0.01, 0.02, 0.5, 0.8])
    assert fdr["n_tests"] == 4
    assert fdr["q"] == pytest.approx([0.04, 0.04, 2.0 / 3.0, 0.8], abs=1e-3)
    assert fdr["n_reject"] == 2          # q≤0.10 的两个
    assert fdr["q"][0] <= fdr["q"][1] <= fdr["q"][2] <= fdr["q"][3]


# ------------------------------------------------------------------ 4. 端到端 schema / 分层输出
def test_evaluate_core_schema_and_empty():
    panel = make_panel(n_dates=90, n_codes=60)
    industry = make_industry(panel.codes)
    rng = np.random.default_rng(9)
    cap = rng.lognormal(10, 1, size=panel.shape)
    payload = irs.evaluate_core(panel, industry, cap=cap,
                                start=str(panel.dates[0]), end=str(panel.dates[-1]),
                                n_placebo=10, seed=42)
    assert set(payload) == {"meta", "factors", "fdr", "verdict"}
    assert set(payload["factors"]) == {"RS_5", "RS_10", "RS_20"}
    for name, f in payload["factors"].items():
        for key in ("name", "origin", "direction", "ic", "deciles", "turnover",
                    "cost_sensitivity", "walk_forward", "placebo", "cap_stratified",
                    "industry_stratified"):
            assert key in f, f"{name}.{key}"
        assert f["direction"]["sign"] in (1, -1)
        assert set(f["deciles"]["groups"]) == {f"D{i}" for i in range(1, 11)}
        assert "long_short" in f["deciles"]
        assert "direction_adjusted" in f["deciles"]["long_short"]
        assert set(f["cost_sensitivity"]) == {"30bp", "50bp", "80bp"}
        assert {"top_decile", "bottom_decile"} <= set(f["turnover"])
        assert set(f["cap_stratified"]) == {"小市值", "中市值", "大市值"}
        assert f["industry_stratified"]["n_industries"] >= 1
        assert f["placebo"]["p_value"] is None or 0.0 <= f["placebo"]["p_value"] <= 1.0
    assert "tests" in payload["fdr"] and len(payload["fdr"]["tests"]) == 6
    assert isinstance(payload["verdict"]["is_effective_factor"], bool)
    assert isinstance(payload["verdict"]["near_tradable"], bool)
    # 裁决必须披露「多空月度 t / 长腿超额 / 市值分层」等可交易性证据
    for key in ("long_short_t_monthly_directed", "long_leg_excess_pct",
                "cap_ic_small", "cap_ic_large", "fdr_q_oos"):
        assert key in payload["verdict"], key
    txt = json.dumps(payload, ensure_ascii=False)
    assert "NaN" not in txt and "Infinity" not in txt

    # 空面板容错
    empty = fl.Panel.empty()
    empty_ind = pd.Series(dtype=str)
    empty_payload = irs.evaluate_core(empty, empty_ind, start="20260101", end="20261030")
    assert empty_payload["factors"] == {}
    assert empty_payload["verdict"]["is_effective_factor"] is False
    json.dumps(empty_payload, ensure_ascii=False)
    md = irs.render_markdown(empty_payload)
    assert "面板为空" in md and "Traceback" not in md


def test_industry_and_cap_stratification_outputs():
    panel = make_panel(n_dates=60, n_codes=90)
    industry = make_industry(panel.codes)
    ind_code, names = irs.industry_codes(industry, panel.codes)
    assert set(names) == {"其他", "行业0", "行业1", "行业2"} and len(ind_code) == 90
    rng = np.random.default_rng(13)
    factor = rng.normal(size=panel.shape)
    label = 0.01 * factor + rng.normal(0, 0.02, size=panel.shape)
    elig = np.ones(panel.shape, dtype=bool)
    cap = rng.lognormal(10, 1, size=panel.shape)
    dates = [str(d) for d in panel.dates]
    cap_out = irs.cap_stratified(factor, label, elig, cap, dates)
    assert set(cap_out) == {"小市值", "中市值", "大市值"}
    for stat in cap_out.values():
        assert stat["n_obs"] > 0 and stat["n_days"] > 0
    ind_out = irs.industry_stratified(factor, label, elig, ind_code, names, dates)
    assert ind_out["n_industries"] == 3
    assert 0.0 <= ind_out["share_positive"] <= 1.0
    assert ind_out["q25_ic"] <= ind_out["median_ic"] <= ind_out["q75_ic"]


def _fake_rs_factor(ls_t_monthly: float, long_leg_excess: float, net_drag: float = 1.62) -> dict:
    """为 decide_verdict 单测构造 RS_20 结果摘要（数值取自真实评测量级）。"""
    d1 = -2.0 + long_leg_excess / 2.0
    d10 = -2.0 - long_leg_excess / 2.0
    groups = {f"D{i}": {"mean_car_pct": -2.0} for i in range(1, 11)}
    groups["D1"] = {"mean_car_pct": d1}
    groups["D10"] = {"mean_car_pct": d10}
    return {
        "direction": {"sign": -1},
        "ic": {"mean": -0.0589, "t_monthly": -2.727},
        "deciles": {
            "groups": groups,
            "long_short": {"ann_pct": -16.29, "t_monthly": -abs(ls_t_monthly),
                           "direction_adjusted": {"t_monthly": abs(ls_t_monthly),
                                                  "bootstrap_p_le_0": 0.0133,
                                                  "ann_pct": 16.29}},
            "monotonicity": -0.806,
        },
        "walk_forward": {"2023-2024": {"ic": {"mean": -0.0597}, "ic_monthly_t": -1.83},
                         "2025-2026": {"ic": {"mean": -0.0579}, "ic_monthly_t": -2.604}},
        "placebo": {"p_value": 0.002},
        "cost_sensitivity": {"30bp": {"cost_drag_ann_pct": net_drag, "net_ann_pct": -16.29 - net_drag}},
        "cap_stratified": {"小市值": {"ic_mean": -0.0712}, "中市值": {"ic_mean": -0.0623},
                           "大市值": {"ic_mean": -0.0276}},
        "industry_stratified": {"median_ic": -0.0689, "share_positive": 0.0575},
    }


def test_decide_verdict_requires_tradable_long_short():
    fdr = {"tests": [{"factor": "RS_20", "period": "2025-2026", "t": -2.604, "p": 0.0092}],
           "q": [0.0276], "n_tests": 1, "q_cut": 0.10, "n_reject": 1}
    # 信息显著（IC/FDR/安慰剂都过），但多空月度 t=0.70<2、长腿超额小 → 不接近可交易
    v = irs.decide_verdict({"RS_20": _fake_rs_factor(ls_t_monthly=0.704, long_leg_excess=0.13)},
                           fdr=fdr)
    assert v["is_effective_factor"] is True
    assert v["near_tradable"] is False
    # 多空月度 t=2.5≥2 且长腿超额 1.0%≥0.5% → 接近可交易
    v2 = irs.decide_verdict({"RS_20": _fake_rs_factor(ls_t_monthly=2.5, long_leg_excess=1.0)},
                            fdr=fdr)
    assert v2["is_effective_factor"] is True
    assert v2["near_tradable"] is True
