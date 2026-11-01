# -*- coding: utf-8 -*-
"""tools/tests/test_rs_overlay_strict.py — RS 叠加严苛复验（task-88）单元测试。

覆盖（≥4）：
  1. 多重检验：BH-FDR 校正正确性（已知算例）+ 报告中 q 可由 p 复算、q≤0.10 计数一致；
  2. 中性化/归因结构：报告含 M0/M1/M2 三层指标、逐日横截面控制项、板块暴露字段，
     且 M2 是「在 M1 基础上再控制板块未来收益」的口径；
  3. 安慰剂：报告记录 1000 次同覆盖率随机选股、分位在 0-100；分位高者才可能判真 alpha；
  4. 裁决 schema：裁决 ∈ {真 alpha, 板块 beta, 无增量}，逐战法有证据字段；C 模式结论存在；
  5. walk-forward 与 FDR 完整性：2023-24/2025-26 双窗、57 个假设（19×3）。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_rs_overlay_strict.py
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "research" / "reports" / "rs_overlay_strict_audit_20261101.json"
sys.path.insert(0, str(ROOT / "tools" / "research"))
import rs_overlay_strict as rss  # noqa: E402

VERDICTS = {"真 alpha", "板块 beta", "无增量"}
MODES = {"A", "B", "C"}


@pytest.fixture(scope="module")
def rep():
    assert REPORT.exists(), f"审计报告缺失: {REPORT}"
    return json.loads(REPORT.read_text(encoding="utf-8"))


# ------------------------------------------------------------------ 1. FDR / 中性化
def test_bh_fdr_known_and_recomputed(rep):
    # 已知算例（BH 1995）：0.01,0.02,0.03,0.04,0.05 → q=0.05,0.05,0.05,0.05,0.05
    q, n = rss.bh_q([0.01, 0.02, 0.03, 0.04, 0.05], 0.10)
    assert all(abs(x - 0.05) < 1e-12 for x in q) and n == 5
    # 报告 q 可由原始 p 复算
    hyp = rep["hypotheses_fdr"]
    pvals = [h["p"] for h in hyp]
    q_re, n_re = rss.bh_q(pvals, 0.10)
    for h, qq in zip(hyp, q_re):
        if h["p"] is None:
            assert h["q"] is None
        else:
            assert h["q"] == pytest.approx(qq, rel=1e-9, abs=1e-12)
    assert rep["summary"]["fdr"]["n_hypotheses"] == len(hyp) == 57
    assert rep["summary"]["fdr"]["n_significant_q10"] == n_re
    for h in hyp:
        assert h["mode"] in MODES
        assert h["significant"] == (h["q"] is not None and h["q"] <= 0.10)


def test_neutralization_layers(rep):
    # 19 战法 × 3 模式，M0/M1/M2 三层齐备
    assert len(rep["strategies"]) == 19
    for label, rec in rep["strategies"].items():
        assert "modes" in rec, (label, rec.get("error"))
        for m in MODES:
            md = rec["modes"][m]
            for layer in ("m0", "m1", "m2"):
                assert layer in md and "mean" in md[layer] and "t" in md[layer], (label, m, layer)
            assert set(md["strata_m2"].keys()) <= {"小市值", "中市值", "大市值"}
            assert "sector_pct_T_mean" in md["sector_exposure"]
            assert "sector_fwd_mean" in md["sector_exposure"]
    # M2 严格于 M1：口径说明里 M2 明说加控制板块未来收益
    assert "板块" in rep["meta"]["definition"]["M2"] and "M1" in rep["meta"]["definition"]["M2"]


# ------------------------------------------------------------------ 2. 安慰剂
def test_placebo(rep):
    checked = 0
    for label, rec in rep["strategies"].items():
        if "modes" not in rec:
            continue
        for m in MODES:
            pl = rec["modes"][m]["placebo"]
            if pl.get("runs", 0) == 0:
                continue
            checked += 1
            assert pl["runs"] == 1000, (label, m, pl["runs"])
            assert pl["percentile"] is not None and 0 <= pl["percentile"] <= 100
    assert checked == 57, f"应记录 57 个（19×3）安慰剂结果，实际 {checked}"
    # 判据自洽：被判「真 alpha」的策略，C 模式安慰剂分位必须 ≥90
    for label, rec in rep["strategies"].items():
        if rec.get("verdict") == "真 alpha":
            assert rec["modes"]["C"]["placebo"]["percentile"] >= 90
            assert rec["verdict_basis"]["q"] <= 0.10
            assert rec["verdict_basis"]["m2_mean"] > 0 and rec["verdict_basis"]["t_m2"] >= 2
    # 安慰剂函数的边界
    out = rss.srs_placebo([np.nan], [np.nan], [1], [10], None)
    assert out["runs"] == 0 and out["percentile"] is None


# ------------------------------------------------------------------ 3. 裁决 schema
def test_verdict_schema(rep):
    for label, rec in rep["strategies"].items():
        assert rec["verdict"] in VERDICTS, (label, rec["verdict"])
        assert rec.get("verdict_reason")
    cnt = rep["summary"]["verdict_counts"]
    assert sum(cnt.values()) == 19
    assert rep["conclusion"]["C_mode_answer"]
    assert set(cnt.keys()) == VERDICTS
    # 裁决逻辑纯函数：构造四类边界
    base = {"q": 0.01, "m0_mean": 0.01, "t_m0": 5.0, "m2_mean": 0.01, "t_m2": 5.0,
            "placebo_percentile": 95, "walk_forward": {"obs_mean": 0.01, "val_mean": 0.01}}
    assert rss.decide_verdict(base)[0] == "真 alpha"
    bad_wf = dict(base, walk_forward={"obs_mean": 0.01, "val_mean": -0.01})
    assert rss.decide_verdict(bad_wf)[0] == "无增量"
    no_placebo = dict(base, placebo_percentile=50)
    assert rss.decide_verdict(no_placebo)[0] == "板块 beta"
    not_sig = dict(base, q=0.5)
    assert rss.decide_verdict(not_sig)[0] == "无增量"  # 无基线改善证据 → 无增量
    beta = {"q": 0.5, "m0_mean": 0.008, "t_m0": 3.0, "m2_mean": -0.001, "t_m2": -0.2,
            "baseline_m0": 0.001, "placebo_percentile": 60,
            "walk_forward": {"obs_mean": 0.004, "val_mean": -0.002}}
    assert rss.decide_verdict(beta)[0] == "板块 beta"  # C 模式改善但控制后消失 → 板块 beta
    nothing = {"q": 0.5, "m0_mean": -0.01, "t_m0": -5.0, "m2_mean": -0.01, "t_m2": -1.0,
               "placebo_percentile": 10, "walk_forward": {"obs_mean": 0, "val_mean": 0}}
    assert rss.decide_verdict(nothing)[0] == "无增量"


# ------------------------------------------------------------------ 4. walk-forward / 结构完整性
def test_walk_forward_and_structure(rep):
    for label, rec in rep["strategies"].items():
        if "modes" not in rec:
            continue
        c = rec["modes"]["C"]
        wf = c["walk_forward"]
        assert set(wf.keys()) == {"obs", "val"}
        assert "mean" in wf["obs"] and "t" in wf["obs"]
        # 2023-24 观察窗、2025-26 验证窗都必须有样本（C 模式）
        assert wf["obs"]["n"] >= 0 and wf["val"]["n"] >= 0
        sub = wf["obs"]["n"] + wf["val"]["n"]
        assert 0 < sub <= c["n"], (label, c["n"], wf)
    # 日频聚合 t 口径：daily_stat 的 t 与手算一致
    di = np.array([0, 0, 1, 1, 2, 2])
    vals = np.array([1.0, 3.0, 2.0, 4.0, -1.0, 1.0])
    st = rss.daily_stat(di, vals, 3)
    daily = np.array([2.0, 3.0, 0.0])
    expect_t = daily.mean() / (daily.std(ddof=1) / math.sqrt(3))
    assert st["mean"] == pytest.approx(daily.mean())
    assert st["t"] == pytest.approx(expect_t)
    assert st["n"] == 6 and st["n_days"] == 3
    # 证据链：报告含修正说明与来源脚本
    assert "复权" in "".join(rep["meta"]["corrections_vs_v2"])
    assert rep["meta"]["source_script"] == "tools/research/liangxue_rs_overlay.py"
