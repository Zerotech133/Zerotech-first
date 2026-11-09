# -*- coding: utf-8 -*-
"""W6 watch_alerts Feishu sink tests (offline; no network)."""
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent_platform import watch_alert_feishu as F  # noqa: E402


EVENT = {
    "symbol": "001872.SZ", "action": "WATCH", "plan_id": "wp-1",
    "observed": "25.20", "threshold": "25.30", "quote_as_of": "2026-11-04T14:00:00+08:00",
}


def test_format_contains_no_token():
    text = F._format(EVENT)
    assert "001872.SZ" in text and "WATCH" in text and "paper_only" in text
    assert "token" not in text.lower()


def test_sink_missing_target(monkeypatch):
    monkeypatch.setattr(F, "_target_id", lambda name: None)
    result = F.make_feishu_sink("内部测试")(EVENT)
    assert result["status"] == "failed"
    assert result["reason"] == "target_not_found"


def test_sink_sent(monkeypatch):
    monkeypatch.setattr(F, "_target_id", lambda name: "chat_test")
    sent = {}
    class S:
        @staticmethod
        def send_content_to(tid, typ, text, msg_type, kind=None):
            sent.update({"tid": tid, "typ": typ, "kind": kind, "text": text})
            return True
    monkeypatch.setitem(sys.modules, "send_feishu", S)
    result = F.make_feishu_sink("内部测试")(EVENT)
    assert result["status"] == "sent"
    assert sent["typ"] == "chat_id"
    assert sent["kind"] == "watch:alert"


def test_sink_send_false(monkeypatch):
    monkeypatch.setattr(F, "_target_id", lambda name: "chat_test")
    class S:
        @staticmethod
        def send_content_to(*a, **k):
            return False
    monkeypatch.setitem(sys.modules, "send_feishu", S)
    assert F.make_feishu_sink()(EVENT)["status"] == "failed"
