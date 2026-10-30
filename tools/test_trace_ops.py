# -*- coding: utf-8 -*-
"""trace_ops 单测：汇总/告警，不打 Phoenix。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))
import trace_ops as t  # noqa: E402


def test_summarize_and_check():
    spans = [
        {"name": "zhuge.llm.chat", "start_time": "2026-10-30T10:00:00+00:00",
         "end_time": "2026-10-30T10:00:01+00:00", "status_code": "UNSET",
         "attributes": {"llm.ok": True}},
        {"name": "zhuge.llm.chat", "start_time": "2026-10-30T10:00:00+00:00",
         "end_time": "2026-10-30T10:00:03+00:00", "status_code": "ERROR",
         "attributes": {"llm.ok": False}},
    ]
    s = t.summarize(spans)
    assert s["count"] == 2 and s["errors"] == 1
    assert s["error_rate"] == 0.5
    assert s["latency_ms"]["p50"] == 1000.0
    assert t.check(s)["ok"] is False


def test_empty_data_is_high_alert(monkeypatch):
    monkeypatch.setattr(t, "fetch_spans", lambda *a, **k: {"ok": True, "status": 200, "spans": []})
    out = t.status(match="")
    assert out["count"] == 0
    assert out["ok"] is False
    assert any(a["code"] == "no_trace_data" for a in out["alerts"])
