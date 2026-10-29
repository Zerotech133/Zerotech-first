# -*- coding: utf-8 -*-
"""组件视觉基线 warn-only 门契约。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ui_visual_gate_t", str(ROOT / "tools" / "ui_visual_gate.py"))
G = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(G)


def test_manifest_exists_and_hashes_match():
    data = G.check(None)
    assert data["ok"] is True, data
    assert not data["warnings"], data["warnings"]


def test_warn_only_cli_returns_zero():
    assert G.main(["--warn-only", "--json"]) == 0


def test_assets_listed():
    assert "portal/static/ui/ui.css" in G.ASSETS
    assert "docs/visual_baseline/2026-10-30_ui_kit/ui_kit_1440.png" in G.SHOTS
