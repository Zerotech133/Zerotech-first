# -*- coding: utf-8 -*-
"""作战台实时聚合测试（WO-WARROOM-LIVE-20261030）。"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
PORTAL = REPO / "portal"
sys.path.insert(0, str(PORTAL))

import war_room_data  # noqa: E402


def test_snapshot_shape():
    d = war_room_data.war_room_snapshot()
    for k in ("ok", "items", "d1_summary", "pool_summary", "strategies",
              "strategy_counts", "paper", "watch", "links", "confirmations"):
        assert k in d, k
    assert d["ok"] is True
    assert isinstance(d["items"], list)
    assert "nav" in d["paper"]
    assert "armed" in d["watch"]


def test_items_have_strategy_and_handoff():
    d = war_room_data.war_room_snapshot()
    for x in d["items"][:5]:
        assert "sources" in x and "handoff" in x and "action" in x
