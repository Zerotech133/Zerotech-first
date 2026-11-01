# -*- coding: utf-8 -*-
"""124 张学习卡质量复核测试（task-118，含新增 20 张 CP3）。

覆盖：
  1) 报告 schema/统计：124 卡、单元 516、问题/严重度计数自洽；
  2) 新增 20 张 CP3 重点：source 可解析/书名在单元/可追溯/长度/无重复 全通过；对齐 18/20；
  3) 问题清单：2 条 medium（cp3_alignment）且含修复建议与合法 card_index；
  4) source 解析纯函数正反例 + 主题分布合计 124 + 书籍/研报合计 124；
  5) 只读安全：报告 cards/units sha256 == 当前文件；MD 结构完整。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
UNITS = S / "kb_units_all.json"
REPORT_JSON = R / "card_quality_audit_124_20261102.json"
REPORT_MD = R / "card_quality_audit_124_20261102.md"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_quality_audit.py"

_spec = importlib.util.spec_from_file_location("cqa124_test", SCRIPT)
cqa = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = cqa
_spec.loader.exec_module(cqa)  # type: ignore[union-attr]


def load(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8-sig"))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def report() -> dict:
    return load(REPORT_JSON)


# ---------------------------------------------------------------------------
# 1) schema / 统计
# ---------------------------------------------------------------------------
def test_report_schema_and_stats():
    d = report()
    for key in ("schema_version", "date", "generated_at", "mode", "sources", "summary",
                "issues", "cp3_focus", "theme_distribution", "traceable_cards",
                "known_notes", "source_unchanged", "disclaimer"):
        assert key in d, key
    s = d["summary"]
    assert d["date"] == "20261102"
    assert s["cards_total"] == 124 == d["sources"]["cards"]["count"]
    assert d["sources"]["units"]["count"] == len(load(UNITS)["units"]) == 516
    assert len(d["issues"]) == s["issues_total"] == 2
    assert sum(s["by_severity"].values()) == s["issues_total"]
    assert s["by_severity"] == {"high": 0, "medium": 2, "low": 0}
    assert s["by_check"] == {"cp3_alignment": 2}
    assert s["fields_missing"] == {} and s["source_unparseable"] == 0
    assert s["answer_length_anomalies"] == 0
    assert s["concept_duplicate_groups"] == 0 and s["concept_duplicate_cards"] == 0
    assert s["traceable_cards"] == 124 and s["traceable_rate"] == 1.0
    assert s["themes_total"] == 21 and s["themes_with_cards"] == 21


# ---------------------------------------------------------------------------
# 2) 新增 20 张 CP3
# ---------------------------------------------------------------------------
def test_cp3_focus():
    d = report()
    f = d["cp3_focus"]
    assert f["total"] == 20 and len(f["cards"]) == 20
    assert f["source_parse_ok"] == 20 and f["book_in_units"] == 20
    assert f["traceable"] == 20 and f["length_ok"] == 20
    assert f["no_dup"] is True
    assert f["alignment_pass"] == 18 and f["alignment_fail"] == 2
    assert f["topic_match"] == 18 and f["topic_mismatch"] == 2
    for rec in f["cards"]:
        assert 104 <= rec["card_index"] < 124
        assert rec["source_ok"] and rec["traceable"] and 20 <= rec["answer_len"] <= 500
        assert rec["concept"]
    # 对齐不足的 2 张：coverage < 34% 且出现在问题清单
    bad = [r for r in f["cards"] if r["alignment_coverage"] < 0.34]
    assert len(bad) == 2
    issue_concepts = {i["concept"] for i in d["issues"] if i["check"] == "cp3_alignment"}
    assert {r["concept"] for r in bad} == issue_concepts


# ---------------------------------------------------------------------------
# 3) 问题清单
# ---------------------------------------------------------------------------
def test_issues_minimal():
    d = report()
    assert len(d["issues"]) == 2
    for it in d["issues"]:
        assert it["severity"] == "medium" and it["check"] == "cp3_alignment"
        assert it["detail"] and it["suggestion"]
        assert 0 <= it["card_index"] < 124
        assert "概念锚点" in it["suggestion"] or "重写问题" in it["suggestion"]
    assert all(len(it["suggestion"]) > 5 for it in d["issues"])


# ---------------------------------------------------------------------------
# 4) 纯函数 / 分布
# ---------------------------------------------------------------------------
def test_source_parse_and_distributions():
    assert cqa.parse_source("《rp_AP202610031830126542》 太平洋证券研究报告 p1-4")["ok"] is True
    bad = cqa.parse_source("无书名格式 1-4")
    assert bad["ok"] is False and bad["errors"]
    assert cqa.parse_source("《书》 章 p9-3")["ok"] is False  # 页码倒置
    assert cqa.source_kind("rp_AP2026") == "研报" and cqa.source_kind("伏击涨停") == "书籍"
    d = report()
    assert sum(r["cards"] for r in d["theme_distribution"]) == 124
    br = d["summary"]["book_vs_report"]
    assert br["书籍"] + br["研报"] == 124
    kinds = Counter(cqa.source_kind(cqa.parse_source(c.get("source")).get("book"))
                    for c in load(CARDS)["cards"])
    assert br["书籍"] == kinds["书籍"] and br["研报"] == kinds["研报"]


# ---------------------------------------------------------------------------
# 5) 只读安全 + MD
# ---------------------------------------------------------------------------
def test_no_source_modification_and_markdown():
    d = report()
    assert d["source_unchanged"] is True
    assert d["sources"]["cards"]["sha256"] == sha(CARDS)
    assert d["sources"]["units"]["sha256"] == sha(UNITS)
    assert len(load(CARDS)["cards"]) == 124
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("124 张学习卡质量复核", "新增 20 张 CP3", "问题清单", "修复建议",
                   "只读审计，不修改学习卡"):
        assert marker in md, marker
