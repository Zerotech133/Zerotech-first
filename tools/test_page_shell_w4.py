# -*- coding: utf-8 -*-
"""W4 PageShell adopted/redirect 契约。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS = ROOT / "portal" / "static" / "page_shell.js"
MANIFEST = ROOT / "portal" / "static" / "page_shell_migration.json"


def test_adopted_contract():
    s = JS.read_text(encoding="utf-8")
    assert "isAdopted" in s
    assert "adoptedHead" in s
    assert "已收编" in s
    assert "wb-adopted-chip" in s


def test_migration_manifest_w4():
    import json
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert "/home.html" in data["adopted"]
    assert "/quant.html" in data["adopted"]
    assert data["redirects"]["/picks_index.html"] == "/picks.html"
