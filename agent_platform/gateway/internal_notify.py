# -*- coding: utf-8 -*-
"""内部流转通知统一适配器：所有内部生产者 → inbox_gateway。

不新建第二套队列；只把“目标/标题/正文/来源/去重”翻译成 inbox_gateway.submit。
"""
from __future__ import annotations

import hashlib
import json
import re
import time

from gateway import inbox_gateway  # noqa: E402

SCHEMA = "hq/internal_notify/v1"
_TARGET_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
MAX_TITLE = 120
MAX_BODY = 4000


def _digest(target: str, source: str, title: str, body: str) -> str:
    raw = json.dumps({"t": target, "s": source, "ti": title, "b": body},
                     ensure_ascii=False, sort_keys=True)
    return "nf-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:28]


def emit(target: str, title: str, body: str = "", *, kind: str = "task_request",
         source: str = "internal", link: str = "", actor: str = "",
         dedupe_key: str | None = None) -> dict:
    """投递一条内部通知；返回 inbox_gateway 信封。"""
    t = str(target or "").strip()
    if not _TARGET_RE.match(t):
        return {"ok": False, "error": {"code": "bad_target",
                                       "message": "target 必须是席位/agent id"}}
    title = str(title or "").strip()[:MAX_TITLE]
    if not title:
        return {"ok": False, "error": {"code": "bad_title", "message": "title 必填"}}
    body = str(body or "")[:MAX_BODY]
    payload = {"schema": SCHEMA, "target": t, "title": title, "body": body,
               "link": str(link or "")[:400], "actor": str(actor or "")[:80],
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
    key = dedupe_key or _digest(t, source, title, body)
    if not re.match(r"^[A-Za-z0-9_-]{1,64}$", key):
        key = _digest(t, source, title, body)
    return inbox_gateway.submit(kind, payload, source=source, idempotency_key=key)


def emit_many(items: list[dict]) -> list[dict]:
    out = []
    for x in items or []:
        if not isinstance(x, dict):
            continue
        out.append(emit(
            target=x.get("target"), title=x.get("title"), body=x.get("body", ""),
            kind=x.get("kind", "task_request"), source=x.get("source", "internal"),
            link=x.get("link", ""), actor=x.get("actor", ""),
            dedupe_key=x.get("dedupe_key")))
    return out
