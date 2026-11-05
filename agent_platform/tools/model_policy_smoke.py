# -*- coding: utf-8 -*-
"""Phase 0：model_policy.yaml 的模型冒烟（只读，不写注册表/DB）。

测试每个 primary/fallback binding 能否在短 prompt 下返回非空文本：
- 云端：gateway.llm_gateway.chat
- 本地 ollama：直接 /api/chat（num_predict=400；qwen3 thinking 模式需要足够 token）

输出：research/reports/model_policy_smoke_20261104.json
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import yaml

PLATFORM = Path(__file__).resolve().parents[1]
if str(PLATFORM) not in sys.path:
    sys.path.insert(0, str(PLATFORM))
POLICY = PLATFORM / "registry" / "model_policy.yaml"
OUT = PLATFORM.parent / "research" / "reports" / "model_policy_smoke_20261104.json"
OLLAMA = "http://127.0.0.1:11434"


def _chat(binding: str, prompt: str = "只回复 OK") -> dict:
    t0 = time.time()
    try:
        from gateway import llm_gateway
        env = llm_gateway.chat(binding=binding, system="你是模型冒烟测试助手。", user=prompt)
        text = str((env.get("data") or {}).get("text") or "")
        ok = bool(env.get("ok")) and bool(text.strip())
        return {"binding": binding, "ok": ok, "status": (env.get("data") or {}).get("status"),
                "text_head": text[:80], "error": (env.get("error") or {}).get("message"),
                "latency_sec": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"binding": binding, "ok": False, "status": "exception",
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "latency_sec": round(time.time() - t0, 2)}


def _ollama_chat(model: str, prompt: str = "只回复 OK") -> dict:
    t0 = time.time()
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"num_predict": 400, "temperature": 0},
    }).encode("utf-8")
    try:
        req = urllib.request.Request(OLLAMA + "/api/chat", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=240) as r:
            d = json.load(r)
        text = str(((d.get("message") or {}).get("content") or "")).strip()
        return {"binding": f"ollama/{model}", "ok": bool(text), "status": "normal",
                "text_head": text[:80], "latency_sec": round(time.time() - t0, 2)}
    except Exception as exc:  # noqa: BLE001
        return {"binding": f"ollama/{model}", "ok": False, "status": "exception",
                "error": f"{type(exc).__name__}: {exc}"[:200],
                "latency_sec": round(time.time() - t0, 2)}


def main() -> int:
    policy = yaml.safe_load(POLICY.read_text(encoding="utf-8"))
    bindings = []
    for pol in (policy.get("roles") or {}).values():
        for key in ("primary", "fallback"):
            b = pol.get(key)
            if b and b not in bindings:
                bindings.append(b)
    local = (policy.get("bindings") or {}).get("local")
    if local and local not in bindings:
        bindings.append(local)
    results = []
    for b in bindings:
        if b.startswith("ollama/"):
            results.append(_ollama_chat(b.split("/", 1)[1]))
        else:
            results.append(_chat(b))
    report = {
        "policy": str(POLICY),
        "tested_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "results": results,
        "all_ok": all(x.get("ok") for x in results),
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["all_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
