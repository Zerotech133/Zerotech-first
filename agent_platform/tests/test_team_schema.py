# -*- coding: utf-8 -*-
"""菜鸟结构化输出移植测试（2026-10-30 WO-INV-CAINIAO-PORT）。"""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "agent_platform" / "engine"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "team_schema_t", REPO / "agent_platform" / "engine" / "team_schema.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


m = _load()


def test_build_instruction_types():
    for t in ("market_technical", "news_event", "fundamental", "bull_research", "bear_research"):
        ins = m.build_instruction(t)
        assert "```json" in ins and "输出格式要求" in ins
    assert m.build_instruction("final_decision") == ""
    assert m.build_instruction("unknown") == ""


def test_parse_structured_block():
    text = "分析正文\n\n---\n```json\n{\"overallTrend\": \"bearish\", \"confidence\": \"medium\"}\n```\n"
    d = m.parse_structured(text)
    assert d["overallTrend"] == "bearish" and d["confidence"] == "medium"
    assert m.extract_markdown(text) == "分析正文"


def test_parse_structured_plain_and_failure():
    assert m.parse_structured('{"a": 1}') == {"a": 1}
    assert m.parse_structured("没有 JSON")["error"] == "no_json_block_found"
    assert m.parse_structured("")["error"] == "empty_content"


def test_parse_line_comments():
    text = '```json\n{\n  "a": 1, // 注释\n  "b": "x//y"\n}\n```'
    d = m.parse_structured(text)
    assert d == {"a": 1, "b": "x//y"}


def test_result_summary_confidence_scale():
    s = m.result_summary({"recommendation": "WATCH", "confidence": 60,
                          "risk_level": "MEDIUM"})
    assert s["recommendation"] == "WATCH" and s["confidence"] == 0.6
    assert s["risk_level"] == "MEDIUM"
