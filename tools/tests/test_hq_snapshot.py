# -*- coding: utf-8 -*-
"""HQ-06 静态快照 + /hq.html 预览页：真实数据、只读、无第二入口。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_snapshot  # noqa: E402

SNAP = ROOT / "portal" / "private" / "hq_snapshot.json"
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
    assert "/api/hq/snapshot" in h
    assert "/static/hq_snapshot.json" not in h  # 敏感快照不得公开静态暴露
    for tab in ("org", "tasks", "approvals", "budget", "heartbeat"):
        assert f'data-tab="{tab}"' in h


def test_snapshot_is_not_second_store():
    s = json.loads(SNAP.read_text(encoding="utf-8-sig"))
    assert "静态" in s["note"] and "只读" in s["note"]


def test_public_static_snapshot_absent():
    # 回归：敏感快照不得回到公开 static 目录（未鉴权可读）。
    assert not (ROOT / "portal" / "static" / "hq_snapshot.json").exists()


def test_snapshot_atomic_write(tmp_path):
    out = tmp_path / "snap.json"
    assert hq_snapshot.main(["--out", str(out)]) == 0
    assert json.loads(out.read_text(encoding="utf-8"))["schema"] == "hq/snapshot/v1"
    assert not (tmp_path / "snap.json.tmp").exists()
