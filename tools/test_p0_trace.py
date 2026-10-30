# -*- coding: utf-8 -*-
"""p0_trace 单测（P0-6）：离线、无 Phoenix 依赖。"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import p0_trace as t  # noqa: E402


class FakeCtx:
    def __init__(self):
        self.attrs = {}
        self.status = None
        self.exceptions = []

    def set_attribute(self, key, value):
        self.attrs[key] = value

    def set_status(self, status):
        self.status = status

    def record_exception(self, exc):
        self.exceptions.append(exc)


class FakeTracer:
    def __init__(self):
        self.spans = []

    def start_as_current_span(self, name):
        tracer = self

        class Ctx:
            def __enter__(self_inner):
                self_inner.span = FakeCtx()
                tracer.spans.append((name, self_inner.span))
                return self_inner.span

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        return Ctx()


def test_traced_chat_noop_calls_http(monkeypatch):
    calls = {}

    def fake_http(binding, system, user, endpoint=t.DEFAULT_ENDPOINT, timeout=120):
        calls.update(binding=binding, user=user)
        return {"ok": True, "data": {"carrier": "none"}, "elapsed_ms": 1.0, "error": None}

    monkeypatch.setattr(t, "http_chat", fake_http)
    res = t.traced_chat("ollama/x", "hi", tracer=None)
    assert res["ok"] is True and calls["binding"] == "ollama/x"


def test_traced_chat_with_fake_tracer(monkeypatch):
    monkeypatch.setattr(t, "http_chat", lambda *a, **k: {
        "ok": True,
        "data": {"carrier": "deepseek/deepseek-chat", "relay": True, "status": "normal"},
        "elapsed_ms": 12.3, "error": None})
    tracer = FakeTracer()
    res = t.traced_chat("openai/gpt-6.0", "hi", tracer=tracer)
    assert res["ok"] is True
    assert len(tracer.spans) == 1
    name, attrs = tracer.spans[0]
    assert name == "zhuge.llm.chat"
    assert attrs.attrs["llm.binding"] == "openai/gpt-6.0"
    assert attrs.attrs["llm.carrier"] == "deepseek/deepseek-chat"
    assert attrs.attrs["llm.relay"] is True
    assert attrs.attrs["llm.latency_ms"] == 12.3


def test_http_chat_error_no_throw(monkeypatch):
    def boom(*a, **k):
        raise OSError("down")

    monkeypatch.setattr(t.urllib.request, "urlopen", boom)
    res = t.http_chat("ollama/x", "", "u")
    assert res["ok"] is False and "down" in res["error"]


def test_init_tracer_disabled(monkeypatch):
    monkeypatch.setattr(t, "_TRACER", None)
    monkeypatch.delenv("PHOENIX_TRACE", raising=False)
    assert t.init_tracer() is None


def test_traced_chat_marks_error_span(monkeypatch):
    monkeypatch.setattr(t, "http_chat", lambda *a, **k: {
        "ok": False, "data": {}, "elapsed_ms": 5.0, "error": "TimeoutError: down"})
    tracer = FakeTracer()
    res = t.traced_chat("ollama/x", "hi", tracer=tracer)
    assert res["ok"] is False
    _, attrs = tracer.spans[0]
    assert attrs.status is not None
    assert attrs.exceptions


def test_main_returns_nonzero_on_failure(monkeypatch):
    monkeypatch.setattr(t, "traced_chat", lambda *a, **k: {
        "ok": False, "data": {}, "elapsed_ms": 1.0, "error": "down"})
    assert t.main([]) == 1
