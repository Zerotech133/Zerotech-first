# -*- coding: utf-8 -*-
"""诸葛亮 IDE 复核 HTTP 适配器（P0）。

路由：
  POST /api/zhuge/review            -> 202 pending
  GET  /api/zhuge/review/status     -> 状态/模型
  GET  /api/zhuge/review/<id>       -> 结果

安全：
- feature flag 默认关；
- 需要门户登录 session（zb_session cookie）；
- 本地测试可用 ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS=1；
- 正文 ≤64KB；只读，不写代码。
"""
from __future__ import annotations

import json
import os

import zhuge_review

MAX_BODY = 64 * 1024


def _authorized(handler) -> bool:
    if os.environ.get("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "").strip() == "1":
        return True
    try:
        import auth_gateway
        sid = auth_gateway._cookie_value(handler)
        return bool(sid and auth_gateway.STORE.get(sid))
    except Exception:
        return False


def _json(handler, payload, status=200):
    if hasattr(handler, "_json"):
        handler._json(payload, status)
        return
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def handle_post(handler) -> None:
    if not zhuge_review.enabled():
        _json(handler, {"ok": False, "error": "zhuge_review_disabled"}, 404)
        return
    if not _authorized(handler):
        _json(handler, {"ok": False, "error": "unauthorized"}, 403)
        return
    try:
        ln = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        _json(handler, {"ok": False, "error": "bad_content_length"}, 400)
        return
    if ln < 0:
        _json(handler, {"ok": False, "error": "bad_content_length"}, 400)
        return
    if ln > MAX_BODY:
        _json(handler, {"ok": False, "error": "request_too_large"}, 413)
        return
    raw = handler.rfile.read(ln) if ln else b""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _json(handler, {"ok": False, "error": "bad_json"}, 400)
        return
    status, out = zhuge_review.start_review(payload)
    _json(handler, out, status)


def handle_get(handler, path: str) -> None:
    if path == "/api/zhuge/review/status":
        # P1 安全：status 也不得未登录可读（此前 8734 无 cookie 200）
        if not _authorized(handler):
            _json(handler, {"ok": False, "error": "unauthorized"}, 403)
            return
        _json(handler, {"ok": True, **zhuge_review.status()}, 200)
        return
    if not zhuge_review.enabled():
        _json(handler, {"ok": False, "error": "zhuge_review_disabled"}, 404)
        return
    if not _authorized(handler):
        _json(handler, {"ok": False, "error": "unauthorized"}, 403)
        return
    rid = path.rsplit("/", 1)[-1]
    out = zhuge_review.get_review(rid)
    _json(handler, out, 200 if out.get("ok") else 404)
