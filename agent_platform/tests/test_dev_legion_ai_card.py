# -*- coding: utf-8 -*-
"""LS-05 AI 席位卡测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "portal"))
import dev_legion_ai_card as C  # noqa: E402
import api_dev_legion as A  # noqa: E402


def test_ai_cards_from_real_org():
    org = json.loads(A.ORG.read_text(encoding="utf-8"))
    cards = C.build_ai_cards(org)
    ids = {c["id"] for c in cards}
    assert "DEV2" in ids and "ARCH1" in ids
    dev2 = next(c for c in cards if c["id"] == "DEV2")
    assert dev2["discipline"] == "ai"
    assert dev2["model"]
    assert isinstance(dev2["fallback"], list)
    assert dev2["eval_status"]


def test_empty_org_safe():
    assert C.build_ai_cards({}) == []


def test_api_payload_has_ai_cards():
    payload = A.build_payload(A.ROOT)
    assert isinstance(payload.get("ai_cards"), list)
    assert any(c["id"] == "DEV2" for c in payload["ai_cards"])
