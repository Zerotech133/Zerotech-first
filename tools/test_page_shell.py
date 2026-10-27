# -*- coding: utf-8 -*-
"""W1 PageShell v1 契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = ROOT / "portal" / "static" / "page_shell.css"
JS = ROOT / "portal" / "static" / "page_shell.js"
NAVJS = ROOT / "portal" / "static" / "nav.js"
MANIFEST = ROOT / "portal" / "static" / "page_shell_migration.json"


def test_page_shell_assets():
    assert CSS.exists() and JS.exists()
    css = CSS.read_text(encoding="utf-8")
    assert ".wb-crumb" in css
    assert ".wb-legacy-chip" in css
    assert ".wb-shell-head" in css


def test_page_shell_component():
    s = JS.read_text(encoding="utf-8")
    assert "window.WBShell" in s
    assert "function mount(" in s
    assert "wb-crumb" in s
    assert "data-wb-shell" in s
    assert "旧版" in s
    assert "MODERN" in s


def test_page_shell_loaded_globally():
    s = NAVJS.read_text(encoding="utf-8")
    assert "/static/page_shell.js" in s
    assert "/static/page_shell.css" in s


def test_page_shell_migration_manifest():
    import json
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert data["component"] == "page_shell"
    assert "/paper" in data["modern"]
    assert data["legacy_marker"] == "旧版"
