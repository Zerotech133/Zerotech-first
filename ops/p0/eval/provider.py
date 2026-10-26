# -*- coding: utf-8 -*-
"""promptfoo Python provider：把 {{input}} 送到 8795 /llm/chat（只读）。"""
from __future__ import annotations

import json
import os
import urllib.request


def call_api(prompt, options, context):
    binding = os.environ.get("P0_BINDING", "ollama/qwen3.5:9b")
    system = os.environ.get(
        "P0_SYSTEM",
        "你是诸葛亮（AI1），反向军师，零执行权。只回答，不执行、不派单；"
        "不确定就说不确定，不编造来源。")
    body = json.dumps({"binding": binding, "system": system, "user": prompt},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8795/llm/chat", data=body, method="POST",
        headers={"Content-Type": "application/json; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"output": "", "error": "%s: %s" % (type(exc).__name__, exc),
                "metadata": {"binding": binding}}
    data = payload.get("data") or {}
    return {"output": data.get("text") or "",
            "metadata": {"binding": binding, "ok": payload.get("ok"),
                         "carrier": data.get("carrier"), "relay": data.get("relay")}}
