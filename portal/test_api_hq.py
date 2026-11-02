# -*- coding: utf-8 -*-
"""HQ-06 /api/hq/* 只读契约：8 端点、未知 404、无写路径。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portal"))

import api_hq  # noqa: E402


class FakeHandler:
    def __init__(self):
        self.calls = []

    def _json(self, obj, code=200):
        self.calls.append((code, obj))


def _call(path, qs=""):
    h = FakeHandler()
    api_hq.handle_get(h, path, qs)
    assert len(h.calls) == 1
    return h.calls[0]


def test_org_endpoint():
    code, obj = _call("/api/hq/org")
    assert code == 200 and obj["ok"] is True
    assert obj["data"]["summary"]["agents"] >= 77


def test_tasks_and_board():
    code, obj = _call("/api/hq/tasks")
    assert code == 200 and obj["data"]["summary"]["total"] >= 2000
    code, obj = _call("/api/hq/tasks/board")
    assert code == 200 and "board" in obj["data"]


def test_approvals_and_budget():
    code, obj = _call("/api/hq/approvals")
    assert code == 200 and obj["data"]["summary"]["total_decisions"] >= 40
    code, obj = _call("/api/hq/budget", "days=30")
    assert code == 200 and "budget" in obj["data"]


def test_heartbeat_and_snapshot():
    code, obj = _call("/api/hq/heartbeat/status")
    assert code == 200 and "coalesced_wakes" in obj["data"]
    code, obj = _call("/api/hq/snapshot")
    assert code == 200 and obj["data"]["schema"] == "hq/snapshot/v1"


def test_unknown_is_404():
    code, obj = _call("/api/hq/nope")
    assert code == 404 and obj["code"] == "not_found"


def test_page_uses_authenticated_api_only():
    h = (ROOT / "portal" / "hq.html").read_text(encoding="utf-8", errors="replace")
    assert "/api/hq/snapshot" in h
    assert "/static/hq_snapshot.json" not in h  # 不再公开静态快照
    assert "需登录" in h
