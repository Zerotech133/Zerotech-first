# -*- coding: utf-8 -*-
"""W3 前置：6 军团 hermes-control 接口、MCP registry、A2A cards。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import a2a_cards  # noqa: E402
import mcp_registry  # noqa: E402

try:
    import yaml
except Exception:  # noqa: BLE001
    yaml = None


def test_all_six_manifests_consume_hermes_control():
    ids = ["acad1-legion", "data-warehouse", "huangzhong-acceptance",
           "lusu-d1-flow", "quant-domain", "quant-strategy-line"]
    for cid in ids:
        p = ROOT / "compositions" / cid / "manifest.yaml"
        s = p.read_text(encoding="utf-8")
        assert "interface/hermes-control" in s, cid


def test_interface_file():
    p = ROOT / "platform" / "interfaces" / "hermes-control.yaml"
    assert p.is_file()
    d = yaml.safe_load(p.read_text(encoding="utf-8")) if yaml else {}
    assert d.get("id") == "hermes-control"
    assert d.get("endpoint", "").startswith("http://127.0.0.1:8787")


def test_mcp_registry():
    r = mcp_registry.validate()
    assert r["ok"] is True
    assert r["servers"] >= 13
    assert len(r["ids"]) == len(set(r["ids"]))


def test_mcp_registry_reconcile_hermes_config():
    rec = mcp_registry.reconcile()
    assert rec["error"] is None
    assert rec["matched"] >= 13
    assert rec["missing_in_registry"] == []
    assert rec["missing_in_config"] == []


def test_a2a_cards_match_compositions():
    r = a2a_cards.validate()
    assert r["ok"] is True
    assert r["cards"] >= 6
    assert not r["missing"]


def test_a2a_cards_are_planned_and_loopback():
    for c in a2a_cards.load_cards():
        assert c["x-status"] in ("planned", "discovery", "active")
        assert "127.0.0.1" in c["url"]
        assert c["protocolVersion"] == "0.3.0"


def test_mcp_cli_json(capsys):
    rc = mcp_registry.main(["--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["ok"] is True and d["servers"] >= 13
