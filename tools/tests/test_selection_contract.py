# -*- coding: utf-8 -*-
"""策略库/选股契约测试（轻量，不跑引擎）。"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
REG = REPO / "agent_platform" / "catalogs" / "strategy_registry.json"
STATIC = REPO / "portal" / "static" / "selection_daily.json"
PAGE = REPO / "portal" / "selection.html"
CLI = REPO / "tools" / "research" / "selection_daily.py"


def test_registry_schema():
    d = json.loads(REG.read_text(encoding="utf-8"))
    assert d["library"]["owner"] == "司马微"
    ids = {s["id"] for s in d["strategies"]}
    assert {"S01_H14_quality_trap_guard", "S02_composite_mvp",
            "S03_H18_forecast_revision_watch", "S04_AH_premium_pair"} <= ids
    for s in d["strategies"]:
        for k in ("id", "name", "type", "status", "owner", "validation", "executable"):
            assert k in s, (s.get("id"), k)
        assert s["owner"] == "司马微"


def test_static_daily_payload():
    d = json.loads(STATIC.read_text(encoding="utf-8"))
    assert d["library"]["owner"] == "司马微"
    assert len(d["strategies"]) >= 4
    if d.get("s02_paused"):
        assert d["picks"] == []
    else:
        assert len(d["picks"]) > 0
        p = d["picks"][0]
        for k in ("ts_code", "name", "score", "reason"):
            assert k in p
    assert "validated" in d["picks_validation"] and "unvalidated" in d["picks_validation"]


def test_page_and_tool_registered():
    assert PAGE.is_file()
    txt = PAGE.read_text(encoding="utf-8")
    assert "司马微" in txt and "selection_daily.json" in txt
    skills = json.loads((REPO / "agent_platform/registry/skill_registry.json").read_text(encoding="utf-8"))
    assert any(s["id"] == "selection_daily" for s in skills["skills"])


def test_engine_cli_help():
    r = subprocess.run([sys.executable, "-X", "utf8", str(CLI), "--help"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
    assert r.returncode == 0
    assert "--date" in r.stdout and "--top" in r.stdout
