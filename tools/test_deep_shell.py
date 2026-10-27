# -*- coding: utf-8 -*-
"""深屏骨架推广契约：/picks.html 静态接入 + /strategies 生成器接入。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PICKS = ROOT / "portal" / "picks.html"
API = ROOT / "portal" / "api_backtest.py"
CSS = ROOT / "portal" / "static" / "deep_shell.css"


def test_picks_deep_shell():
    s = PICKS.read_text(encoding="utf-8")
    assert '/static/deep_shell.css' in s
    assert '<body class="dw-page picks-page">' in s
    for cls in ("dw-hero", "dw-quick", "dw-context", "dw-footer"):
        assert cls in s
    for i in ("clock", "vitals", "filters", "list", "detail"):
        assert 'id="%s"' % i in s


def test_strategies_generator_deep_shell():
    s = API.read_text(encoding="utf-8")
    assert '/static/deep_shell.css' in s
    assert "dw-page strategies-page" in s
    assert "dw-hero" in s and "dw-footer" in s
    assert "策略资产中心" in s
    assert "dw-kpi" in s


def test_deep_shell_css_shape():
    s = CSS.read_text(encoding="utf-8")
    for token in ("body.dw-page", ".dw-hero", ".dw-quick", ".dw-context",
                  ".dw-footer", "body.picks-page", "body.strategies-page"):
        assert token in s
