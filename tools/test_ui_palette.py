# -*- coding: utf-8 -*-
"""Week3 命令面板/密度/键盘审计契约。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent.parent
PAGE = ROOT / "portal" / "os_entry_v9_prod.html"
JS = ROOT / "portal" / "static" / "ui_palette.js"
CSS = ROOT / "portal" / "static" / "ui_palette.css"

sys.path.insert(0, str(ROOT / "tools"))
import ui_keyboard_audit as kba  # noqa: E402


def test_page_wires_palette():
    s = PAGE.read_text(encoding="utf-8")
    assert "/static/ui_palette.css" in s
    assert "/static/ui_palette.js" in s
    assert 'id="view-ai" tabindex="-1"' in s


def test_palette_is_safe_and_keyboard():
    s = JS.read_text(encoding="utf-8")
    for token in ("Ctrl", "metaKey", "cmd", "Command", "Escape", "ArrowDown",
                  "ArrowUp", "aria-modal", "role"):
        assert token.lower() in s.lower()
    # 不引入执行/持久化/网络副作用（just navigation/scroll/toggle）
    for bad in ("fetch(", "XMLHttpRequest", "localStorage", "eval(",
                "sessionStorage", "sendBeacon"):
        assert bad not in s


def test_density_css():
    s = CSS.read_text(encoding="utf-8")
    assert 'data-wb-density="compact"' in s
    assert "table th" in s and "table td" in s


def test_page_skip_link_first():
    rep = kba.audit(str(PAGE))
    assert rep["skip_first"] is True
    assert rep["positive_tabindex"] == []
