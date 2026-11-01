# -*- coding: utf-8 -*-
"""tools/tests/test_simahui_strict_audit.py — 司马微 G1-G8 严苛调校审计（产物级测试）。

不重跑回测，独立复核审计产物（research/reports/simahui_strict_audit_20261101.json +
agent_platform/catalogs/strategy_registry.json）的计算与结构：
  1. 多重检验：从报告里的原始 p 重算 BH-FDR q，与报告 q 一致；显著正向结论可复现；
  2. 成本敏感性：由报告 cost_sensitivity 的 gross/cost@30bp 独立重算 50/80bp 净超额，
     并校验 G3 判据（50bp 净≤0 → fail）；
  3. 安慰剂：G4 只有在「同覆盖率随机信号 ≥1000 次置换」时才可 pass；本审计无 G4 pass；
  4. 决策 schema：keep/close/needs-more 合法、G1-G8 齐全、注册表 book_backlog/S01-S05
     strict_verdict 与审计报告一致、证据路径存在；
  5. keep=0 与逐项裁决一致性。

运行：C:\\项目\\py312\\python.exe -X utf8 -m pytest -q tools/tests/test_simahui_strict_audit.py
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
AUDIT = ROOT / "research" / "reports" / "simahui_strict_audit_20261101.json"
REG = ROOT / "agent_platform" / "catalogs" / "strategy_registry.json"
VALID_DECISIONS = {"keep", "close", "needs-more"}
GATES = [f"G{i}" for i in range(1, 9)]


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def audit():
    assert AUDIT.exists(), f"审计报告缺失: {AUDIT}"
    return _load(AUDIT)


@pytest.fixture(scope="module")
def registry():
    return _load(REG)


def _bh_q(pvals):
    items = [(i, p) for i, p in enumerate(pvals) if p is not None]
    n = len(items)
    q = [None] * len(pvals)
    items.sort(key=lambda x: x[1])
    prev = 1.0
    for rank in range(n, 0, -1):
        i, p = items[rank - 1]
        prev = min(prev, p * n / rank)
        q[i] = prev
    return q


# ------------------------------------------------------------------ 1. G1
def test_multiple_testing_bh_fdr(audit):
    g1 = audit["G1_multiple_testing"]
    hyp = g1["hypotheses"]
    assert len(hyp) == g1["n_hypotheses"] >= 26, "假设池应覆盖 26 个已测假设"
    q_recalc = _bh_q([h["p"] for h in hyp])
    for h, q in zip(hyp, q_recalc):
        if h["p"] is None:
            assert h["q"] is None
            continue
        assert h["q"] == pytest.approx(q, rel=1e-12, abs=1e-15), h["id"]
        assert 0.0 <= h["q"] <= 1.0
    # 显著正向结论必须能由 p/q/t 复现（q≤0.10 且 t>0）
    expected = {h["id"] for h in hyp if h["q"] is not None and h["q"] <= 0.10
                and h["t"] is not None and h["t"] > 0}
    got = {f["id"] for f in g1["significant_positive_findings"]}
    assert got == expected == {"LX20_BULL_STOCK_THREE_ABSOLUTES",
                               "LX06_YIN_LINE_TACTICS",
                               "LX22_CONCAVE_NOTCH_GOLD"}, \
        "校正后显著为正的应只有三个反向规避（LX20/LX06/LX22）"
    assert g1["n_q_le_0_10"] >= len(got)
    # 做多量学形态应全部不显著为正
    assert not any(f["id"].startswith(("H2", "LX03", "LX04", "LX05", "LX10", "LX11",
                                       "LX14", "LX17", "LX19"))
                   for f in g1["significant_positive_findings"])


# ------------------------------------------------------------------ 2. G3 成本
def test_cost_sensitivity_g3(audit):
    checked = 0
    for sid, s in audit["strategies"].items():
        cs = s.get("cost_sensitivity") or {}
        if "30bp" in cs:                      # 反向规避：{30bp,50bp,80bp}
            by_bp = cs
        else:                                 # S05: {"rows": [...]} 列表口径，跳过
            continue
        checked += 1
        gross = by_bp["30bp"]["gross_ann_pct"]
        cost30 = by_bp["30bp"]["cost_ann_pct"]
        for bp in (30, 50, 80):
            row = by_bp[f"{bp}bp"]
            assert row["net_ann_pct"] == pytest.approx(
                gross - cost30 * bp / 30.0, abs=1e-3), (sid, bp)
        net50 = by_bp["50bp"]["net_ann_pct"]
        g3 = {g["gate"]: g["status"] for g in s["gates"]}["G3"]
        assert g3 == ("pass" if net50 > 0 else "fail"), (sid, net50, g3)
        # 80bp 净超额应不高于 50bp
        assert by_bp["80bp"]["net_ann_pct"] <= by_bp["50bp"]["net_ann_pct"]
    assert checked == 4, "应复核 LX20/LX06/LX22/LX12 四个反向规避的成本敏感性"
    # 关键裁决：LX20 50bp 净超额已转负 → G3 fail → 不可交易（needs-more 的前提）
    lx20 = audit["strategies"]["LX20_BULL_STOCK_THREE_ABSOLUTES"]
    assert lx20["cost_sensitivity"]["50bp"]["net_ann_pct"] < 0
    assert {g["gate"]: g["status"] for g in lx20["gates"]}["G3"] == "fail"
    # LX22/LX12 30bp 就为负 → close
    for sid in ("LX22_CONCAVE_NOTCH_GOLD", "LX12_DOUBLE_YIN_EXIT"):
        s = audit["strategies"][sid]
        assert s["cost_sensitivity"]["30bp"]["net_ann_pct"] <= 0
        assert s["strict_verdict"] == "close"


# ------------------------------------------------------------------ 3. G4 安慰剂
def test_placebo_gate(audit):
    any_g4_pass = False
    for sid, s in audit["strategies"].items():
        p = s.get("placebo")
        assert p is not None, f"{sid} 缺少 placebo 字段"
        g4 = {g["gate"]: g["status"] for g in s["gates"]}["G4"]
        if p["meets_g4"]:
            assert p["runs"] >= 1000, "G4 pass 必须 ≥1000 次同覆盖率置换"
            assert p["percentile"] is not None and 0 <= p["percentile"] <= 100
            any_g4_pass = True
        else:
            assert g4 in ("fail", "not-met", "partial"), (sid, g4)
        if p.get("percentile") is not None:
            assert 0 <= p["percentile"] <= 100
    # 本次审计：无人做过 1000 次置换 → 不应有 G4 pass
    assert not any_g4_pass
    assert all(not s["placebo"]["meets_g4"] for s in audit["strategies"].values())


# ------------------------------------------------------------------ 4/5. 决策 schema + 注册表
def test_decision_schema_and_registry_sync(audit, registry):
    strategies = audit["strategies"]
    assert strategies, "strategies 不能为空"
    for sid, s in strategies.items():
        assert s["strict_verdict"] in VALID_DECISIONS, (sid, s["strict_verdict"])
        gids = [g["gate"] for g in s["gates"]]
        assert gids == GATES, (sid, gids)
        for g in s["gates"]:
            assert g["status"] in {"pass", "fail", "partial", "not-met", "na", "done"}
            assert g["evidence"], f"{sid} {g['gate']} 缺证据"
        assert s.get("rationale"), f"{sid} 缺 rationale"
    # keep=0：没有策略通过全部 G1-G8
    assert audit["verdicts_summary"]["keep"] == []
    # 与注册表同步：S05 及 book_backlog 每项
    reg_s = {s["id"]: s for s in registry["strategies"]}
    for sid, s in strategies.items():
        if sid in reg_s:
            assert reg_s[sid]["strict_verdict"]["verdict"] == s["strict_verdict"], sid
    s05 = reg_s["S05_LX20_AVOID_DEWEIGHT"]
    assert s05["strict_verdict"]["verdict"] == "close"
    for k, v in reg_s.items():
        if k[:3] in ("S01", "S02", "S03", "S04", "S05"):
            assert v.get("strict_verdict", {}).get("verdict") in VALID_DECISIONS, k
    items = registry["book_backlog"]["items"]
    assert len(items) == 24
    for it in items:
        sv = it.get("strict_verdict") or {}
        assert sv.get("verdict") in VALID_DECISIONS, it.get("strategy_spec_id")
        assert sv.get("rationale") and sv.get("evidence")
    assert registry["strict_audit_20261101"]["report"] == \
        "research/reports/simahui_strict_audit_20261101.json"


def test_keep_zero_and_consistency(audit, registry):
    vs = audit["verdicts_summary"]
    assert set(vs["keep"]) | set(vs["needs-more"]) | set(vs["close"]) == set(
        audit["strategies"].keys())
    cnt = {}
    for it in registry["book_backlog"]["items"]:
        v = it["strict_verdict"]["verdict"]
        cnt[v] = cnt.get(v, 0) + 1
    assert cnt == audit["book_backlog"]["counts"], (cnt, audit["book_backlog"]["counts"])
    assert cnt.get("keep", 0) == 0
    # 关键裁决方向
    assert audit["strategies"]["LX20_BULL_STOCK_THREE_ABSOLUTES"]["strict_verdict"] == "needs-more"
    assert audit["strategies"]["LX06_YIN_LINE_TACTICS"]["strict_verdict"] == "needs-more"
    assert audit["strategies"]["LX22_CONCAVE_NOTCH_GOLD"]["strict_verdict"] == "close"
    assert audit["strategies"]["LX12_DOUBLE_YIN_EXIT"]["strict_verdict"] == "close"
    # 证据路径存在（相对仓库根）
    for p in audit["evidence_paths"]:
        if p.startswith("deliverables/") or p.startswith("research/") or p.startswith("agent_platform/"):
            assert (ROOT / p).exists(), f"证据路径不存在: {p}"
