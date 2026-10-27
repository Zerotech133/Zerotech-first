# -*- coding: utf-8 -*-
"""nav.js 生成物回归：不再内联 style/hex，class + nav.css。"""
import re
from pathlib import Path

NAV = Path(__file__).resolve().parent.parent / "portal" / "static" / "nav.js"
CSS = Path(__file__).resolve().parent.parent / "portal" / "static" / "nav.css"


def test_nav_js_tokenized():
    s = NAV.read_text(encoding="utf-8")
    assert "wb-nav" in s
    assert "/static/nav.css" in s
    assert not re.search(r"style\s*=", s), "nav.js 不应再注入内联 style"
    assert not re.findall(r"#[0-9a-fA-F]{3,8}\b", s), "nav.js 不应硬编码 hex"


def test_nav_css_uses_tokens():
    s = CSS.read_text(encoding="utf-8")
    assert "var(--" in s
    assert ".wb-nav" in s
    assert "prefers-reduced-motion" in s
