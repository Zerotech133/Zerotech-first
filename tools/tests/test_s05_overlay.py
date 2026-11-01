# -*- coding: utf-8 -*-
"""tools/tests/test_s05_overlay.py · S05×S01 叠加测试（tools/research/s05_overlay_test.py）单元测试。

覆盖（任务验收要求 ≥4）：
  1. S01 过滤复刻：ST/次新/低价/低流动性/涨停/缺财务/亏损/极端涨幅 过滤 + 流通市值降序 topN 选取；
  2. 质量陷阱剔除：PE 三分位最高层的 ROE 层内 top20% 被剔除（与 build_baseline 同口径）；
  3. 叠加权重：LX20 信号股降配公式（w=0 剔除、w=1 退化为基线、归一化、全部触发时空仓）；
  4. T+1 收盘执行约束与成本：执行日涨停/停牌不可买 → 现金；首期成本 = 换手×2×30bp；
  5. 指标口径：年化收益/波动/MDD/Sharpe 数学正确；
  6. 全流程 schema：合成数据 run() 输出 variants/registry_entry/conclusions 可 JSON 序列化、Markdown 可渲染。

不跑真实全量：合成面板 140 天 × 60 只，单测 < 10s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_s05_overlay.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "tools" / "research"
sys.path.insert(0, str(RESEARCH))

import factor_lib as fl                 # noqa: E402
import s05_overlay_test as ov          # noqa: E402


# ------------------------------------------------------------------ 合成数据
def make_panel(n_dates: int = 140, n_codes: int = 60, seed: int = 9) -> tuple[fl.Panel, pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2025-01-06", periods=n_dates).strftime("%Y%m%d")
    codes = [f"{i:06d}.SZ" for i in range(1, n_codes + 1)]
    ret = rng.normal(0.0005, 0.02, size=(n_dates, n_codes))
    close = 10.0 * np.exp(np.cumsum(np.log1p(ret), axis=0))
    pre = np.vstack([close[0:1], close[:-1]])
    pct = (close / pre - 1.0) * 100.0
    open_ = pre * (1.0 + rng.normal(0.0, 0.005, close.shape))
    high = np.maximum(open_, close) * (1.0 + np.abs(rng.normal(0.0, 0.003, close.shape)))
    low = np.minimum(open_, close) * (1.0 - np.abs(rng.normal(0.0, 0.003, close.shape)))
    vol = rng.lognormal(12.0, 0.3, size=close.shape)
    amount = vol * close * 0.1
    pe = rng.uniform(5.0, 50.0, size=close.shape)
    roe = rng.uniform(0.01, 0.20, size=close.shape)
    circ_mv = rng.uniform(1e6, 5e7, size=close.shape)

    def fr(x):
        return pd.DataFrame(x, index=dates, columns=codes)

    fields = {"open": fr(open_), "high": fr(high), "low": fr(low), "close": fr(close),
              "pre_close": fr(pre), "pct_chg": fr(pct), "vol": fr(vol),
              "amount": fr(amount), "adj_close": fr(close), "pe_ttm": fr(pe), "roe": fr(roe)}
    panel = fl.Panel(fields)
    meta = pd.DataFrame({"name": [f"股票{c}" for c in codes],
                         "industry": ["银行"] * 5 + ["电子"] * (n_codes - 5),
                         "list_date": ["20100101"] * n_codes}, index=pd.Index(codes))
    return panel, fr(circ_mv), meta


# ------------------------------------------------------------------ 1. S01 过滤
def test_s01_filters_and_top_selection():
    panel, circ_mv, meta = make_panel(n_dates=30, n_codes=10, seed=2)
    i = 25  # 第 25 日（ret20 可算）
    close = panel.close
    codes = list(panel.codes)
    # 构造可识别样本：代码 0 ST、1 低价、2 低流动性、3 涨停、4 亏损、5 缺 PE、6 极端涨幅
    meta.loc[codes[0], "name"] = "*ST样本"
    close.iloc[i, 1] = 1.5
    panel.fields["amount"].iloc[i, 2] = 1e4           # 千元 → 1000 万元 < 5000 万
    panel.fields["pct_chg"].iloc[i, 3] = 9.9
    panel.fields["roe"].iloc[i, 4] = -0.05
    panel.fields["pe_ttm"].iloc[i, 5] = np.nan
    panel.fields["adj_close"].iloc[i, 6] = panel.fields["adj_close"].iloc[i - 20, 6] * 2.0  # ret20=+100%
    masks = ov.compute_s01_masks(panel, circ_mv, meta, top=10)
    elig = masks["eligible"][i]
    for bad in (0, 1, 2, 3, 4, 5, 6):
        assert not bool(elig[bad]), f"股票 {codes[bad]} 应被过滤"
    picks = masks["picks"][i]
    assert picks and len(picks) <= 10
    assert not (set(picks) & set(range(7)))
    # 判选顺序：可候选按流通市值降序
    cap = np.asarray(circ_mv)[i]
    got = [cap[j] for j in picks]
    assert got == sorted(got, reverse=True)


# ------------------------------------------------------------------ 2. 质量陷阱
def test_quality_trap_filter():
    panel, circ_mv, meta = make_panel(n_dates=30, n_codes=6, seed=3)
    i, codes = 25, list(panel.codes)
    # 6 只全部通过基础过滤；PE 设 10/20/30/40/50/60 → 高层为 50/60；
    # ROE 设 0.05/0.06/0.07/0.08/0.09/0.20 → 高 PE 层内 roe top20% 为 code5（0.20）
    for k, val in enumerate([10.0, 20.0, 30.0, 40.0, 50.0, 60.0]):
        panel.fields["pe_ttm"].iloc[i, k] = val
    for k, val in enumerate([0.05, 0.06, 0.07, 0.08, 0.09, 0.20]):
        panel.fields["roe"].iloc[i, k] = val
    masks = ov.compute_s01_masks(panel, circ_mv, meta, top=6)
    picks = set(masks["picks"][i])
    assert codes[5] not in {panel.codes[j] for j in picks}, "高估值×高 ROE 质量陷阱应被剔除"
    assert len(picks) == 5


# ------------------------------------------------------------------ 3. 叠加权重
def test_overlay_target_weights():
    window = np.zeros((2, 4), dtype=bool)
    window[1, 0] = True   # code0 触发
    window[1, 1] = True   # code1 触发
    base = [0, 1, 2, 3]
    codes = pd.Index(["a", "b", "c", "d"])
    w0 = ov.overlay_target_weights(base, 1, window, codes, 0.0)
    assert set(w0) == {2, 3}
    assert sum(w0.values()) == pytest.approx(1.0)
    assert w0[2] == pytest.approx(0.5)
    w25 = ov.overlay_target_weights(base, 1, window, codes, 0.25)
    # 非信号 2 只（权重1）、信号 2 只（权重0.25）：denom = 2 + 0.5 = 2.5
    assert w25[2] == pytest.approx(1 / 2.5) and w25[0] == pytest.approx(0.25 / 2.5)
    assert sum(w25.values()) == pytest.approx(1.0)
    w1 = ov.overlay_target_weights(base, 1, window, codes, 1.0)
    assert all(v == pytest.approx(0.25) for v in w1.values())      # 退化为等权基线
    # 全部触发且 w=0 → 空仓
    window_all = np.ones((2, 4), dtype=bool)
    assert ov.overlay_target_weights(base, 1, window_all, codes, 0.0) == {}
    # 无触发 → 基线等权
    empty = np.zeros((2, 4), dtype=bool)
    assert ov.overlay_target_weights(base, 1, empty, codes, 0.0)[0] == pytest.approx(0.25)


# ------------------------------------------------------------------ 4. 执行约束/成本
def test_simulate_execution_limitup_and_cost():
    dates = pd.bdate_range("2025-01-06", periods=5).strftime("%Y%m%d")
    codes = ["a", "b"]
    adj = pd.DataFrame([[10.0, 10.0], [10.0, 10.0], [11.0, 10.0], [12.1, 10.0], [13.31, 10.0]],
                       index=dates, columns=codes)
    fields = {k: adj.copy() for k in ("open", "high", "low", "close", "pre_close", "adj_close")}
    fields["pct_chg"] = pd.DataFrame(0.0, index=dates, columns=codes)
    fields["vol"] = pd.DataFrame(1e5, index=dates, columns=codes)
    fields["amount"] = pd.DataFrame(1e6, index=dates, columns=codes)
    panel = fl.Panel(fields)
    # 执行日 i=1：code b 涨停不可买 → 权重 0.5 转现金；code a 可买 0.5
    n = len(dates)
    ex = {"limitup": np.zeros((n, 2), bool), "tradable": np.ones((n, 2), bool)}
    ex["limitup"][1, 1] = True
    schedule = [{"decision_i": 0, "exec_i": 1, "weights": {0: 0.5, 1: 0.5}}]
    sim = ov.simulate_portfolio(panel, schedule, ex)
    # 执行后可买权重 sum=0.5，首期 turn=0.5*0.5=0.25，成本=2*0.003*0.25=0.0015
    r2 = adj.iloc[2, 0] / adj.iloc[1, 0] - 1.0            # code a 第 3 日收益 = +10%
    assert sim["daily"][2] == pytest.approx(0.5 * r2 - 0.0015)
    assert sim["cash"][0][1] == pytest.approx(0.5)       # 转现金权重
    # 停牌（不可交易）同样转现金
    ex2 = {"limitup": np.zeros((n, 2), bool), "tradable": np.ones((n, 2), bool)}
    ex2["tradable"][1, 0] = False
    sim2 = ov.simulate_portfolio(panel, schedule, ex2)
    assert sim2["cash"][0][1] == pytest.approx(0.5)


# ------------------------------------------------------------------ 5. 指标
def test_series_stats_math():
    dates = pd.Index([f"2026010{i}" for i in range(1, 7)])
    daily = np.array([0.01, -0.02, 0.03, 0.01, -0.01, 0.02])
    st = ov.series_stats(daily, dates)
    assert st["days"] == 6
    cum = float(np.prod(1.0 + daily))
    assert st["ann_ret_pct"] == pytest.approx((cum ** (252 / 6) - 1) * 100, abs=1e-3)
    assert st["mdd_pct"] == pytest.approx(-2.0, abs=1e-3)   # 1.01→0.9898 = -2.0%
    ex = ov.excess_stats(daily, dates)
    assert ex["ann_excess_pct"] == pytest.approx(daily.mean() * 252 * 100, abs=1e-3)
    assert ex["te_ann_pct"] == pytest.approx(daily.std(ddof=1) * np.sqrt(252) * 100, abs=1e-3)


# ------------------------------------------------------------------ 6. 全流程
def test_run_payload_schema_and_markdown(monkeypatch):
    panel, circ_mv, meta = make_panel()
    monkeypatch.setattr(ov.rav, "load_circ_mv", lambda *a, **k: circ_mv)
    monkeypatch.setattr(ov, "load_names_meta", lambda *a, **k: meta)
    payload = ov.run(start=str(panel.dates[30]), end=str(panel.dates[-2]),
                     weights=(0.0, 0.25, 0.5), panel=panel, validate=False)
    assert set(payload) == {"meta", "variants", "exposure", "registry_entry", "conclusions"}
    assert set(payload["variants"]) >= {"BASELINE", "W0", "W0.25", "W0.5"}
    base = payload["variants"]["BASELINE"]
    for key in ("overall", "by_year", "bear_2026"):
        assert key in base, key
    for w in (0.0, 0.25, 0.5):
        v = payload["variants"][f"W{w:g}"]
        assert v["weight_signal_stock"] == pytest.approx(w)
        assert "excess" in v and "excess_by_year" in v
        assert "ann_excess_pct" in v["excess"]
    entry = payload["registry_entry"]
    for key in ("strategy_id", "library_slot", "validation_stage", "metrics",
                "validation_result", "shadow_portfolio_recommendation", "missing_before_shadow"):
        assert key in entry, key
    assert entry["library_slot"] == "S05"
    assert payload["conclusions"]["bullets"]
    json.loads(json.dumps(payload, ensure_ascii=False))
    md = ov.render_markdown(payload)
    assert "叠加" in md and "registry" in md and "结论" in md
