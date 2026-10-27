# -*- coding: utf-8 -*-
"""OTEL 追踪助手（fail-open，默认关闭）：给关键链路加轻量 trace，不改变业务行为。

启用条件（二选一，默认全关）：
  OTEL_ENABLED=1 且设置 OTEL_EXPORTER_OTLP_ENDPOINT（默认 http://127.0.0.1:6006/v1/traces）
  （Phoenix 1.43GB 镜像实测：OTLP HTTP 端点在 6006/v1/traces；gRPC 4317）

设计原则：
- **默认 no-op**：未启用时零依赖、零开销、零行为变化；
- **fail-open**：OTEL 包缺失/导出失败/配置错误一律静默降级，绝不让追踪拖垮主流程；
- 只做薄封装：`traced(name, attrs...)` 上下文管理器 + `instrument(main)` 装饰器。

用法：
    from otel_tracing import traced
    with traced("datawarehouse.run_all", mode="daily"):
        payload = collect(mode)
"""
from __future__ import annotations

import contextlib
import functools
import os
import sys
from typing import Any, Callable
import logging

ENV_ENABLED = "OTEL_ENABLED"
ENV_ENDPOINT = "OTEL_EXPORTER_OTLP_ENDPOINT"
ENV_SERVICE = "OTEL_SERVICE_NAME"
DEFAULT_ENDPOINT = "http://127.0.0.1:6006/v1/traces"
DEFAULT_SERVICE = "zerobond"

_state: dict[str, Any] = {"init": None, "tracer": None, "error": None}


def enabled() -> bool:
    """是否启用追踪：OTEL_ENABLED=1 且未显式关闭。"""
    v = (os.environ.get(ENV_ENABLED) or "").strip().lower()
    return v in ("1", "true", "yes", "on")


def _init():
    """惰性初始化（只成功一次）；失败则永久降级 no-op，并缓存错误原因。"""
    if _state["init"] is not None:
        return _state["tracer"]
    _state["init"] = False
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        endpoint = (os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT).strip()
        service = (os.environ.get(ENV_SERVICE) or DEFAULT_SERVICE).strip()
        provider = TracerProvider(resource=Resource.create({"service.name": service}))
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint)
        except Exception:  # noqa: BLE001 - 无 http exporter 时退回 grpc
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            exporter = OTLPSpanExporter(endpoint=endpoint.replace("/v1/traces", ""))
        provider.add_span_processor(BatchSpanProcessor(exporter))
        trace.set_tracer_provider(provider)
        _state["tracer"] = trace.get_tracer("zerobond.tools")
        _state["init"] = True
    except Exception as e:  # noqa: BLE001 - fail-open
        _state["tracer"] = None
        _state["error"] = "%s: %s" % (type(e).__name__, str(e)[:160])
    return _state["tracer"]


@contextlib.contextmanager
def traced(name: str, **attrs):
    """上下文管理器：启用时创建 span 并附加属性；未启用/失败时为空操作。"""
    if not enabled():
        yield None
        return
    tr = _init()
    if tr is None:
        yield None
        return
    try:
        with tr.start_as_current_span(name) as span:
            for k, v in attrs.items():
                try:
                    span.set_attribute(k, v if isinstance(v, (str, int, float, bool)) else str(v)[:200])
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "tools/otel_tracing.py", exc)
            yield span
    except Exception:  # noqa: BLE001 - 不吞业务异常，仅重新抛出
        raise


def instrument(fn: Callable | None = None, *, name: str = ""):
    """装饰器：给函数加 span；未启用时原样返回函数。"""
    def deco(f):
        span_name = name or ("%s.%s" % (f.__module__.split(".")[-1], f.__name__))

        @functools.wraps(f)
        def wrapper(*args, **kwargs):
            if not enabled():
                return f(*args, **kwargs)
            with traced(span_name):
                return f(*args, **kwargs)
        return wrapper
    return deco(fn) if fn is not None else deco


def status() -> dict:
    """诊断用途：当前是否启用/初始化是否成功/错误原因（不含敏感信息）。"""
    return {"enabled": enabled(), "initialized": bool(_state["init"]),
            "endpoint": os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT,
            "service": os.environ.get(ENV_SERVICE) or DEFAULT_SERVICE,
            "error": _state["error"]}


if __name__ == "__main__":
    sys.stdout.write("otel_tracing " + str(status()) + "\n")
