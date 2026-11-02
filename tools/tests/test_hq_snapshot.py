# -*- coding: utf-8 -*-
"""HQ-06 静态快照 + /hq.html 预览页：真实数据、只读、无第二入口。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_snapshot  # noqa: E402

SNAP = ROOT / "portal" / "static" / "hq_snapshot.json"
PAGE = ROOT / "portal" / "hq.html"


def test_snapshot_shape():
    s = hq_snapshot.build_snapshot()
    assert s["schema"] == "hq/snapshot/v1"
    assert s["read_only"] is True
    assert s["org"]["summary"]["agents"] >= 77
    assert "wip" in s["tasks"]["summary"]
    assert "total_decisions" in s["approvals"]["summary"]
    assert "totals" in s["budget"]
    assert "coalesced_wakes" in s["heartbeat"]


def test_snapshot_file_is_valid():
    s = json.loads(SNAP.read_text(encoding="utf-8-sig"))
    assert s["schema"] == "hq/snapshot/v1"
    assert s["generated_at"]
    assert s["org"]["departments"]


def test_page_contract():
    h = PAGE.read_text(encoding="utf-8", errors="replace")
    assert "/static/tokens.css" in h
    assert "/static/nav.js" in h
    assert "/static/hq_snapshot.json" in h
    for tab in ("org", "tasks", "approvals", "budget", "heartbeat"):
        assert f'data-tab="{tab}"' in h


def test_snapshot_is_not_second_store():
    s = json.loads(SNAP.read_text(encoding="utf-8-sig"))
    assert "静态" in s["note"] and "只读" in s["note"]
