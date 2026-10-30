# -*- coding: utf-8 -*-
"""诸葛亮 IDE 复核 P0 单测：默认关/校验/解析/异步/缓存。"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL))
import zhuge_review as z  # noqa: E402


PAYLOAD = {
    "task_id": "WO-TEST-1",
    "diff": "--- a/x.py\n+++ b/x.py\n+print('x')\n",
    "changed_files": ["x.py"],
    "test_result": {"ok": True, "passed": 1, "failed": 0, "output_tail": "ok"},
    "context": {"goal": "review test", "acceptance": ["no break"]},
}


def test_default_off(monkeypatch):
    monkeypatch.delenv(z.FEATURE_ENV, raising=False)
    assert z.enabled() is False
    status, out = z.start_review(PAYLOAD)
    assert status == 404 and out["error"] == "zhuge_review_disabled"


def test_validate_limits(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    bad = dict(PAYLOAD, diff="")
    status, out = z.start_review(bad)
    assert status == 400
    status, out = z.start_review(dict(PAYLOAD, diff="x" * (z.MAX_DIFF + 1)))
    assert status == 400


def test_parse_review():
    raw = '前言\n```json\n' + json.dumps({
        "verdict": "concerns",
        "findings": [{"severity": "P1", "file": "x.py", "line": 3,
                      "claim": "c", "evidence": "e", "fix": "f"}],
        "questions": ["q"],
    }, ensure_ascii=False) + '\n```'
    out = z.parse_review(raw)
    assert out["verdict"] == "concerns"
    assert out["findings"][0]["file"] == "x.py"
    assert out["questions"] == ["q"]


def test_parse_bad_degrades():
    out = z.parse_review("not json")
    assert out["verdict"] == "degraded"
    assert out["findings"] == []


def test_async_done_and_cache(monkeypatch):
    monkeypatch.setenv(z.FEATURE_ENV, "1")
    text = '{"verdict":"pass","findings":[],"questions":[]}'

    def fake_consult(prompt, timeout=90):
        assert "diff" in prompt
        return {"llm_ok": True, "text": text, "mode": "openclaw_cloud",
                "carrier": "deepseek/deepseek-chat", "sources": []}

    status, out = z.start_review(PAYLOAD, consult_fn=fake_consult)
    assert status == 202 and out["review_id"]
    rid = out["review_id"]
    for _ in range(50):
        got = z.get_review(rid)
        if got.get("status") != "pending":
            break
        time.sleep(0.02)
    assert got["status"] == "done"
    assert got["read_only"] is True
    assert got["verdict"] == "pass"

    status2, out2 = z.start_review(PAYLOAD, consult_fn=fake_consult)
    assert status2 == 200
    assert out2["cached"] is True
