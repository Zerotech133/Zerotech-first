# -*- coding: utf-8 -*-
"""D1 decision API 契约测试（WO-D1-DECISION-20261101）。离线，不写生产账。"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
for p in (ROOT, ROOT / "portal"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from agent_platform import d1_decision as D  # noqa: E402
import api_d1_decision as A  # noqa: E402


class FakeHandler:
    def __init__(self, body, path="/api/d1/decision"):
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.status = None
        self.payload = None

    def _json(self, obj, code=200):
        self.status = code
        self.payload = obj


def _body(**kw):
    p = {
        "code": "002445.SZ",
        "tier": "BUY",
        "confidence": 0.7,
        "rationale": "3买回抽不破中枢上沿，paper-only 观察",
        "evidence": ["bridge"],
        "paper_only": True,
    }
    p.update(kw)
    return json.dumps(p, ensure_ascii=False).encode("utf-8")


def test_api_accepts_valid_decision(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
    h = FakeHandler(_body())
    A.handle_post(h)
    assert h.status == 200
    assert h.payload["ok"] is True
    assert h.payload["decision"]["tier"] == "BUY"
    assert h.payload["paper_only"] is True
    assert (tmp_path / "d1.jsonl").exists()


def test_api_rejects_bad_tier(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
    h = FakeHandler(_body(tier="WATCH"))
    A.handle_post(h)
    assert h.status == 400
    assert h.payload["error"] == "bad_tier"


def test_api_rejects_execution_field(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
    h = FakeHandler(_body(quantity=100))
    A.handle_post(h)
    assert h.status == 400
    assert h.payload["error"] == "execution_field_forbidden"


def test_api_wrong_path_404():
    h = FakeHandler(_body(), path="/api/d1/other")
    A.handle_post(h)
    assert h.status == 404


def test_api_get_decisions(tmp_path, monkeypatch):
    monkeypatch.setattr(D, "DEFAULT_LEDGER", tmp_path / "d1.jsonl")
    h = FakeHandler(_body(), path="/api/d1/decision")
    A.handle_post(h)
    assert h.status == 200
    g = FakeHandler(b"", path="/api/d1/decisions")
    A.handle_get(g, "")
    assert g.status == 200 and g.payload["ok"] is True
    assert "002445.SZ" in g.payload["latest_by_code"]
    g2 = FakeHandler(b"", path="/api/d1/decisions")
    A.handle_get(g2, "code=002445.SZ")
    assert g2.status == 200 and g2.payload["latest"]["tier"] == "BUY"
