# -*- coding: utf-8 -*-
"""ui_ux_audit 单测（离线）。"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ui_ux_audit as u  # noqa: E402


GOOD = """<!doctype html><html lang="zh-CN"><head>
<meta name="viewport" content="width=device-width">
<link rel="stylesheet" href="/static/tokens.css"></head>
<body><button aria-label="刷新">刷新</button>
<div aria-live="polite"></div><div class="skeleton"></div></body></html>"""

BAD = """<html><body><div style="color:#ff0000">x</div></body></html>"""


def test_good_page_minimal_warnings():
    r = u.scan_text("good.html", GOOD)
    codes = {f["code"] for f in r["findings"]}
    assert "tokens_link" not in codes
    assert "a11y" not in codes
    assert "focus" in codes  # GOOD 没写 focus-visible，仍应提示
    assert r["metrics"]["lang"] is True


def test_bad_page_warnings():
    r = u.scan_text("bad.html", BAD)
    codes = {f["code"] for f in r["findings"]}
    assert {"raw_hex", "inline_style", "html_lang", "viewport",
            "tokens_link"} <= codes
    assert r["warn"] >= 3


def test_tokens_file_exempt():
    r = u.scan_text("tokens.css", ":root{--x:#fff}")
    codes = {f["code"] for f in r["findings"]}
    assert "raw_hex" not in codes
    assert "token_primitives" in codes


def test_markdown_contains_findings():
    results = [u.scan_text("x.html", BAD)]
    md = u.to_markdown(results)
    assert "# UI/UX 静态审计" in md and "raw_hex" in md
