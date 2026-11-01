# -*- coding: utf-8 -*-
"""D1 投研会 API 契约测试（WO-D1-MEETING-20261101）。离线。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "portal"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent_platform import d1_meeting as M  # noqa: E402
import api_d1_meeting as A  # noqa: E402


class FakeHandler:
    def __init__(self, body=b"{}", path="/api/d1/meeting"):
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _post(obj):
    return FakeHandler(json.dumps(obj, ensure_ascii=False).encode("utf-8"))


def test_meeting_create_vote_finalize(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
    h = _post({"action": "create", "code": "002445.SZ", "topic": "测试",
               "asof": "2026-11-01"})
    A.handle_post(h)
    assert h.status == 200 and h.payload["ok"] is True
    mid = h.payload["meeting"]["meeting_id"]
    for seat in M.VOTERS:
        h = _post({"action": "vote", "meeting_id": mid, "seat": seat,
                   "tier": "BUY", "confidence": 0.7,
                   "rationale": "%s 看多理由" % seat, "evidence": ["bridge"]})
        A.handle_post(h)
        assert h.status == 200, h.payload
    h = _post({"action": "finalize", "meeting_id": mid, "tier": "BUY",
               "confidence": 0.78, "rationale": "四席汇总后 D1 最终买入观察",
               "evidence": ["bridge"], "dissent": ["T2 量能不足"]})
    A.handle_post(h)
    assert h.status == 200 and h.payload["ok"] is True
    assert h.payload["meeting"]["status"] == "finalized"
    assert h.payload["decision_candidate"]["tier"] == "BUY"


def test_meeting_api_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
    h = _post({"action": "nope"})
    A.handle_post(h)
    assert h.status == 400 and h.payload["error"] == "bad_action"
    h = _post({"action": "create"})  # no code
    A.handle_post(h)
    assert h.status == 400 and h.payload["error"] == "missing_code"


def test_meeting_get_summary(tmp_path, monkeypatch):
    monkeypatch.setattr(M, "DEFAULT_DIR", tmp_path / "meetings")
    M.create_meeting("002445.SZ", dirpath=tmp_path / "meetings")
    h = FakeHandler(path="/api/d1/meeting")
    A.handle_get(h, "")
    assert h.status == 200 and h.payload["ok"] is True
    assert h.payload["summary"]["total"] == 1
