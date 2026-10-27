# -*- coding: utf-8 -*-
"""荀攸 T1/T2 分档逻辑单测（2026-10-19 拍板口径，全部 mock 数据，禁读真实池文件）。

覆盖：断层分界 / 封顶旋钮 / 分歧票不进档不占额度 / build_pool 集成 /
汇报信分层陈述段 / 导出脚本对新旧池文件的兼容。
"""
import importlib.util
import json
import os

from strategy_layer import xunyou
from strategy_layer.xunyou import (
    T1_RANK_CAP,
    T1_SCORE_MIN,
    assign_tiers,
    build_pool,
)

DAY = "20261019"


def row(symbol, score, divergence=False):
    return {"trade_date": DAY, "symbol": symbol, "action": "buy",
            "score": score, "confidence": 0.8, "divergence": divergence,
            "rationale": "r-" + symbol, "contributions": {}}


def by_symbol(rows):
    return {r["symbol"]: r for r in rows}


# ── 断层分界（方案一双断点：T1>99.0 开区间；T2=[95, 99.0] 闭区间）──────
def test_t1_boundary_is_strictly_above_99():
    rows = [row("A", 99.01), row("B", T1_SCORE_MIN)]  # 99.0 本身不入 T1
    tiers = by_symbol(assign_tiers(rows))
    assert tiers["A"]["tier"] == "T1"
    assert tiers["B"]["tier"] == "T2"


def test_t2_boundary_closed_at_95_and_99():
    rows = [row("A", 95.0), row("B", 94.99), row("C", 99.0),
            row("D", 97.0)]
    tiers = by_symbol(assign_tiers(rows))
    assert tiers["A"]["tier"] == "T2"
    assert tiers["B"]["tier"] == ""  # 95 以下不入档
    assert tiers["C"]["tier"] == "T2"  # 上沿与 T1 下沿衔接（≤99.0）
    assert tiers["D"]["tier"] == "T2"


def test_low_scores_are_untiered():
    tiers = by_symbol(assign_tiers([row("A", 60.0), row("B", 87.0)]))
    assert tiers["A"]["tier"] == ""
    assert tiers["B"]["tier"] == ""


# ── 封顶旋钮（T1_RANK_CAP：>99.0 且排名≤20；超封顶不入档、不落 T2）────
def test_t1_rank_cap_limits_alert_volume():
    rows = [row("S%02d" % i, 99.5 - i * 0.01) for i in range(25)]
    tiers = by_symbol(assign_tiers(rows))
    t1 = [s for s, r in tiers.items() if r["tier"] == "T1"]
    assert len(t1) == T1_RANK_CAP
    assert "S19" in t1 and "S20" not in t1  # 排名 21 起被封顶裁掉


def test_over_cap_scores_do_not_leak_into_t2():
    rows = [row("S%02d" % i, 99.5) for i in range(T1_RANK_CAP + 3)]
    tiers = by_symbol(assign_tiers(rows))
    assert sum(1 for r in tiers.values() if r["tier"] == "T1") == T1_RANK_CAP
    assert all(r["tier"] == "" for r in tiers.values()
               if r["symbol"] >= "S20")  # 超封顶 = 不入档，绝不落 T2


def test_rank_cap_counts_from_top_by_score_desc():
    rows = ([row("TOP", 100.0)]
            + [row("F%02d" % i, 99.5) for i in range(19)]
            + [row("EDGE", 99.1)])
    tiers = by_symbol(assign_tiers(rows))
    assert tiers["TOP"]["tier"] == "T1"
    assert tiers["EDGE"]["tier"] == ""  # 排名 21，score>99.0 仍被封顶裁掉


# ── 分歧票：一律人工复核、不进任何档、不占 T1 排名额度 ────────────────
def test_divergence_forced_review_and_untiered():
    tiers = by_symbol(assign_tiers([row("DIV", 100.0, divergence=True)]))
    assert tiers["DIV"]["tier"] == ""
    assert tiers["DIV"]["review_required"] is True


def test_divergence_does_not_consume_t1_rank_quota():
    rows = ([row("DIV", 100.0, divergence=True)]
            + [row("S%02d" % i, 99.5) for i in range(T1_RANK_CAP)])
    tiers = by_symbol(assign_tiers(rows))
    assert tiers["DIV"]["tier"] == ""
    assert tiers["DIV"]["review_required"] is True
    t1 = [s for s, r in tiers.items() if r["tier"] == "T1"]
    assert len(t1) == T1_RANK_CAP  # 分歧票旁路后 20 个非分歧票全数入 T1


def test_divergence_string_true_also_bypassed():
    r = row("DIV", 100.0)
    r["divergence"] = "true"  # 兼容字符串口径（与导出脚本 is_divergent 一致）
    tiers = by_symbol(assign_tiers([r]))
    assert tiers["DIV"]["tier"] == ""
    assert tiers["DIV"]["review_required"] is True


# ── build_pool 集成：池条目带 tier、stats 计数、notes 拍板口径 ─────────
def test_build_pool_annotates_tiers_and_stats():
    combined = [row("T1A", 99.9), row("T2A", 97.0),
                row("LOW", 60.0), row("DIV", 100.0, divergence=True)]
    pool = build_pool(DAY, combined, [{"symbol": "DIV"}])
    tiers = by_symbol(pool["pool"])
    assert tiers["T1A"]["tier"] == "T1"
    assert tiers["T2A"]["tier"] == "T2"
    assert tiers["LOW"]["tier"] == ""
    assert tiers["DIV"]["tier"] == ""
    assert tiers["DIV"]["review_required"] is True
    assert pool["stats"]["tier1_total"] == 1
    assert pool["stats"]["tier2_total"] == 1
    assert pool["stats"]["review_total"] == 1
    assert any("2026-10-19" in n and "T1" in n for n in pool["notes"])
    assert all("review_required" not in r for r in pool["pool"]
               if not r["divergence"])  # 复核标记仅分歧票携带
    json.dumps(pool, ensure_ascii=False)  # 可序列化


def test_build_pool_empty_result_has_zero_tiers():
    pool = build_pool(DAY, [], [])
    assert pool["pool"] == []
    assert pool["stats"]["tier1_total"] == 0
    assert pool["stats"]["tier2_total"] == 0
    assert pool["stats"]["review_total"] == 0


# ── 汇报信分层陈述段（落盘至 tmp，绝不写真实 reports/）────────────────
def test_upstream_report_contains_tier_section(tmp_path, monkeypatch):
    monkeypatch.setattr(xunyou, "REPORT_DIR", str(tmp_path))
    pool = build_pool(DAY, [row("T1A", 99.9), row("T2A", 97.0),
                            row("DIV", 63.475, divergence=True)],
                      [{"symbol": "DIV"}])
    path = xunyou.write_upstream_report(DAY, pool, ["kg_boost"])
    text = open(path, encoding="utf-8").read()
    assert "## 分层陈述（2026-10-19 拍板口径）" in text
    assert "T1（告警档" in text and "**1** 条：T1A" in text
    assert "T2（人工关注档" in text and "**1** 条：T2A" in text
    assert "人工复核（分歧票" in text and "**1** 只：DIV" in text
    assert "不直连飞书" in text


# ── 导出脚本：tier 透传 + 旧池文件（无 tier 字段）兼容不崩 ────────────
def _load_export_module():
    here = os.path.dirname(os.path.abspath(__file__))
    script = os.path.abspath(os.path.join(
        here, "..", "..", "..", "portal", "tools",
        "export_xunyou_latest.py"))
    spec = importlib.util.spec_from_file_location("export_xunyou_latest",
                                                  script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pool_doc(items):
    return {"agent": "荀攸（公达）", "trade_date": DAY,
            "generated_at": "2026-10-19 17:50:40",
            "scope": "RESEARCH_ONLY_NOT_PRODUCTION",
            "cutover_verdict": "BLOCK_CUTOVER", "pool": items,
            "stats": {"divergence_total": 0}}


def test_export_passthrough_tier_for_new_pool_files():
    mod = _load_export_module()
    doc = _pool_doc([
        {"symbol": "600519.SH", "score": 99.9, "confidence": 0.8,
         "divergence": False, "rationale": "r1", "tier": "T1"},
        {"symbol": "000002.SZ", "score": 97.0, "confidence": 0.7,
         "divergence": False, "rationale": "r2", "tier": "T2"}])
    payload = mod.build_payload("fake.json", doc)
    assert [t["tier"] for t in payload["top"]] == ["T1", "T2"]


def test_export_old_pool_files_without_tier_do_not_crash():
    mod = _load_export_module()
    doc = _pool_doc([
        {"symbol": "600519.SH", "score": 99.9, "confidence": 0.8,
         "divergence": False, "rationale": "r1"},  # 旧文件：无 tier 字段
        {"symbol": "000002.SZ", "score": 97.0, "confidence": 0.7,
         "divergence": False, "rationale": "r2"}])
    payload = mod.build_payload("fake.json", doc)
    assert all(t["tier"] == "" for t in payload["top"])  # 兼容为空串
