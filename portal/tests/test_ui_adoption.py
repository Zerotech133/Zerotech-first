# -*- coding: utf-8 -*-
"""UI Kit 在两个高频面的接入契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _text(name):
    return (ROOT / name).read_text(encoding="utf-8")


def test_os_entry_uses_ui_kit():
    s = _text("os_entry_v9_prod.html")
    assert "/static/ui/ui.css" in s and "/static/ui/ui.js" in s
    assert 'class="ui-kpi os-kpi' in s
    assert 'class="ui-panel os-panel' in s


def test_os_board_uses_ui_kit():
    s = _text("os.html")
    assert "/static/ui/ui.css" in s and "/static/ui/ui.js" in s
    assert 'class="ui-card acard"' in s
    assert 'class="ui-kpi tile"' in s
