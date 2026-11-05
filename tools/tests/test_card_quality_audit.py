# -*- coding: utf-8 -*-
"""学习卡质量复核测试（当前全量卡：400 张；2026-11-04 更新）。

覆盖：
  1) 报告 schema/统计自洽：卡数=当前卡片数、单元 516、问题 0、可追溯 100%；
  2) 可追溯与主题分布：traceable_cards 完整、主题合计=卡数、来源类型合计=卡数；
  3) source 解析纯函数正反例；
  4) 只读安全：source_unchanged、报告 sha256 == 当前文件、MD 结构完整。
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
S = REPO / "portal" / "static"
R = REPO / "research" / "reports"
CARDS = S / "kb_learning_cards.json"
UNITS = S / "kb_units_all.json"
SCRIPT = REPO / "tools" / "kb_corpus" / "card_quality_audit.py"


def latest_report() -> tuple[Path, Path]:
    """最新全量审计报告（排除历史 124 张快照），按报告 generated_at 而非 mtime 选。"""
    js = [p for p in R.glob("card_quality_audit_*.json") if "_124_" not in p.name]
    js.sort(key=lambda p: json.loads(p.read_text(encoding="utf-8-sig")).get("generated_at", ""),
            reverse=True)
    assert js, "缺少 card_quality_audit 全量报告"
    return js[0], js[0].with_suffix(".md")


REPORT_JSON, REPORT_MD = latest_report()

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


def test_report_schema_and_stats():
    d = report()
    for key in ("schema_version", "date", "generated_at", "mode", "sources", "summary",
                "issues", "traceable_cards", "theme_distribution", "source_unchanged", "disclaimer"):
        assert key in d, key
    s = d["summary"]
    cards = load(CARDS)["cards"]
    units = load(UNITS)["units"]
    assert s["cards_total"] == len(cards) == d["sources"]["cards"]["count"]
    assert s["units_total"] == len(units) == 516
    assert d["sources"]["units"]["count"] == len(units)
    assert s["issues_total"] == 0 and d["issues"] == []
    assert sum(s["by_severity"].values()) == 0
    assert s["fields_missing"] == {} and s["source_unparseable"] == 0
    assert s["concept_duplicate_groups"] == 0 and s["concept_duplicate_cards"] == 0
    assert s["traceable_cards"] == s["cards_total"] and s["traceable_rate"] == 1.0
    assert s["themes_total"] == 21 and s["themes_with_cards"] == 21
    assert s["source_kinds"] == {"研报": 147, "书籍": 12, "笔记": 28, "视频号": 213}


def test_traceability_and_distributions():
    d = report()
    tr = d["traceable_cards"]
    assert len(tr) == d["summary"]["cards_total"]
    assert all(x.get("traceable") is True and x.get("concept") for x in tr)
    assert d["summary"]["trace_methods"].get("provenance(file+line)", 0) > 0
    assert sum(r["cards"] for r in d["theme_distribution"]) == d["summary"]["cards_total"]
    sk = d["summary"]["source_kinds"]
    assert sum(sk.values()) == d["summary"]["cards_total"]
    assert sk["研报"] + sk["书籍"] + sk["笔记"] + sk["视频号"] == d["summary"]["cards_total"]


def test_source_parse_pure_functions():
    assert cqa.parse_source("《rp_AP202610031830126542》 太平洋证券研究报告 p1-4")["ok"] is True
    bad = cqa.parse_source("无书名格式 1-4")
    assert bad["ok"] is False and bad["errors"]
    assert cqa.parse_source("《书》 章 p9-3")["ok"] is False  # 页码倒置
    assert cqa.source_kind("rp_AP2026") == "研报" and cqa.source_kind("伏击涨停") == "书籍"


def test_no_source_modification_and_markdown():
    d = report()
    assert d["source_unchanged"] is True
    assert d["sources"]["cards"]["sha256"] == sha(CARDS)
    assert d["sources"]["units"]["sha256"] == sha(UNITS)
    assert len(load(CARDS)["cards"]) == d["summary"]["cards_total"]
    md = REPORT_MD.read_text(encoding="utf-8")
    for marker in ("张学习卡质量复核", "只读审计，不修改学习卡", "质量统计", "unit 可追溯率"):
        assert marker in md, marker
