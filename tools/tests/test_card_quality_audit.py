# -*- coding: utf-8 -*-
"""105 张学习卡质量复核测试（task-103）。

覆盖：
  1) schema/统计一致性：卡片 105、单元实际数、问题/严重度/检查项计数自洽；
  2) 字段与重复检测：无高严重度问题、无字段缺失、已知「涨停密码」重复 1 组 2 张；
  3) source 解析与长度阈值：parse_source 正反例、source_kind 书籍/研报、答案长度全部在阈值内；
  4) 可追溯与分布：unit 可追溯 100%、21 主题分布、书籍 vs 研报比例与卡片数一致；
  5) 只读安全：报告记录的 cards/units sha256 == 当前文件（未修改学习卡）。
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
REPORT_JSON = R / "card_quality_audit_20261101.json"
REPORT_MD = R / "card_quality_audit_20261101.md"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_quality_audit.py"

_spec = importlib.util.spec_from_file_location("cqa_test", SCRIPT)
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
# 1) schema / 统计一致性
# ---------------------------------------------------------------------------
def test_report_schema_and_stats():
    d = report()
    for key in ("schema_version", "date", "generated_at", "mode", "policy", "sources",
                "summary", "issues", "traceable_cards", "theme_distribution", "known_notes",
                "source_unchanged", "disclaimer"):
        assert key in d, key
    s = d["summary"]
    assert s["cards_total"] == 105 == d["sources"]["cards"]["count"]
    assert d["sources"]["units"]["count"] == len(load(UNITS)["units"])
    assert len(d["issues"]) == s["issues_total"]
    assert sum(s["by_severity"].values()) == s["issues_total"]
    assert sum(s["by_check"].values()) == s["issues_total"]
    assert s["traceable_cards"] == sum(1 for r in d["traceable_cards"] if r["traceable"])
    for it in d["issues"]:
        assert it["severity"] in ("high", "medium", "low")
        assert it["issue_id"] and it["check"] and it["detail"] and it["suggestion"]
        assert 0 <= it["card_index"] < 105


# ---------------------------------------------------------------------------
# 2) 字段 / 重复检测
# ---------------------------------------------------------------------------
def test_fields_and_duplicates():
    d = report()
    s = d["summary"]
    # 本批无高严重度问题；必填字段齐全
    assert s["by_severity"]["high"] == 0
    assert s["fields_missing"] == {}
    # 已知重复：涨停密码 x2（1 组）
    assert s["concept_duplicate_groups"] == 1
    assert s["concept_duplicate_cards"] == 2
    dup_issues = [i for i in d["issues"] if i["check"] == "concept_duplicate"]
    assert len(dup_issues) == 1
    assert "涨停密码" in dup_issues[0]["concept"]
    assert "2 张" in dup_issues[0]["detail"]


# ---------------------------------------------------------------------------
# 3) source 解析 / 长度阈值
# ---------------------------------------------------------------------------
def test_source_parse_and_length():
    assert cqa.parse_source("《rp_AP202610031830126542》 太平洋证券研究报告 p1-4")["ok"] is True
    parsed = cqa.parse_source("《伏击涨停》 第8章 将军柱 p12-18")
    assert parsed["ok"] and parsed["book"] == "伏击涨停" and parsed["chapter"].startswith("第8章")
    assert parsed["p1"] == 12 and parsed["p2"] == 18
    bad = cqa.parse_source("无书名格式 1-4")
    assert bad["ok"] is False and bad["errors"]
    assert cqa.parse_source("《书》 章 p9-3")["ok"] is False  # 页码倒置
    assert cqa.source_kind("rp_AP2026") == "研报"
    assert cqa.source_kind("伏击涨停") == "书籍"
    # 长度阈值：全部在 [20, 500]
    cards = load(CARDS)["cards"]
    lens = [len(str(c.get("a") or "")) for c in cards]
    assert all(20 <= n <= 500 for n in lens), (min(lens), max(lens))
    d = report()
    assert d["summary"]["answer_length_anomalies"] == 0
    assert d["summary"]["source_unparseable"] == 0


# ---------------------------------------------------------------------------
# 4) 可追溯 / 主题与来源分布
# ---------------------------------------------------------------------------
def test_traceability_and_distribution():
    d = report()
    s = d["summary"]
    assert s["traceable_cards"] == 105 and s["traceable_rate"] == 1.0
    assert sum(s["trace_methods"].values()) == 105
    # 21 主题分布
    assert s["themes_total"] == 21
    assert s["themes_with_cards"] == 21
    themes = {r["theme"] for r in d["theme_distribution"]}
    assert len(themes) == 21
    assert sum(r["cards"] for r in d["theme_distribution"]) == 105
    # 书籍 vs 研报比例与 source 解析一致
    kinds = Counter()
    for c in load(CARDS)["cards"]:
        kinds[cqa.source_kind(cqa.parse_source(c.get("source")).get("book"))] += 1
    assert s["book_vs_report"]["书籍"] == kinds["书籍"]
    assert s["book_vs_report"]["研报"] == kinds["研报"]
    assert s["book_vs_report"]["书籍"] + s["book_vs_report"]["研报"] == 105


# ---------------------------------------------------------------------------
# 5) 只读安全
# ---------------------------------------------------------------------------
def test_no_source_modification():
    d = report()
    assert d["source_unchanged"] is True
    assert d["sources"]["cards"]["sha256"] == sha(CARDS)
    assert d["sources"]["units"]["sha256"] == sha(UNITS)
    assert len(load(CARDS)["cards"]) == 105
    # 报告 MD 存在且含只读声明
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("质量复核", "问题清单", "主题分布", "修复建议", "只读审计，不修改学习卡"):
        assert marker in md, marker
