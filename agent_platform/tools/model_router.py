# -*- coding: utf-8 -*-
"""模型路由（model-routing）：按能力/席位给出有序模型链，并在 429/不可用时降级。

路由表（默认，可被调用方 prefer 覆盖）：
  · governance / 日常治理归纳：deepseek-flash → deepseek-v4-pro → ollama/qwen3.5:9b
  · deep-reasoning / 复杂判读：deepseek-v4-pro → deepseek-flash → ollama/35b（若已起）
  · local-only / 离线/降级：ollama/qwen3.5:9b
  · glm 通道：glm/glm-5.3-flash（**配额风险**；429 自动降级，不丢任务）
  · dev-lead / dev-exec（研发部新链）：dev-lead=E3 组长/主官链（glm/glm-5.3 优先，
    429/不可用时自动降级到 deepseek-flash → 本地 9b）；dev-exec=E2/E1 执行席快档链
    （glm/glm-5.3-flash 优先，同链降级）。
  · glm 无 adapter 时会抛 unsupported_model：invoke 已按路由链 fail 到下一档（不丢任务）。

本模块只做"选路 + 调用 + 留证"：不写 org/bundle 真源、不自动切全局默认。
调用记录落 `agent_platform/data/zhaoyun_capability_usage.jsonl`（drill-evidence）。
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path
import logging

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
USAGE_LOG = PLATFORM / "data" / "zhaoyun_capability_usage.jsonl"
# 测试重定向开关：非 None 时 _record_llm 把 llm.call 写到此库（而非生产 traces.sqlite），
# 同时给 span 打 zhaoyun.test=true 标记，供 summary() 排除。生产保持 None。
LLM_TRACE_DB = None

ROUTES = {
    "governance": [
        "deepseek/deepseek-flash",
        "deepseek/deepseek-v4-pro",
        "ollama/qwen3.5:9b",
    ],
    "deep-reasoning": [
        "deepseek/deepseek-v4-pro",
        "deepseek/deepseek-flash",
        "ollama/qwen3.5:9b",
    ],
    "local-only": ["ollama/qwen3.5:9b"],
    "glm-quota": ["deepseek/deepseek-flash", "glm/glm-5.3-flash", "ollama/qwen3.5:9b"],
    # 研发部指挥链（C4）：E3 组长/主官 glm 强档优先，429/不可用逐档降级
    "dev-lead": ["glm/glm-5.3", "deepseek/deepseek-flash", "ollama/qwen3.5:9b"],
    # 研发部执行链（C4）：E2/E1 执行席 glm 快档优先，同链降级
    "dev-exec": ["glm/glm-5.3-flash", "deepseek/deepseek-flash", "ollama/qwen3.5:9b"],
}


def route(purpose: str = "governance", prefer: list | None = None) -> list:
    """返回有序模型链 [{"model","why","risk"}]。"""
    base = list(ROUTES.get(purpose) or ROUTES["governance"])
    if prefer:
        for m in reversed([str(x) for x in prefer if x]):
            if m in base:
                base.remove(m)
            base.insert(0, m)
    out = []
    for i, m in enumerate(base):
        risk = ""
        if m.startswith("glm/"):
            risk = "quota_429_risk"
        if m.startswith("ollama/"):
            risk = risk or "local_9b_degraded"
        out.append(
            {
                "model": m,
                "rank": i + 1,
                "why": "primary" if i == 0 else "fallback",
                "risk": risk,
            }
        )
    return out


def _default_adapters() -> dict:
    return {"call": _call_model}


def _call_model(model: str, messages: list, max_tokens: int = 256):
    """真实调用一个模型；返回 str（无 usage）或 {"text","usage"}。"""
    if model.startswith("deepseek/"):
        sys.path.insert(0, str(PLATFORM))
        from engine import deepseek  # noqa: WPS433

        m = model.split("/", 1)[1]
        full = deepseek.DeepSeekClient().chat_full(
            messages, model=m, max_tokens=max_tokens
        )
        return {
            "text": str(full.get("content") or ""),
            "usage": full.get("usage") or {},
        }
    if model.startswith("ollama/"):
        sys.path.insert(0, str(PLATFORM))
        from engine.llm import make_llm  # noqa: WPS433

        llm = make_llm(model)
        return str(llm.chat(messages) or "")
    raise RuntimeError("unsupported_model:%s" % model)


def _usage_tokens(usage: dict | None, messages: list, text: str) -> dict:
    """优先用 provider usage；缺失时按字符数/4 估算并标注来源。"""
    u = usage or {}
    pin = u.get("prompt_tokens") or u.get("input_tokens") or 0
    pout = u.get("completion_tokens") or u.get("output_tokens") or 0
    source = "provider"
    try:
        pin, pout = int(pin), int(pout)
    except (TypeError, ValueError):
        pin, pout = 0, 0
    if pin <= 0:
        pin = max(1, sum(len(str(m.get("content") or "")) for m in messages) // 4)
        source = "estimated"
    if pout <= 0:
        pout = max(1, len(str(text or "")) // 4)
        source = "estimated"
    return {"input": pin, "output": pout, "source": source, "total": pin + pout}


def invoke(
    messages: list,
    purpose: str = "governance",
    prefer: list | None = None,
    max_tokens: int = 256,
    adapters: dict | None = None,
) -> dict:
    """按路由链依次尝试；成功即返回并留证；全失败返回 ok=False + attempts。"""
    adapters = adapters or _default_adapters()
    plan = route(purpose, prefer)
    attempts = []
    request_id = uuid.uuid4().hex[:16]  # 关联同一次 invoke 的多次尝试，供请求级度量
    for item in plan:
        model = item["model"]
        t0 = time.time()
        try:
            raw = adapters["call"](model, messages, max_tokens)
            if isinstance(raw, dict):
                text = str(raw.get("text") or "")
                usage = raw.get("usage") or {}
            else:
                text = str(raw or "")
                usage = {}
            if not text:
                raise RuntimeError("empty_reply")
            tokens = _usage_tokens(usage, messages, text)
            rec = {
                "ok": True,
                "model": model,
                "used": True,
                "elapsed_ms": int((time.time() - t0) * 1000),
                "attempts": attempts,
                "text": text,
                "usage": tokens,
            }
            _log_usage("model-routing", purpose, model, True)
            _record_llm(model, purpose, True, t0, tokens=tokens, text=text,
                        request_id=request_id)
            return rec
        except Exception as e:  # noqa: BLE001
            attempts.append(
                {
                    "model": model,
                    "error": "%s: %s" % (type(e).__name__, str(e)[:120]),
                    "elapsed_ms": int((time.time() - t0) * 1000),
                }
            )
            _log_usage("model-routing", purpose, model, False)
            _record_llm(model, purpose, False, t0, err=str(e), request_id=request_id)
    return {"ok": False, "model": None, "used": False, "attempts": attempts}


def _record_llm(
    model: str,
    purpose: str,
    ok: bool,
    t0: float,
    err: str = "",
    tokens: dict | None = None,
    text: str = "",
    request_id: str = "",
    db: str | None = None,
) -> None:
    """LLM 调用留证（trace span，OTel GenAI 字段；成本由 usage 估算）。

    db=None 走模块默认 LLM_TRACE_DB（生产库）；测试传临时库隔离。
    request_id 关联同一次 invoke 的多次尝试，供 summary 计算请求级失败率。
    """
    try:
        sys.path.insert(0, str(PLATFORM))
        from observability import trace as tr  # noqa: WPS433

        target_db = db if db is not None else LLM_TRACE_DB
        is_test = bool(target_db)
        attrs = {
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": model,
            "zhaoyun.purpose": purpose,
            "zhaoyun.request_id": str(request_id or ""),
            "zhaoyun.test": "true" if is_test else "false",
            "error": str(err or "")[:120],
        }
        if tokens:
            attrs.update(
                {
                    "gen_ai.usage.input_tokens": int(tokens.get("input") or 0),
                    "gen_ai.usage.output_tokens": int(tokens.get("output") or 0),
                    "zhaoyun.usage_source": str(tokens.get("source") or "estimated"),
                    "zhaoyun.response_chars": len(str(text or "")),
                }
            )
        tr.record(
            tr.trace_id(),
            "llm.call",
            "llm",
            start=t0,
            end=time.time(),
            status="ok" if ok else "error",
            attrs=attrs,
            db=target_db,
        )
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/model_router.py", exc)


def _log_usage(capability: str, purpose: str, model: str, ok: bool) -> None:
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "capability": capability,
            "purpose": purpose,
            "model": model,
            "ok": bool(ok),
        }
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/model_router.py", exc)


def usage_summary(limit: int = 20) -> dict:
    rows = []
    try:
        lines = USAGE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-limit:]:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/tools/model_router.py", exc)
    return {"ok": True, "count": len(rows), "items": rows}
