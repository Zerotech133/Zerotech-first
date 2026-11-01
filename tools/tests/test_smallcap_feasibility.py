# -*- coding: utf-8 -*-
"""tools/tests/test_smallcap_feasibility.py — 「小市值反转」可行性（task-92）单元测试。

覆盖（≥4）：
  1. 容量计算：AUM_cap = participation × ADTV × n_hold，剔除极端流动性用 p10；
  2. 成本分层：30/50/80bp 净年化单调递减、公式正确、long-only 单腿；
  3. 多头超额年化：20 日超额 → 年化（×252/20），缺失容错；
  4. go/no-go 判定逻辑：四条门槛全过才 go；容量/净超额/替代路径任一不过 → no-go；
  5. 报告 schema：真实产物的字段完整性（含 decision/cost/capacity/margin）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_smallcap_feasibility.py
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
import sys

sys.path.insert(0, str(ROOT / "tools" / "research"))
import smallcap_reversal_feasibility as scf  # noqa: E402

REPORT = ROOT / "research" / "reports" / "smallcap_reversal_feasibility_20261101.json"


# ------------------------------------------------------------------ 1. 容量
def test_capacity_estimate():
    adtv = np.array([1e8, 2e8, 3e8, 4e8, 10e8])  # 元
    out = scf.capacity_estimate(adtv, n_hold=100, participations=(0.10, 0.20))
    med = float(np.median(adtv))                 # 3e8
    p10 = float(np.percentile(adtv, 10))
    assert out["aum_cap_10pct_yi"] == pytest.approx(0.10 * med * 100 / 1e8, abs=0.01)
    assert out["aum_cap_20pct_yi"] == pytest.approx(0.20 * med * 100 / 1e8, abs=0.01)
    assert out["aum_cap_10pct_ex_lowliq_yi"] == pytest.approx(0.10 * p10 * 100 / 1e8, abs=0.01)
    assert out["aum_cap_10pct_ex_lowliq_yi"] <= out["aum_cap_10pct_yi"]
    # 空输入容错
    assert scf.capacity_estimate(np.array([]), 100)["n_names"] == 0
    assert scf.capacity_estimate(np.array([np.nan]), 100)["n_names"] == 0


# ------------------------------------------------------------------ 2. 成本分层
def test_cost_layers():
    gross, turn = 10.0, 2.0
    costs = scf.cost_layers(gross, turn, costs_bp=(30, 50, 80), legs=1)
    assert costs["30bp"]["net_ann_pct"] == pytest.approx(
        gross - 30 / 10000 * 100 * turn, abs=0.01)
    assert costs["30bp"]["net_ann_pct"] > costs["50bp"]["net_ann_pct"] > costs["80bp"]["net_ann_pct"]
    # long-only 单腿：legs=2 时成本翻倍
    two = scf.cost_layers(gross, turn, costs_bp=(30,), legs=2)
    assert two["30bp"]["cost_drag_ann_pct"] == pytest.approx(
        2 * costs["30bp"]["cost_drag_ann_pct"], abs=0.01)
    # 缺失容错
    assert scf.cost_layers(None, 2.0)["30bp"]["net_ann_pct"] is None


# ------------------------------------------------------------------ 3. 年化
def test_annualize():
    assert scf.annualize(1.0) == pytest.approx(1.0 * 252 / 20)
    assert scf.annualize(None) is None
    assert scf.annualize(float("nan")) is None


# ------------------------------------------------------------------ 4. go/no-go 逻辑
def _ll(net30=2.0, excess=0.8, cap10=10.0):
    return {"excess_20d_pct": excess,
            "cost": {"30bp": {"net_ann_pct": net30}},
            "capacity": {"aum_cap_10pct_ex_lowliq_yi": cap10}}


def _alt(net30=2.0, obs=0.5, val=0.4, name="P3"):
    return {name: {"cost": {"30bp": {"net_ann_pct": net30}},
                   "walk_forward": {"2023-2024": {"mean_20d_pct": obs},
                                    "2025-2026": {"mean_20d_pct": val}}}}


def test_go_no_go_logic():
    margin = {"short_side_feasible": False, "reason": "券源稀缺"}
    ok = scf.decide_go_no_go(_ll(), _alt(), margin)
    assert ok["decision"] == "go" and ok["best_alt_path"] == "P3"
    assert all(ok["criteria_pass"].values())
    # A 不过：净超额低
    assert scf.decide_go_no_go(_ll(net30=0.2), _alt(), margin)["decision"] == "no-go"
    # A 不过：长腿超额低
    assert scf.decide_go_no_go(_ll(excess=0.1), _alt(), margin)["decision"] == "no-go"
    # B 不过：容量不足
    assert scf.decide_go_no_go(_ll(cap10=1.0), _alt(), margin)["decision"] == "no-go"
    # C 不过：替代路径 WF 不稳定
    assert scf.decide_go_no_go(_ll(), _alt(val=-0.1), margin)["decision"] == "no-go"
    # C 不过：替代路径净超额为负
    assert scf.decide_go_no_go(_ll(), _alt(net30=-1.0), margin)["decision"] == "no-go"


# ------------------------------------------------------------------ 5. 报告 schema
def test_report_schema():
    assert REPORT.exists(), f"报告缺失: {REPORT}"
    rep = json.loads(REPORT.read_text(encoding="utf-8"))
    assert rep.get("schema_check") == [], rep.get("schema_check")
    assert scf.validate_report_schema(rep) == []
    assert rep["decision"]["decision"] in ("go", "no-go")
    ll = rep["long_leg"]
    for bp in ("30bp", "50bp", "80bp"):
        assert bp in ll["cost"] and "net_ann_pct" in ll["cost"][bp]
    assert {"aum_cap_10pct_ex_lowliq_yi", "aum_cap_20pct_ex_lowliq_yi"} <= set(ll["capacity"])
    assert rep["margin_constraint"]  # 券源约束必须给出（含假设）
    assert rep["alt_paths"], "至少要有替代路径"
    for name, r in rep["alt_paths"].items():
        assert "excess_20d_pct" in r and "walk_forward" in r
    # 坏报告报错
    broken = json.loads(json.dumps(rep))
    del broken["long_leg"]["cost"]
    assert scf.validate_report_schema(broken)
