# -*- coding: utf-8 -*-
"""A2A cards API：6 卡 discovery、单卡、未知 404。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "portal"))

import api_a2a  # noqa: E402


class FakeHandler:
    def __init__(self):
        self.calls = []

    def _json(self, obj, code=200):
        self.calls.append((code, obj))


def _call(path):
    h = FakeHandler()
    api_a2a.handle_get(h, path)
    assert len(h.calls) == 1
    return h.calls[0]


def test_list_cards():
    code, obj = _call("/api/a2a/agent-cards")
    assert code == 200 and obj["ok"] is True
    assert obj["data"]["count"] == 6


def test_single_card():
    code, obj = _call("/api/a2a/agent-cards/acad1-legion")
    assert code == 200
    assert obj["data"]["x-composition"] == "acad1-legion"


def test_well_known_alias():
    code, obj = _call("/api/a2a/acad1-legion/.well-known/agent-card.json")
    assert code == 200
    assert obj["data"]["protocolVersion"] == "0.3.0"


def test_unknown_404():
    code, obj = _call("/api/a2a/nope")
    assert code == 404 and obj["code"] == "not_found"
