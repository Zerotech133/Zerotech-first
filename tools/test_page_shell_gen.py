# -*- coding: utf-8 -*-
"""PageShell v2 生成器契约。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("page_shell_gen_t", str(ROOT / "tools" / "page_shell_gen.py"))
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


def test_template_exists_with_placeholders():
    t = G.TEMPLATE.read_text(encoding="utf-8")
    for key in ("{{TITLE}}", "{{EYEBROW}}", "{{LEAD}}", "{{CONTENT}}"):
        assert key in t
    assert "/static/tokens.css" in t
    assert "/static/ui/ui.css" in t
    assert "/static/ui/ui.js" in t


def test_inject_is_idempotent(tmp_path, monkeypatch):
    p = tmp_path / "page.html"
    p.write_text("<html><body><h1>x</h1></body></html>", encoding="utf-8")
    monkeypatch.setattr(G, "REPO", tmp_path)
    once = G.inject(p.read_text(encoding="utf-8"))
    twice = G.inject(once)
    assert once.count("data-page-shell") >= 5
    assert twice == once
    assert G.revert(once) == "<html><body><h1>x</h1></body></html>"


def test_assets_canonical_paths():
    assert G.CSS == ("/static/tokens.css", "/static/os_theme.css", "/static/ui/ui.css")
    assert "/static/nav.js" in G.SCRIPTS
    assert "/static/ui/ui.js" in G.SCRIPTS
