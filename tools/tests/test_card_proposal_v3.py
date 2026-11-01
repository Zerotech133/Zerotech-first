# -*- coding: utf-8 -*-
"""学习卡第二轮扩卡（card_proposal_v3）回归测试。

覆盖：
  1) 70 张基线重建（cards + 清单 pass36 + 修订 merge_fix14，按 concept 去重）；
  2) 主题覆盖统计（21 主题：覆盖/深度覆盖/知识单元覆盖，前后 delta）；
  3) 候选生成（30-40 张、三角度、去重 vs 基线、可追溯、pending_review、质量门槛）；
  4) schema + portal 静态 JSON + 真实数据未修改；
  5) generate_candidates 纯函数：缺口主题优先 + 每主题上限 + 批内 concept 去重。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "card_proposal_v3.py"
OUT_JSON = REPO / "research" / "reports" / "card_proposals_v3_20261101.json"
OUT_MD = REPO / "research" / "reports" / "card_proposals_v3_20261101.md"
PORTAL_JSON = REPO / "portal" / "static" / "card_proposals_v3.json"
UNITS = REPO / "portal" / "static" / "kb_units_all.json"
CARDS = REPO / "portal" / "static" / "kb_learning_cards.json"
CHECKLIST = REPO / "research" / "reports" / "card_review_checklist_20261101.json"
REVISIONS = REPO / "research" / "reports" / "card_revisions_20261101.json"

_spec = importlib.util.spec_from_file_location("card_proposal_v3_tool", SCRIPT)
cp3 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cp3)  # type: ignore[union-attr]

_payload_cache: dict | None = None


def payload() -> dict:
    global _payload_cache
    if _payload_cache is None:
        _payload_cache = cp3.build(write=True)
    return _payload_cache


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def learn_module():
    return cp3.load_module("cp3_test_learning_analysis", "learning_analysis.py")


# ---------------------------------------------------------------------------
# 1) 基线重建
# ---------------------------------------------------------------------------
def test_baseline_reconstruction():
    learn = learn_module()
    cards = [{"concept": "卡A", "a": "卡A答案", "evidence": "卡A证据"},
             {"concept": "卡A", "a": "重复", "evidence": "重复"}]  # 同 concept 去重
    checklist = [{"concept": "通过卡", "_precheck_verdict": "pass", "a": "答案", "evidence": "证据"},
                 {"concept": "修订卡", "_precheck_verdict": "revise", "a": "答案", "evidence": "证据"}]
    revisions = [{"concept": "修复卡", "final_action": "merge_fix",
                  "original": {"answer": "修复答案", "evidence": "修复证据"}},
                 {"concept": "丢弃卡", "final_action": "drop",
                  "original": {"answer": "x", "evidence": "y"}}]
    base = cp3.build_baseline(cards, checklist, revisions, learn)
    concepts = [i["concept"] for i in base["items"]]
    caps = [i["cap"] for i in base["items"]]
    assert len(concepts) == 3, concepts  # 卡A + 通过卡 + 修复卡
    assert caps.count("cards_file") == 1
    assert "通过卡" in concepts and "修复卡" in concepts
    assert "修订卡" not in concepts and "丢弃卡" not in concepts


# ---------------------------------------------------------------------------
# 2) 覆盖统计
# ---------------------------------------------------------------------------
def test_theme_coverage_metrics():
    learn = learn_module()
    units = [{"concept": "AI 算力", "claim": "AI 算力需求增长", "evidence": "数据"},
             {"concept": "银行", "claim": "银行资产质量", "evidence": "数据"},
             {"concept": "光伏", "claim": "光伏装机", "evidence": "数据"}]
    baseline = {"items": [{"concept": "AI 算力", "cap": "cards_file", "theme": learn.classify_theme(units[0])}]}
    candidates = [{"theme": learn.classify_theme(units[1])}, {"theme": learn.classify_theme(units[2])}]
    cov = cp3.theme_coverage(units, baseline, candidates, learn)
    assert cov["themes_total"] == len({learn.classify_theme(u) for u in units})
    assert cov["cards_before"] == 1 and cov["cards_after"] == 3
    assert 0 <= cov["coverage_rate_before"] <= cov["coverage_rate_after"] <= 1
    assert cov["depth_covered_after"] >= cov["depth_covered_before"]
    assert cov["unit_coverage_after"] > cov["unit_coverage_before"]
    assert sum(r["units"] for r in cov["rows"]) == len(units)


# ---------------------------------------------------------------------------
# 3) 候选生成（30-40 / 去重 / 角度 / 可追溯）
# ---------------------------------------------------------------------------
def test_candidates_generation():
    p = payload()
    cands = p["candidates"]
    assert 30 <= len(cands) <= 40, len(cands)
    ids = [c["card_id"] for c in cands]
    assert len(set(ids)) == len(ids)
    assert all(c["status"] == "pending_review" for c in cands)
    assert all(c["traceable"] for c in cands)
    assert all(c["book"] and c["chapter"] and isinstance(c["page_start"], int) for c in cands)
    assert all(c["evidence"] and len(c["a"]) > 0 for c in cands)
    assert {c["angle_cn"] for c in cands} <= set(cp3.ANGLE_CN.values())
    assert len({c["angle_cn"] for c in cands}) >= 2
    # 与基线 concept 去重 + 批内去重（基线 = cards + 清单 + 修订）
    learn = learn_module()
    cards = json.loads(CARDS.read_text(encoding="utf-8-sig"))["cards"]
    checklist = json.loads(CHECKLIST.read_text(encoding="utf-8-sig"))["entries"]
    revisions = json.loads(REVISIONS.read_text(encoding="utf-8-sig"))["revisions"]
    precheck = json.loads((REPO / "research" / "reports" / "card_precheck_20261101.json").read_text(encoding="utf-8-sig"))
    verdicts = {e["review_id"]: e.get("suggested_verdict") for e in precheck["entries"]}
    for entry in checklist:
        entry["_precheck_verdict"] = verdicts.get(entry.get("review_id"))
    base = cp3.build_baseline(cards, checklist, revisions, learn)
    base_concepts = base["concepts"]
    norm = [cp3.normalize(c["concept"]) for c in cands]
    assert len(set(norm)) == len(norm)
    assert not (set(norm) & base_concepts)
    # 每题≤MAX_PER_THEME
    from collections import Counter
    per_theme = Counter(c["theme"] for c in cands)
    assert all(v <= cp3.MAX_PER_THEME for v in per_theme.values())


# ---------------------------------------------------------------------------
# 4) schema + portal + 数据未修改
# ---------------------------------------------------------------------------
def test_schema_portal_and_no_mutation():
    p = payload()
    for key in ("schema_version", "date", "generated_at", "generator", "status", "baseline",
                "coverage", "groups", "summary", "candidates", "validation", "sources",
                "data_unchanged", "disclaimer"):
        assert key in p
    assert p["status"] == "pending_review"
    assert p["validation"]["ok"] is True, p["validation"]["errors"]
    assert p["data_unchanged"] is True
    for src, path in (("units", UNITS), ("cards", CARDS), ("checklist", CHECKLIST), ("revisions", REVISIONS)):
        assert p["sources"][src]["sha256_before"] == sha(path), src
    cov = p["coverage"]
    assert cov["themes_total"] == 21
    assert cov["covered_after"] == 21
    assert cov["cards_after"] == cov["cards_before"] + len(p["candidates"])
    # 分组一致
    grouped = [cid for g in p["groups"] for cid in g["card_ids"]]
    assert sorted(grouped) == sorted(c["card_id"] for c in p["candidates"])
    # portal 静态 JSON
    portal = json.loads(PORTAL_JSON.read_text(encoding="utf-8-sig"))
    assert portal["status"] == "pending_review"
    assert len(portal["candidates"]) == len(p["candidates"])
    assert portal["summary"]["candidates"] == len(p["candidates"])
    # MD 审核稿
    md = OUT_MD.read_text(encoding="utf-8")
    for marker in ("主题覆盖前后对比", "☐通过", "☐修改", "☐驳回", "pending_review"):
        assert marker in md, marker
    # 真实报告可加载
    disk = json.loads(OUT_JSON.read_text(encoding="utf-8-sig"))
    assert len(disk["candidates"]) == len(p["candidates"])


# ---------------------------------------------------------------------------
# 5) generate_candidates 纯函数：缺口优先 + 主题上限
# ---------------------------------------------------------------------------
def test_generate_candidates_priority_and_cap():
    learn = learn_module()
    units = []
    # 构造 2 个主题的单元（各 5 个），主题 A 已覆盖较多
    for i in range(5):
        units.append({"concept": f"AI算力概念{i}",
                      "claim": f"AI 算力需求增长的第 {i} 条核心结论：机架功率与 GPU 出货显著提升，行业景气度持续上行，数据充分可追溯。",
                      "evidence": f"AI 算力原文证据 {i}：机架功率与 GPU 出货增长显著，数据中心扩容明显，头部厂商资本开支与订单均超预期，产业链景气度持续验证。",
                      "book": "rp_A", "chapter": "第1章", "p1": i + 1, "p2": i + 2, "source": "研报"})
        units.append({"concept": f"新能源汽车概念{i}",
                      "claim": f"新能源车销量增长的第 {i} 条核心结论：渗透率与出口持续改善，电池成本下降，行业需求保持高增，数据充分可追溯。",
                      "evidence": f"新能源车原文证据 {i}：渗透率与出口数据持续改善，电池成本下降，车企订单与交付均实现同比增长，产业链盈利逐步修复。",
                      "book": "rp_B", "chapter": "第2章", "p1": i + 1, "p2": i + 2, "source": "研报"})
    themes = {learn.classify_theme(u) for u in units}
    assert len(themes) >= 2
    theme_a = learn.classify_theme(units[0])
    baseline = {"items": [{"concept": "已有", "cap": "cards_file", "theme": theme_a}],
                "concepts": {cp3.normalize("已有")}}
    cands = cp3.generate_candidates(units, baseline, learn, target_min=5, target_max=8)
    assert 5 <= len(cands) <= 8
    assert all(c["concept"] not in ("已有",) for c in cands)
    from collections import Counter
    assert max(Counter(c["theme"] for c in cands).values()) <= cp3.MAX_PER_THEME
    assert len({c["concept"] for c in cands}) == len(cands)
