# -*- coding: utf-8 -*-
"""promptfoo Python provider：把 {{input}} 送到 8795 /llm/chat（只读）。"""
from __future__ import annotations

import json
import os
import urllib.request


# 回环禁代理：本机服务（Ollama 11434 等）的请求不走系统代理（见 tools/safe_urlopen.py）
import sys as _sys
from pathlib import Path as _Path
for _base in _Path(__file__).resolve().parents:
    for _cand in (_base, _base / "tools"):
        if (_cand / "safe_urlopen.py").exists():
            _sys.path.insert(0, str(_cand))
            break
    else:
        continue
    break
try:
    from safe_urlopen import safe_urlopen
except ImportError:  # 作为包/仓库根导入时
    from tools.safe_urlopen import safe_urlopen

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
        with safe_urlopen(req, timeout=120) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        return {"output": "", "error": "%s: %s" % (type(exc).__name__, exc),
                "metadata": {"binding": binding}}
    data = payload.get("data") or {}
    return {"output": data.get("text") or "",
            "metadata": {"binding": binding, "ok": payload.get("ok"),
                         "carrier": data.get("carrier"), "relay": data.get("relay")}}
