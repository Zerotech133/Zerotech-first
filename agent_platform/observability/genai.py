# -*- coding: utf-8 -*-
"""Agent 可观测：OpenTelemetry GenAI 语义约定适配层（默认关闭）。

设计（ADR-005）：
  · 默认完全 no-op：仅当 `DSH_OTEL_ENABLED=1` 且本机装了 opentelemetry-api 才生效；
  · 字段按 OTel `gen_ai.*` semconv（system / request.model / operation.name /
    response.model / usage.input_tokens / usage.output_tokens / tool.name）；
  · 通过 wrap_llm() 在 `engine.llm.make_llm` 单点包装，所有 provider 自动获得 span；
  · 失败绝不反噬主链路：任何异常都吞掉并返回原对象/空 span；
  · 后续可接 Phoenix/Langfuse/Datadog（见 docs/specs/GenAI可观测字段映射）。
"""
from __future__ import annotations

import os
from contextlib import contextmanager, nullcontext
import logging

_TRUE = ("1", "true", "yes", "on")
_TRACER = None
_OTEL_AVAILABLE = None


def enabled() -> bool:
    """是否启用 OTel span（默认关；env 开关 + 依赖可用）。"""
    if str(os.environ.get("DSH_OTEL_ENABLED") or "").strip().lower() not in _TRUE:
        return False
    global _OTEL_AVAILABLE
    if _OTEL_AVAILABLE is None:
        try:
            import opentelemetry.trace  # noqa: F401
            _OTEL_AVAILABLE = True
        except Exception:  # noqa: BLE001
            _OTEL_AVAILABLE = False
    return _OTEL_AVAILABLE


def _attrs(model="", provider="", operation="chat", extra=None):
    """构造 GenAI semconv 属性（纯函数，便于单测）。"""
    a = {
        "gen_ai.system": provider or "unknown",
        "gen_ai.operation.name": operation or "chat",
    }
    if model:
        a["gen_ai.request.model"] = str(model)
    if extra:
        for k, v in extra.items():
            if v is not None:
                a[str(k)] = v
    return a


def _get_tracer():
    global _TRACER
    if _TRACER is None:
        try:
            from opentelemetry import trace
            _TRACER = trace.get_tracer("agent_platform.genai")
        except Exception:  # noqa: BLE001
            _TRACER = False
    return _TRACER or None


@contextmanager
def llm_span(model="", provider="", operation="chat", extra=None):
    """LLM 调用 span；未启用/出错时退化为 nullcontext（不改变行为）。"""
    if not enabled():
        with nullcontext():
            yield None
        return
    tracer = _get_tracer()
    if tracer is None:
        with nullcontext():
            yield None
        return
    attrs = _attrs(model, provider, operation, extra)
    try:
        with tracer.start_as_current_span("chat %s" % (model or "llm"),
                                          attributes=attrs) as span:
            yield span
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/observability/genai.py", exc)


class _InstrumentedLLM:
    """LLM 代理：chat 外面包 span；其余属性透传。"""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def chat(self, *args, **kwargs):
        model = getattr(self._inner, "model", None) or kwargs.get("model") or ""
        provider = getattr(self._inner, "provider", None) or ""
        with llm_span(model=model, provider=provider, operation="chat"):
            return self._inner.chat(*args, **kwargs)


def wrap_llm(llm):
    """默认关时原样返回；开启时返回轻量代理（不改原对象）。"""
    if not enabled():
        return llm
    try:
        return _InstrumentedLLM(llm)
    except Exception:  # noqa: BLE001
        return llm
