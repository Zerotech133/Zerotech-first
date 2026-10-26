# -*- coding: utf-8 -*-
"""模型路由（model-routing）：按能力/席位给出有序模型链，并在 429/不可用时降级。

路由表（默认，可被调用方 prefer 覆盖）：
  · governance / 日常治理归纳：deepseek-flash → deepseek-v4-pro → ollama/qwen3.5:9b
  · deep-reasoning / 复杂判读：deepseek-v4-pro → deepseek-flash → ollama/35b（若已起）
  · local-only / 离线/降级：ollama/qwen3.5:9b
  · glm 通道：glm/glm-5.3-flash（**配额风险**；429 自动降级，不丢任务）

本模块只做"选路 + 调用 + 留证"：不写 org/bundle 真源、不自动切全局默认。
调用记录落 `agent_platform/data/zhaoyun_capability_usage.jsonl`（drill-evidence）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

PLATFORM = Path(__file__).resolve().parents[1]
REPO = PLATFORM.parent
USAGE_LOG = PLATFORM / "data" / "zhaoyun_capability_usage.jsonl"

ROUTES = {
    "governance": ["deepseek/deepseek-flash", "deepseek/deepseek-v4-pro",
                   "ollama/qwen3.5:9b"],
    "deep-reasoning": ["deepseek/deepseek-v4-pro", "deepseek/deepseek-flash",
                       "ollama/qwen3.5:9b"],
    "local-only": ["ollama/qwen3.5:9b"],
    "glm-quota": ["deepseek/deepseek-flash", "glm/glm-5.3-flash",
                  "ollama/qwen3.5:9b"],
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
        out.append({"model": m, "rank": i + 1,
                    "why": "primary" if i == 0 else "fallback",
                    "risk": risk})
    return out


def _default_adapters() -> dict:
    return {"call": _call_model}


def _call_model(model: str, messages: list, max_tokens: int = 256) -> str:
    """真实调用一个模型；异常抛出由 invoke 捕获后降级。"""
    if model.startswith("deepseek/"):
        sys.path.insert(0, str(PLATFORM))
        from engine import deepseek  # noqa: WPS433
        m = model.split("/", 1)[1]
        return str(deepseek.DeepSeekClient().chat(messages, model=m,
                                                  max_tokens=max_tokens) or "")
    if model.startswith("ollama/"):
        sys.path.insert(0, str(PLATFORM))
        from engine.llm import make_llm  # noqa: WPS433
        llm = make_llm(model)
        return str(llm.chat(messages) or "")
    raise RuntimeError("unsupported_model:%s" % model)


def invoke(messages: list, purpose: str = "governance", prefer: list | None = None,
           max_tokens: int = 256, adapters: dict | None = None) -> dict:
    """按路由链依次尝试；成功即返回并留证；全失败返回 ok=False + attempts。"""
    adapters = adapters or _default_adapters()
    plan = route(purpose, prefer)
    attempts = []
    for item in plan:
        model = item["model"]
        t0 = time.time()
        try:
            text = adapters["call"](model, messages, max_tokens)
            if not text:
                raise RuntimeError("empty_reply")
            rec = {"ok": True, "model": model, "used": True,
                   "elapsed_ms": int((time.time() - t0) * 1000),
                   "attempts": attempts, "text": text}
            _log_usage("model-routing", purpose, model, True)
            return rec
        except Exception as e:  # noqa: BLE001
            attempts.append({"model": model, "error": "%s: %s"
                             % (type(e).__name__, str(e)[:120]),
                             "elapsed_ms": int((time.time() - t0) * 1000)})
            _log_usage("model-routing", purpose, model, False)
    return {"ok": False, "model": None, "used": False, "attempts": attempts}


def _log_usage(capability: str, purpose: str, model: str, ok: bool) -> None:
    try:
        USAGE_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
               "capability": capability, "purpose": purpose,
               "model": model, "ok": bool(ok)}
        with open(USAGE_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def usage_summary(limit: int = 20) -> dict:
    rows = []
    try:
        lines = USAGE_LOG.read_text(encoding="utf-8", errors="replace").splitlines()
        for ln in lines[-limit:]:
            try:
                rows.append(json.loads(ln))
            except ValueError:
                continue
    except OSError:
        pass
    return {"ok": True, "count": len(rows), "items": rows}
