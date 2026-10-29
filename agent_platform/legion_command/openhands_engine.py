# -*- coding: utf-8 -*-
"""方案 A：OpenHands Agent Server 适配层。

- OpenHands 负责 Agent runtime / 工具循环 / workspace
- 军团网关负责模型、Key、组织与入口
- 只通过 loopback HTTP 通信；session key 不写进仓库，放本地状态文件
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE_URL = os.environ.get("OPENHANDS_URL", "http://127.0.0.1:8770").rstrip("/")
MODEL = os.environ.get("OPENHANDS_MODEL", "openhands_legion_gateway")
KEY_FILE = Path(os.environ.get("OPENHANDS_KEY_FILE", r"C:\项目\legion-openhands\.session_key"))
DEFAULT_TIMEOUT = 600


def _key() -> str:
    try:
        return KEY_FILE.read_text(encoding="utf-8-sig").strip()
    except OSError:
        return os.environ.get("OPENHANDS_SESSION_KEY", "").strip()


def _req(path: str, method: str = "GET", body: dict | None = None,
         timeout: int = 30, conversation_id: str = ""):
    key = _key()
    if not key:
        return None, {"ok": False, "error": "openhands_key_missing"}
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    headers = {"Authorization": "Bearer " + key, "Content-Type": "application/json"}
    if conversation_id:
        headers["X-OpenHands-ServerConversation-ID"] = conversation_id
    req = urllib.request.Request(BASE_URL + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        try:
            msg = e.read(500).decode("utf-8", "replace")
        except OSError:
            msg = ""
        return None, {"ok": False, "error": "openhands_http_%s" % e.code,
                      "detail": msg[:300]}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return None, {"ok": False, "error": "openhands_unreachable",
                      "detail": "%s: %s" % (type(e).__name__, str(e)[:200])}


def health() -> dict:
    _r, data = _req("/health", timeout=5)
    if data.get("status") == "ok":
        return {"ok": True, "base_url": BASE_URL, "model": MODEL}
    return {"ok": False, "base_url": BASE_URL, **{k: v for k, v in data.items() if k != "ok"}}


def chat(message: str, conversation_id: str = "", timeout: int = DEFAULT_TIMEOUT) -> dict:
    message = str(message or "").strip()
    if not message:
        return {"ok": False, "error": "empty_message"}
    t0 = time.time()
    r, data = _req(
        "/v1/chat/completions", method="POST",
        body={"model": MODEL, "stream": False,
              "messages": [{"role": "user", "content": message}]},
        timeout=timeout, conversation_id=conversation_id,
    )
    if r is None:
        return data
    choices = data.get("choices") or [{}]
    content = ((choices[0] or {}).get("message") or {}).get("content") or ""
    return {
        "ok": True,
        "reply": content,
        "conversation_id": r.headers.get("X-OpenHands-ServerConversation-ID") or "",
        "duration_ms": int((time.time() - t0) * 1000),
        "engine": "openhands",
        "model": MODEL,
    }


def events(conversation_id: str) -> dict:
    if not conversation_id:
        return {"ok": False, "error": "conversation_id_required"}
    _r, data = _req("/api/conversations/%s/events" % conversation_id, timeout=30)
    if _r is None:
        return data
    return {"ok": True, "events": data if isinstance(data, list) else data.get("items") or data}
