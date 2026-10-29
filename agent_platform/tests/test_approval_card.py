# -*- coding: utf-8 -*-
"""S2 审批卡构建测试。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import approval_card as C  # noqa: E402


def test_card_structure():
    card = C.build_card("WO-TEST", "试点卡", evidence_hash="abc123")
    assert card["schema"] == "2.0"
    assert "WO-TEST" in card["header"]["title"]["content"]
    body = card["body"]["elements"]
    tags = [e.get("tag") for e in body]
    assert "markdown" in tags and "column_set" in tags
    buttons = body[-1]["columns"]
    values = [b["elements"][0]["behaviors"][0]["value"] for b in buttons]
    tagsv = {v["tag"] for v in values}
    assert {"approval:approve", "approval:reject", "approval:escalate"} <= tagsv
    assert all(v["task"] == "WO-TEST" for v in values)
