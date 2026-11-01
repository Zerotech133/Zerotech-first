# -*- coding: utf-8 -*-
"""同 concept 重复单元审计（concept_dedup）回归测试。

覆盖：
  1) concept 归一化 + 分组（仅重复组、形态合并、单例排除）；
  2) 相似度 / 完整度纯函数；
  3) 判定规则（真重复/近似重复/跨源复用/同书重复论述/概念粒度过粗）；
  4) 审计 schema / 统计一致性 / 主单元选择 / alias id；
  5) 只读审计：--write 不改原 kb_units_all.json + json/md 输出 + CLI；
  6) 空输入容错。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "kb_corpus" / "concept_dedup.py"

_spec = importlib.util.spec_from_file_location("concept_dedup_tool", SCRIPT)
cd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cd)  # type: ignore[union-attr]


def unit(concept: str, book: str, chapter: str = "第1章", p1: int = 1, p2: int = 3,
         claim: str = "默认结论句子。", evidence: str = "默认原文证据。" ) -> dict:
    return {"source": "研报", "book": book, "chapter": chapter, "p1": p1, "p2": p2,
            "concept": concept, "claim": claim, "evidence": evidence}


def make_units() -> list[dict]:
    return [
        # 跨源逐字重复 → 真重复/可合并
        unit("东盟中小微企业（MSME）经济地位", "rp_A", "5.", 5, 17,
             "微、小和中型企业是东盟私营部门的中坚力量，占所有企业的 97% 至 99%。",
             "微、小和中型企业是东盟私营部门的中坚力量，占所有企业的 97% 至 99%。"),
        unit("东盟中小微企业（MSME）经济地位", "rp_B", "5.", 5, 17,
             "微、小和中型企业是东盟私营部门的中坚力量，占所有企业的 97% 至 99%。",
             "微、小和中型企业是东盟私营部门的中坚力量，占所有企业的 97% 至 99%。"),
        # 跨源近似 → near_duplicate/标注
        unit("行业评级定义", "rp_C", "评级", 1, 13, "看好评级意味着预计未来 6~12 个月行业指数强于市场基准指数。"),
        unit("行业评级定义", "rp_D", "评级", 1, 13, "看好评级定义为预计未来 6~12 个月行业指数表现强于市场基准指数（沪深300）。"),
        # 同书不同章节 → repeated_discussion/标注
        unit("量学理论", "伏击涨停(2)", "序言1", 5, 6, "量学理论构建三维互动看盘操盘系统。"),
        unit("量学理论", "伏击涨停(2)", "序言2", 11, 12, "量柱价柱量线是黑马王子独创的投资理论。"),
        # 同书同章同页但不同事实 → concept_too_coarse/标注
        unit("资本市场现存障碍", "rp_E", "13", 13, 13, "非国有企业科技创新债券代表性不足。"),
        unit("资本市场现存障碍", "rp_E", "13", 13, 13, "外国投资者市场持仓占比相对较小。"),
        # 同 concept 归一化差异（括号/连字符）→ 同组
        unit("SNPO (Scale-Up NPO)", "rp_F", "光互连", 1, 8, "SNPO 是全球首款实现光互连的超节点方案。"),
        unit("SNPO (ScaleUp NPO)", "rp_G", "光互连", 1, 8, "SNPO 专为 GPU 服务器 Scale-Up 互连设计。"),
        # 单例（不应出现在重复组）
        unit("黄金柱", "伏击涨停(2)", "第8章", 74, 78, "黄金柱三日不破实顶。"),
        unit("唯一概念XYZ", "rp_H", "1", 1, 1, "唯一结论。"),
    ]


# ---------------------------------------------------------------------------
# 1) 归一化 + 分组
# ---------------------------------------------------------------------------
def test_normalize_and_grouping():
    assert cd.normalize("东盟中小微企业（MSME）经济地位") == cd.normalize("东盟中小微企业(MSME) 经济地位")
    assert cd.normalize("SNPO (Scale-Up NPO)") == cd.normalize("SNPO (ScaleUp NPO)")
    assert cd.normalize("  AI 算力  ") == "ai算力"

    groups = cd.group_units(make_units())
    assert len(groups) == 5, f"应有 5 个重复组，实际 {len(groups)}"
    assert all(len(v) >= 2 for v in groups.values())
    assert cd.normalize("黄金柱") not in groups
    assert cd.normalize("唯一概念XYZ") not in groups
    snpo_key = cd.normalize("SNPO (Scale-Up NPO)")
    assert snpo_key in groups and len(groups[snpo_key]) == 2


# ---------------------------------------------------------------------------
# 2) 相似度 / 完整度
# ---------------------------------------------------------------------------
def test_similarity_and_completeness():
    assert cd.similarity("完全一样的句子。", "完全一样的句子。") == 1.0
    assert cd.similarity("银行不良率下降。", "光伏装机高增长。") < 0.3
    assert 0 <= cd.similarity("看好评级定义", "看好评级定义为未来6个月强于基准") <= 1

    short = unit("A", "b", claim="短。", evidence="短。", p1=1, p2=1)
    rich = unit("A", "b", claim="很长的结论句子，" * 5, evidence="很长的原文证据，" * 6, p1=2, p2=9)
    assert cd.completeness(rich) > cd.completeness(short)


# ---------------------------------------------------------------------------
# 3) 判定规则
# ---------------------------------------------------------------------------
def test_classification_rules():
    def sims(claim_pairs: list[tuple[float, float]]) -> dict:
        return {"claim": {"min": min(p[0] for p in claim_pairs), "max": max(p[0] for p in claim_pairs),
                          "mean": 1.0},
                "evidence": {"min": 0, "max": 1, "mean": 0.5}}

    g_identical = [unit("X", "a"), unit("X", "b")]
    assert cd.classify_group(g_identical, sims([(1.0, 1.0)]))[:2] == ("true_duplicate", "merge")

    g_same_book_diff_chapter = [unit("X", "a", "第1章"), unit("X", "a", "第2章")]
    verdict, action, _ = cd.classify_group(g_same_book_diff_chapter, sims([(0.3, 0.3)]))
    assert (verdict, action) == ("repeated_discussion", "annotate")

    g_same_chapter = [unit("X", "a", "第1章", claim="事实一"), unit("X", "a", "第1章", claim="事实二")]
    verdict, action, _ = cd.classify_group(g_same_chapter, sims([(0.2, 0.2)]))
    assert (verdict, action) == ("concept_too_coarse", "annotate")

    g_same_page_same_claim = [unit("X", "a", "第1章", 1, 3, "同一句。"), unit("X", "a", "第1章", 1, 3, "同一句。")]
    assert cd.classify_group(g_same_page_same_claim, sims([(1.0, 1.0)]))[:2] == ("true_duplicate", "merge")

    g_near = [unit("X", "a"), unit("X", "b")]
    assert cd.classify_group(g_near, sims([(0.8, 0.5)]))[:2] == ("near_duplicate", "annotate")
    assert cd.classify_group(g_near, sims([(0.2, 0.5)]))[:2] == ("cross_source_reuse", "annotate")


# ---------------------------------------------------------------------------
# 4) 审计 schema / 统计一致性 / 主单元 / alias
# ---------------------------------------------------------------------------
def test_audit_schema_and_alias():
    report = cd.audit(make_units(), generated_at="2026-11-01T09:00:00+08:00")
    for key in ("schema_version", "date", "generated_at", "source", "policy", "stats",
                "groups", "alias_map", "recommendations", "disclaimer"):
        assert key in report
    stats = report["stats"]
    assert stats["units"] == len(make_units())
    assert stats["duplicate_groups"] == len(report["groups"]) == 5
    assert stats["units_in_duplicate_groups"] == sum(g["count"] for g in report["groups"])
    assert stats["merge_groups"] == 1, "仅跨源逐字重复组可合并（同书同页不同事实仅标注）"
    assert stats["annotate_groups"] + stats["merge_groups"] == stats["duplicate_groups"]
    assert stats["mergeable_units"] == sum(g["count"] - 1 for g in report["groups"] if g["action"] == "merge")
    assert stats["true_duplicate_groups"] == 1

    group_ids = [g["group_id"] for g in report["groups"]]
    assert group_ids == [f"CG-{i:03d}" for i in range(1, len(group_ids) + 1)]
    for g in report["groups"]:
        assert g["action"] in ("merge", "annotate")
        assert g["primary_unit_id"] in [u["unit_id"] for u in g["units"]]
        assert len(g["alias_ids"]) == g["count"]
        assert all(a.startswith(f"CD-{g['group_id'][3:]}-") for a in g["alias_ids"])
        primary = next(u for u in g["units"] if u["is_primary"])
        assert primary["unit_id"] == g["primary_unit_id"]
        assert primary["completeness"] == max(u["completeness"] for u in g["units"])
        assert "similarity" in g and g["similarity"]["pairwise"]
    # alias_map 只含非主单元，且都指向主单元
    total_non_primary = sum(g["count"] - 1 for g in report["groups"])
    assert len(report["alias_map"]) == total_non_primary
    for alias, info in report["alias_map"].items():
        assert info["alias_of"] and info["group_id"] and info["action"] in ("merge", "annotate")
    # 不修改数据声明
    assert report["policy"]["modifies_unit_data"] is False
    assert "不构成投资建议" in report["disclaimer"]
    assert report["recommendations"]["summary"]


# ---------------------------------------------------------------------------
# 5) 只读审计：--write 输出 json/md 且不改原数据
# ---------------------------------------------------------------------------
def test_run_write_without_modifying_units(tmp_path):
    units_path = tmp_path / "kb_units_all.json"
    payload = {"total": len(make_units()), "generated_at": "2026-11-01T00:00:00+08:00", "units": make_units()}
    units_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    before = units_path.read_text(encoding="utf-8")

    out_dir = tmp_path / "reports"
    report = cd.run(units_path, out_dir=out_dir, write=True)
    assert report["stats"]["duplicate_groups"] == 5
    assert units_path.read_text(encoding="utf-8") == before, "审计不得修改知识单元数据"
    jp = out_dir / "concept_dedup_20261101.json"
    mp = out_dir / "concept_dedup_20261101.md"
    assert jp.is_file() and mp.is_file()
    saved = json.loads(jp.read_text(encoding="utf-8"))
    assert saved["stats"]["duplicate_groups"] == 5
    md = mp.read_text(encoding="utf-8")
    assert "重复组总览" in md and "alias" in md and "不修改知识单元数据" in md

    # CLI --json（默认不写盘）
    proc = subprocess.run([sys.executable, "-X", "utf8", str(SCRIPT), "audit", "--json",
                           "--units", str(units_path)],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    assert proc.returncode == 0, proc.stderr
    cli = json.loads(proc.stdout)
    assert cli["stats"]["duplicate_groups"] == 5


# ---------------------------------------------------------------------------
# 6) 空输入容错
# ---------------------------------------------------------------------------
def test_empty_inputs_tolerated():
    report = cd.audit([])
    assert report["stats"]["units"] == 0
    assert report["stats"]["duplicate_groups"] == 0
    assert report["groups"] == [] and report["alias_map"] == {}
    md = cd.render_markdown(report)
    assert isinstance(md, str) and "重复组" in md
