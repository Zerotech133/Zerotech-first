# -*- coding: utf-8 -*-
"""军团对外信封规约（WO-LEGION-S21）。

所有军团门面（gateway）出入参统一用本信封，保证外部板块（如飞书中台）
只依赖这一层稳定结构，不感知军团内部模块变化。

信封字段（v1）：
  schema_version : "1.0" 固定
  ok             : bool —— 业务成功与否（降级不算失败）
  kind           : str  —— 业务类型（llm_chat / inbox_submit / inbox_receipt）
  data           : dict —— 业务负载（成功时）
  error          : dict —— 失败信息 {code, message}（ok=false 时）
  degraded       : bool —— 底层是否走了降级路径（llm 专用，默认 False）
  latency_ms     : int  —— 门面层耗时
  ts             : str  —— ISO8601 本地时区时间戳

校验规则（validate_envelope）：
  必填键齐全；schema_version=="1.0"；ok=True 时 data 必为 dict 且 error 为空；
  ok=False 时 error 必为含 code/message 的 dict；latency_ms 非负整数。
"""

from __future__ import annotations

import time
from datetime import datetime

SCHEMA_VERSION = "1.0"

REQUIRED_KEYS = ("schema_version", "ok", "kind", "data", "error", "ts")
ERROR_KEYS = ("code", "message")


class EnvelopeError(ValueError):
    """信封不合规（供调用方与测试断言）。"""


def make_envelope(*, kind, ok, data=None, error=None, degraded=False, latency_ms=0):
    """构造标准信封。ok 与 error/data 的自洽性由 validate 兜底。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "ok": bool(ok),
        "kind": kind,
        "data": data or {},
        "error": error or {},
        "degraded": bool(degraded),
        "latency_ms": int(latency_ms),
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def validate_envelope(env) -> None:
    """校验信封；不合规抛 EnvelopeError（fail-visible，不静默容忍）。"""
    if not isinstance(env, dict):
        raise EnvelopeError("envelope must be dict")
    for key in REQUIRED_KEYS:
        if key not in env:
            raise EnvelopeError("missing key: %s" % key)
    if env["schema_version"] != SCHEMA_VERSION:
        raise EnvelopeError("schema_version mismatch: %r" % env["schema_version"])
    if env["ok"]:
        if env["error"]:
            raise EnvelopeError("ok=True but error non-empty")
        if not isinstance(env["data"], dict):
            raise EnvelopeError("ok=True requires dict data")
    else:
        if not isinstance(env["error"], dict) or not env["error"]:
            raise EnvelopeError("ok=False requires error dict")
        for key in ERROR_KEYS:
            if not env["error"].get(key):
                raise EnvelopeError("error missing field: %s" % key)
    if not isinstance(env["latency_ms"], int) or env["latency_ms"] < 0:
        raise EnvelopeError("latency_ms must be non-negative int")


def timed(fn):
    """门面方法计时装饰器：自动注入 latency_ms（毫秒）。"""

    def wrapper(*args, **kwargs):
        t0 = time.monotonic()
        env = fn(*args, **kwargs)
        env["latency_ms"] = int((time.monotonic() - t0) * 1000)
        return env

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper
