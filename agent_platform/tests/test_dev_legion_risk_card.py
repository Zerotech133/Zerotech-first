# -*- coding: utf-8 -*-
"""LS-07 回测/风控卡测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import dev_legion_risk_card as R  # noqa: E402
import api_dev_legion as A  # noqa: E402


def test_risk_cards():
    org = json.loads(A.ORG.read_text(encoding="utf-8"))
    cards = R.build_risk_cards(org)
    assert len(cards) == 1 and cards[0]["id"] == "DEV4"
    assert "未接" in cards[0]["go_no_go_status"]
    assert cards[0]["model"]


def test_empty_org():
    assert R.build_risk_cards({}) == []


def test_api_payload_has_risk_cards():
    p = A.build_payload(A.ROOT)
    assert isinstance(p.get("risk_cards"), list)
    assert p["risk_cards"][0]["id"] == "DEV4"
