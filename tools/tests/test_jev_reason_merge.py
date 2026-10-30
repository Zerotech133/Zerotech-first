# -*- coding: utf-8 -*-
"""BATCH4 回归：reason 并入本地判定调用（不再每条多调一次模型）。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

NC = Path(__file__).resolve().parents[2] / "news_collector"
if str(NC) not in sys.path:
    sys.path.insert(0, str(NC))

import news_ai  # noqa: E402


class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_local_judge_uses_reason_from_same_call(monkeypatch):
    payload = {"message": {"content": json.dumps({
        "bias": "bear", "conf": 0.9, "scope": "cn", "event_type": "risk",
        "impact": "high", "companies": [], "reason": "监管处罚直接利空",
    }, ensure_ascii=False)}}
    calls = {"n": 0}

    def fake_post(*args, **kwargs):
        calls["n"] += 1
        return _Resp(payload)

    monkeypatch.setattr(news_ai.requests, "post", fake_post)
    monkeypatch.setattr(news_ai, "jev_reason",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不应再单独调用")))
    r = news_ai.jev_judge_one_local("某公司被立案调查", [])
    assert r is not None
    assert r["reason"] == "监管处罚直接利空"
    assert calls["n"] == 1