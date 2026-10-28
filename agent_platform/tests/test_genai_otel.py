# -*- coding: utf-8 -*-
"""D3：OTel GenAI 适配层单测（默认关 no-op + 字段映射 + span 包装）。"""
import contextlib
import importlib.util
import pathlib

HERE = pathlib.Path(__file__).resolve().parents[1]  # agent_platform/
spec = importlib.util.spec_from_file_location(
    "obs_genai", HERE / "observability" / "genai.py")
genai = importlib.util.module_from_spec(spec)
spec.loader.exec_module(genai)


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("DSH_OTEL_ENABLED", raising=False)
    assert genai.enabled() is False
    llm = object()
    assert genai.wrap_llm(llm) is llm


def test_attrs_mapping():
    a = genai._attrs(model="deepseek-flash", provider="deepseek",
                     operation="chat", extra={"gen_ai.tool.name": "x", "skip": None})
    assert a["gen_ai.system"] == "deepseek"
    assert a["gen_ai.request.model"] == "deepseek-flash"
    assert a["gen_ai.operation.name"] == "chat"
    assert a["gen_ai.tool.name"] == "x"
    assert "skip" not in a


class _FakeSpan:
    def __init__(self):
        self.attributes = {}
        self.ended = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.ended = True


class _FakeTracer:
    def __init__(self):
        self.spans = []

    @contextlib.contextmanager
    def start_as_current_span(self, name, attributes=None):
        span = _FakeSpan()
        span.name = name
        span.attributes = dict(attributes or {})
        self.spans.append(span)
        try:
            yield span
        finally:
            span.ended = True


def test_wrap_llm_emits_span_when_enabled(monkeypatch):
    fake = _FakeTracer()
    monkeypatch.setenv("DSH_OTEL_ENABLED", "1")
    monkeypatch.setattr(genai, "_OTEL_AVAILABLE", True)
    monkeypatch.setattr(genai, "_TRACER", fake)
    monkeypatch.setattr(genai, "enabled", lambda: True)

    class Dummy:
        model = "qwen3.5:9b"
        provider = "ollama"

        def chat(self, system="", user=""):
            return "回答"

    wrapped = genai.wrap_llm(Dummy())
    assert wrapped is not None
    assert wrapped.chat("s", "u") == "回答"
    assert len(fake.spans) == 1
    span = fake.spans[0]
    assert span.attributes["gen_ai.system"] == "ollama"
    assert span.attributes["gen_ai.request.model"] == "qwen3.5:9b"
    assert span.attributes["gen_ai.operation.name"] == "chat"
    assert span.ended is True
