# -*- coding: utf-8 -*-
"""P0 /paper 首屏减法契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "paper.html"
CSS = ROOT / "portal" / "static" / "paper_simple.css"
JS = ROOT / "portal" / "static" / "paper_simple.js"


def test_paper_simple_shell():
    s = PAGE.read_text(encoding="utf-8")
    assert "paper-simple" in s
    assert "/static/paper_simple.css" in s
    assert "/static/paper_simple.js" in s
    assert "p0-cockpit" in s


def test_paper_simple_rules():
    s = CSS.read_text(encoding="utf-8")
    assert "display:none!important" in s
    assert "p0-collapsible" in s
    assert "nth-child(5)" in s


def test_paper_simple_js_preserves_features():
    s = JS.read_text(encoding="utf-8")
    assert "您的进门路线" in s
    assert "AI 值班经理" in s
    assert "p0-toggle" in s
    assert "p0-collapsible" in s
    assert "askDuty" not in s


def test_paper_original_ids_preserved():
    s = PAGE.read_text(encoding="utf-8")
    for i in ("kpis", "duty-input", "plan-body", "stops-body", "strat12-grid",
              "agent-grid", "pos-body", "hist-body"):
        assert 'id="%s"' % i in s
