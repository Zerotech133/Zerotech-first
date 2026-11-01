# -*- coding: utf-8 -*-
"""D1 交棒回执 API 测试（WO-D1-HANDOFF-20261101）。离线。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "portal"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent_platform import d1_handoff as H  # noqa: E402
import api_d1_handoff as A  # noqa: E402


class FakeHandler:
    def __init__(self, body=b"{}", path="/api/d1/handoff"):
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _req(obj, path="/api/d1/handoff"):
    return FakeHandler(json.dumps(obj, ensure_ascii=False).encode(), path)


def test_api_create_ack(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
    h = _req({"action_name": "create", "code": "002445.SZ", "target": "TR1",
              "action": "trade", "rationale": "3买回抽交模拟所",
              "buy_zone": [2.98, 3.05], "paper_only": True})
    A.handle_post(h)
    assert h.status == 200 and h.payload["ok"] is True
    rid = h.payload["handoff"]["receipt_id"]
    h2 = _req({"action_name": "ack", "receipt_id": rid, "by": "TR1",
               "status": "received", "note": "收到"})
    A.handle_post(h2)
    assert h2.status == 200
    assert h2.payload["handoff"]["status"] == "acked"


def test_api_bad_target(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
    h = _req({"code": "X", "target": "WA1", "action": "trade",
              "rationale": "bad", "paper_only": True})
    A.handle_post(h)
    assert h.status == 400
    assert h.payload["error"] == "bad_target_action"


def test_api_get_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(H, "DEFAULT_LEDGER", tmp_path / "h.jsonl")
    H.append_handoff({"code": "A", "target": "WA1", "action": "watch",
                      "rationale": "观察", "paper_only": True},
                     ledger=tmp_path / "h.jsonl")
    h = FakeHandler(path="/api/d1/handoffs")
    A.handle_get(h, "")
    assert h.status == 200 and h.payload["summary"]["total"] == 1
