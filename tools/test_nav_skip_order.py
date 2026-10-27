# -*- coding: utf-8 -*-
"""skip link 必须在统一顶栏之前（真实 Tab 顺序回归）。"""
from pathlib import Path

NAV = Path(__file__).resolve().parent.parent / "portal" / "static" / "nav.js"


def test_nav_inserts_after_skip_link():
    s = NAV.read_text(encoding="utf-8")
    assert "querySelector('.wb-skip')" in s
    assert "insertBefore(bar,skip.nextSibling)" in s
