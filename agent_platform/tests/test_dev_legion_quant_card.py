# -*- coding: utf-8 -*-
"""LS-06 量化席位卡测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import dev_legion_quant_card as Q  # noqa: E402
import api_dev_legion as A  # noqa: E402


def test_quant_cards():
    org = json.loads(A.ORG.read_text(encoding="utf-8"))
    cards = Q.build_quant_cards(org)
    ids = {c["id"] for c in cards}
    assert ids == {"DEV3", "DEV4"}
    for c in cards:
        assert c["discipline"] == "quant"
        assert c["model"]
        assert "未接" in c["strategy_status"]


def test_empty_org():
    assert Q.build_quant_cards({}) == []


def test_api_payload_has_quant_cards():
    p = A.build_payload(A.ROOT)
    assert isinstance(p.get("quant_cards"), list)
    assert {c["id"] for c in p["quant_cards"]} == {"DEV3", "DEV4"}
