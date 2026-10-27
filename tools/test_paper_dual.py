# -*- coding: utf-8 -*-
"""模拟仓深色工作台与双版本 UI 规范的契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "paper.html"


def test_paper_uses_os_dark_tokens():
    s = PAGE.read_text(encoding="utf-8")
    for token in ("#090f1c", "#111e31", "#15243a", "#eef5ff", "#a7b8cc", "#30445d", "#6bd7e5"):
        assert token in s
    assert 'id="os-paper-theme"' in s


def test_paper_links_back_to_cockpit():
    s = PAGE.read_text(encoding="utf-8")
    assert 'href="/zero_proto.html"' in s
    assert "AI 驾驶舱" in s


def test_paper_a11y_contract():
    s = PAGE.read_text(encoding="utf-8")
    assert "prefers-reduced-motion" in s
    assert ":focus-visible" in s
    assert 'role="main"' in s
    assert 'aria-live="polite"' in s
    assert 'aria-current="page"' in s
