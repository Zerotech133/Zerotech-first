# -*- coding: utf-8 -*-
"""L4 provider 契约测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent_platform" / "engine"))
import l4_provider as L  # noqa: E402


def test_missing_file_is_missing(tmp_path):
    r = L.load_l4("600519", path=tmp_path / "nope.json")
    assert r["status"] == "missing"


def test_established(tmp_path):
    p = tmp_path / "l4.json"
    p.write_text(json.dumps({"symbols": {"600519": {
        "status": "established", "source": "xsec", "as_of": "2026-10-30",
        "confidence": 0.7}}}), encoding="utf-8")
    r = L.load_l4("600519", path=p)
    assert r["status"] == "established"
    assert r["source"] == "xsec"


def test_unknown_symbol_and_invalid_status(tmp_path):
    p = tmp_path / "l4.json"
    p.write_text(json.dumps({"symbols": {"600519": {"status": "weird"}}}),
                 encoding="utf-8")
    assert L.load_l4("000858", path=p)["status"] == "missing"
    assert L.load_l4("600519", path=p)["status"] == "unknown"
