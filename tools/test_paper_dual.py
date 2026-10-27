# -*- coding: utf-8 -*-
"""模拟仓深色工作台与双版本 UI 规范的契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "paper.html"
CSS = ROOT / "portal" / "static" / "paper_workbench.css"


def test_paper_uses_os_dark_tokens():
    s = PAGE.read_text(encoding="utf-8")
    for token in ("var(--os-bg)", "var(--os-paper)", "var(--os-ink)", "var(--os-muted)", "var(--os-line)", "var(--os-blue)"):
        assert token in s
    assert 'id="os-paper-theme"' in s
    assert "var(--pw-bg)" in CSS.read_text(encoding="utf-8")


def test_paper_v2_shell_and_stylesheet():
    s = PAGE.read_text(encoding="utf-8")
    assert '/static/paper_workbench.css' in s
    assert '<body class="paper-v2">' in s
    for cls in ("pw-hero", "pw-context", "pw-footer", "pw-brand"):
        assert cls in s
    assert 'role="tablist"' in s
    css = CSS.read_text(encoding="utf-8")
    for token in ("--pw-bg:#080e19", "--pw-cyan:#6bd7e5", ".paper-v2 header.pw-hero", ".paper-v2 .tab.active"):
        assert token in css


def test_paper_links_back_to_cockpit():
    s = PAGE.read_text(encoding="utf-8")
    assert 'href="/zero_proto.html"' in s
    assert "AI 驾驶舱" in s


def test_paper_preserves_business_ids():
    s = PAGE.read_text(encoding="utf-8")
    for i in ("kpis", "duty-input", "plan-body", "stops-body", "strat12-grid",
              "exec-ai_team", "agent-grid", "pos-body", "hist-body", "nav-curve"):
        assert 'id="%s"' % i in s


def test_paper_a11y_contract():
    s = PAGE.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in s
    assert ":focus-visible" in s
    assert 'role="main"' in s
    assert 'aria-live="polite"' in s
    assert 'aria-current="page"' in s
