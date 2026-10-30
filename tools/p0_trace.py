# -*- coding: utf-8 -*-
"""p0_trace.py — 诸葛亮能力底座 P0-6：Phoenix 本地 tracing。

设计：
- **懒加载 + 失败 no-op**：未设置 `PHOENIX_TRACE=1` 或 Phoenix 未安装/不可用时，
  traced_chat 退化为普通 HTTP 调用，**不影响业务**；
- 只 trace **8795 /llm/chat 客户端调用**：attributes = binding/model/carrier/relay/
  status/latency_ms/ok/error（服务器端 llm_gateway 侧 instrumentation 留待 P1）；
- 不常驻、不新增计划任务；本机起 Phoenix（6006）仅用于验证。

用法：
  PHOENIX_TRACE=1 python tools/p0_trace.py --selftest
  PHOENIX_TRACE=1 PHOENIX_ENDPOINT=http://127.0.0.1:6006/v1/traces python tools/p0_trace.py --selftest
"""
from __future__ import annotations

import argparse
import json
import os
import time
import urllib.request
# safe_path=true 时脚本目录不入 sys.path：把本树根加入 path（helper 同目录）
import sys as _sys
from pathlib import Path as _Path
for _p in _Path(__file__).resolve().parents:
    if (_p / "safe_urlopen.py").exists():
        _sys.path.insert(0, str(_p))
        break

try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包导入时
    try:
        from safe_urlopen import safe_urlopen
    except ImportError:  # 作为包/仓库根导入时
        from tools.safe_urlopen import safe_urlopen

DEFAULT_ENDPOINT = "http://127.0.0.1:8795/llm/chat"
DEFAULT_PHOENIX = "http://127.0.0.1:6006/v1/traces"
DEFAULT_SYSTEM = "你是诸葛亮（AI1），反向军师，零执行权。"
_TRACER = None
_INIT_ERROR = None


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def init_tracer(force: bool = False):
    """初始化 tracer：优先 Phoenix OTLP，失败回退 OTel console（--selftest 用）。

    未启用/失败返回 None（不抛）；全局 _INIT_ERROR 记录原因。
    """
    global _TRACER, _INIT_ERROR
    if _TRACER is not None:
        return _TRACER or None
    if not force and not (_truthy("PHOENIX_TRACE") or _truthy("OTEL_TRACE")):
        _TRACER = False
        return None
    try:
        from phoenix.otel import register  # type: ignore
        endpoint = os.environ.get("PHOENIX_ENDPOINT", DEFAULT_PHOENIX)
        project = os.environ.get("PHOENIX_PROJECT", "zhuge-p0")
        provider = register(project_name=project, endpoint=endpoint,
                            set_global_tracer_provider=True, auto_instrument=False)
        _TRACER = provider.get_tracer("zhuge-p0")
        _INIT_ERROR = None
        return _TRACER
    except Exception as exc:  # noqa: BLE001
        _INIT_ERROR = "phoenix unavailable: %s: %s" % (type(exc).__name__, str(exc)[:160])
    # 回退 1：标准 OTLP HTTP exporter（phoenix.otel 缺失时仍把 span 送到 Phoenix）
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        endpoint = os.environ.get("PHOENIX_ENDPOINT", DEFAULT_PHOENIX)
        project = os.environ.get("PHOENIX_PROJECT", "zhuge-p0")
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={"x-phoenix-project-name": project},
        )
        provider = TracerProvider(
            resource=Resource.create({"service.name": "zhuge-p0"})
        )
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        otel_trace.set_tracer_provider(provider)
        _TRACER = provider.get_tracer("zhuge-p0-otlp")
        _INIT_ERROR += " | using otlp http fallback"
        return _TRACER
    except Exception as exc:  # noqa: BLE001
        _INIT_ERROR += " | otlp fallback failed: %s: %s" % (
            type(exc).__name__, str(exc)[:120])
    # 回退 2：OpenTelemetry SDK + console exporter（仅证明 span 生成；不写远端）
    try:
        from opentelemetry import trace as otel_trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor, ConsoleSpanExporter
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
        otel_trace.set_tracer_provider(provider)
        _TRACER = provider.get_tracer("zhuge-p0-otel")
        return _TRACER
    except Exception as exc:  # noqa: BLE001
        _INIT_ERROR += " | otel fallback failed: %s: %s" % (
            type(exc).__name__, str(exc)[:120])
        _TRACER = False
        return None


def http_chat(binding: str, system: str, user: str, endpoint: str = DEFAULT_ENDPOINT,
              timeout: float = 120) -> dict:
    """调用 8795 /llm/chat；返回 {ok, data, elapsed_ms, error}。"""
    body = json.dumps({"binding": binding, "system": system, "user": user},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(endpoint, data=body, method="POST",
                                 headers={"Content-Type": "application/json; charset=utf-8"})
    t0 = time.time()
    try:
        with safe_urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return {"ok": bool(payload.get("ok")), "data": payload.get("data") or {},
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
                "error": payload.get("error")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "data": {},
                "elapsed_ms": round((time.time() - t0) * 1000, 1),
                "error": "%s: %s" % (type(exc).__name__, str(exc)[:200])}


def traced_chat(binding: str, user: str, system: str = DEFAULT_SYSTEM,
                endpoint: str = DEFAULT_ENDPOINT, timeout: float = 120,
                tracer=None) -> dict:
    """带 Phoenix span 的 8795 调用；无 tracer 时纯 HTTP（no-op）。"""
    tracer = tracer if tracer is not None else init_tracer()
    if tracer is None:
        return http_chat(binding, system, user, endpoint, timeout)
    with tracer.start_as_current_span("zhuge.llm.chat") as span:
        span.set_attribute("llm.binding", binding)
        span.set_attribute("llm.endpoint", endpoint)
        span.set_attribute("input.user_chars", len(user or ""))
        result = http_chat(binding, system, user, endpoint, timeout)
        data = result.get("data") or {}
        span.set_attribute("llm.carrier", data.get("carrier") or "")
        span.set_attribute("llm.relay", bool(data.get("relay")))
        span.set_attribute("llm.status", data.get("status") or "")
        span.set_attribute("llm.latency_ms", float(result.get("elapsed_ms") or 0))
        span.set_attribute("llm.ok", bool(result.get("ok")))
        if result.get("error"):
            span.set_attribute("llm.error", str(result["error"])[:300])
        return result


def _selftest() -> int:
    print("phoenix_enabled=", _truthy("PHOENIX_TRACE"),
          "otel_enabled=", _truthy("OTEL_TRACE"),
          "endpoint=", os.environ.get("PHOENIX_ENDPOINT", DEFAULT_PHOENIX))
    tracer = init_tracer(force=_truthy("PHOENIX_TRACE") or _truthy("OTEL_TRACE"))
    print("tracer=", bool(tracer), "init_error=", _INIT_ERROR)
    result = traced_chat("ollama/qwen3.5:9b", "用一句话回答：tracing 自检是否通过？",
                         tracer=tracer)
    print(json.dumps({"ok": result.get("ok"), "elapsed_ms": result.get("elapsed_ms"),
                      "carrier": (result.get("data") or {}).get("carrier"),
                      "relay": (result.get("data") or {}).get("relay"),
                      "error": result.get("error")}, ensure_ascii=False))
    return 0 if result.get("ok") else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="P0-6 Phoenix tracing")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--binding", default="ollama/qwen3.5:9b")
    ap.add_argument("--user", default="用一句话回答：tracing 自检是否通过？")
    args = ap.parse_args(argv)
    if args.selftest:
        return _selftest()
    result = traced_chat(args.binding, args.user)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
