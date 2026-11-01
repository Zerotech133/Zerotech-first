# -*- coding: utf-8 -*-
"""tools/tests/test_s05_reverse.py · S05 反向（超配）假设验证（tools/research/s05_reverse_hypothesis.py）单元测试。

覆盖（任务验收要求 ≥4）：
  1. 超配权重（乘子 m）：非信号 1、信号 m、归一化；无信号→基线等权；全信号→等权；
  2. 超配权重（占比 k）：信号股合计 k、非信号合计 1−k；边界退化；
  3. 四关判定逻辑：OOS(超额>0 且 t≥2)、逐年≥3/4、成本 (30bp 超额>0 且 t≥2 且 50bp>0)、容量≥10亿、PIT；
  4. 容量估计：按超配股票 ADV20 与 10% 参与率计算 AUM 上限；
  5. PIT 静态检查：模块内无前视 shift(-n)；
  6. 全流程 schema：合成数据 run() 输出 variants/gates/registry_entry/conclusions 可 JSON 序列化、Markdown 可渲染。

不跑真实全量：合成面板 140 天 × 60 只，单测 < 10s。
运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_s05_reverse.py
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
import s05_reverse_hypothesis as rv    # noqa: E402

MODULE = RESEARCH / "s05_reverse_hypothesis.py"


# ------------------------------------------------------------------ 合成数据
def make_panel(n_dates: int = 140, n_codes: int = 60, seed: int = 11) -> tuple[fl.Panel, pd.DataFrame, pd.DataFrame]:
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


# ------------------------------------------------------------------ 1. 乘子权重
def test_reverse_weights_multiplier():
    window = np.zeros((2, 4), dtype=bool)
    window[1, :2] = True                    # code0/1 触发
    base = [0, 1, 2, 3]
    w = rv.reverse_weights_multiplier(base, 1, window, 2.0)
    # 非信号 2 只（1）、信号 2 只（2）：denom = 2 + 4 = 6
    assert w[2] == pytest.approx(1 / 6) and w[0] == pytest.approx(2 / 6)
    assert sum(w.values()) == pytest.approx(1.0)
    # m=1 → 基线等权
    w1 = rv.reverse_weights_multiplier(base, 1, window, 1.0)
    assert all(v == pytest.approx(0.25) for v in w1.values())
    # 无触发 → 基线等权
    empty = np.zeros((2, 4), dtype=bool)
    assert rv.reverse_weights_multiplier(base, 1, empty, 3.0)[0] == pytest.approx(0.25)
    # 全部触发 → 等权（m 对纯信号组合无影响）
    allw = np.ones((2, 4), dtype=bool)
    w_all = rv.reverse_weights_multiplier(base, 1, allw, 3.0)
    assert all(v == pytest.approx(0.25) for v in w_all.values())


# ------------------------------------------------------------------ 2. 占比权重
def test_reverse_weights_share():
    window = np.zeros((2, 4), dtype=bool)
    window[1, 0] = True                     # 只有 code0 触发
    base = [0, 1, 2, 3]
    w = rv.reverse_weights_share(base, 1, window, 0.5)
    assert w[0] == pytest.approx(0.5)                  # 信号股合计 50%
    assert w[1] == pytest.approx(0.5 / 3)              # 非信号 3 只均分 50%
    assert sum(w.values()) == pytest.approx(1.0)
    # 无信号 / 全信号 → 基线等权
    empty = np.zeros((2, 4), dtype=bool)
    assert rv.reverse_weights_share(base, 1, empty, 0.3)[0] == pytest.approx(0.25)
    allw = np.ones((2, 4), dtype=bool)
    assert rv.reverse_weights_share(base, 1, allw, 0.5)[0] == pytest.approx(0.25)


# ------------------------------------------------------------------ 3. 四关判定
def test_evaluate_gates_logic():
    pit_ok = {"pass": True}
    primary_pass = {"excess_oos": {"ann_excess_pct": 3.0, "t": 2.4},
                    "excess_is": {"ann_excess_pct": 1.0, "t": 1.2}}
    cost_rows = [{"cost_bp_oneway": 30, "excess_ann_pct": 2.5, "t": 2.1},
                 {"cost_bp_oneway": 50, "excess_ann_pct": 0.5, "t": 0.4}]
    cap_ok = {"aum_cap_yi": 50.0}
    g = rv.evaluate_gates(primary_pass, cost_rows, cap_ok, pit_ok, 3, 4)
    assert g["OOS"]["pass"] and g["YEARLY"]["pass"] and g["COST"]["pass"] and g["CAPACITY"]["pass"]
    assert g["hypothesis_pass"] is True

    # OOS t 不足 → 不通过
    weak = {"excess_oos": {"ann_excess_pct": 0.5, "t": 1.0},
            "excess_is": {"ann_excess_pct": -1.0, "t": -0.5}}
    g2 = rv.evaluate_gates(weak, cost_rows, cap_ok, pit_ok, 2, 4)
    assert g2["OOS"]["pass"] is False and g2["YEARLY"]["pass"] is False
    assert g2["hypothesis_pass"] is False
    # 成本 50bp 转负 → COST 关不通过
    cost_bad = [{"cost_bp_oneway": 30, "excess_ann_pct": 2.5, "t": 2.1},
                {"cost_bp_oneway": 50, "excess_ann_pct": -0.2, "t": -0.1}]
    g3 = rv.evaluate_gates(primary_pass, cost_bad, cap_ok, pit_ok, 4, 4)
    assert g3["COST"]["pass"] is False and g3["hypothesis_pass"] is False
    # 容量不足
    g4 = rv.evaluate_gates(primary_pass, cost_rows, {"aum_cap_yi": 2.0}, pit_ok, 4, 4)
    assert g4["CAPACITY"]["pass"] is False and g4["hypothesis_pass"] is False


# ------------------------------------------------------------------ 4. 容量
def test_capacity_reverse_formula():
    panel, circ_mv, meta = make_panel(n_dates=40, n_codes=20, seed=4)
    # 构造 picks 与超配计划：基线 20 只中 2 只信号，m=2 → 每只信号额外权重 0.05
    i = 25
    base = list(range(20))
    masks = {"picks": {i: base}}
    window = np.zeros((len(panel.dates), 20), dtype=bool)
    window[i, 0] = window[i, 1] = True
    weights = rv.reverse_weights_multiplier(base, i, window, 2.0)
    schedule = [{"decision_i": i, "exec_i": i + 1, "weights": weights}]
    cap = rv.capacity_reverse(panel, masks, window, schedule, participation=0.10)
    assert cap["aum_cap_yi"] is not None and cap["aum_cap_yi"] > 0
    # 每只超配股票额外权重 = 2/18 - 1/20 = 0.0611...
    extra = 2.0 / (18 + 2 * 2.0) - 1.0 / 20.0
    assert cap["median_extra_weight_pct"] == pytest.approx(extra * 100, rel=0.05)
    # AUM 上限 ≈ 0.1×ADV/extra；ADV20 在 40 天面板上有限值
    assert cap["overweight_positions"] == 2


# ------------------------------------------------------------------ 5. PIT 静态检查
def test_pit_static_check():
    res = rv.pit_static_check(MODULE)
    assert res["pass"] is True and res["negative_shifts"] == []
    assert "signal_pit" in res and "weights_pit" in res


# ------------------------------------------------------------------ 6. 全流程
def test_run_payload_schema_and_markdown(monkeypatch):
    panel, circ_mv, meta = make_panel()
    monkeypatch.setattr(ov.rav, "load_circ_mv", lambda *a, **k: circ_mv)
    monkeypatch.setattr(ov, "load_names_meta", lambda *a, **k: meta)
    payload = rv.run(start=str(panel.dates[30]), end=str(panel.dates[-2]),
                     multipliers=(1.5, 2.0), shares=(0.3,), panel=panel, validate=False)
    assert set(payload) == {"meta", "variants", "exposure", "gates", "registry_entry", "conclusions"}
    assert set(payload["variants"]) >= {"BASELINE", "M1.5", "M2", "K30"}
    primary = payload["variants"]["M2"]
    for key in ("overall", "by_year", "excess", "excess_is", "excess_oos", "excess_by_year",
                "excess_bear_2026", "excess_gross_ann_pct"):
        assert key in primary, key
    for gate in ("PIT", "OOS", "YEARLY", "COST", "CAPACITY"):
        assert gate in payload["gates"] and "pass" in payload["gates"][gate]
    entry = payload["registry_entry"]
    assert entry["library_slot"] == "S05"
    assert entry["validation_result"] in ("REVERSE_HYPOTHESIS_PASS", "REVERSE_HYPOTHESIS_NOT_PASS")
    assert "data_snooping_warning" in entry and entry["missing_before_shadow"]
    assert payload["conclusions"]["bullets"]
    json.loads(json.dumps(payload, ensure_ascii=False))
    md = rv.render_markdown(payload)
    assert "四关" in md and "registry" in md and "结论" in md
