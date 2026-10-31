# -*- coding: utf-8 -*-
"""诸葛亮 IDE 复核 HTTP 适配器（P0 + 运营化 2026-10-31，WO-LEGION-REVIEWOPS-20261031）。

路由：
  POST /api/zhuge/review                    -> 202 pending（按 session 限额）
  POST /api/zhuge/review/<id>/adoption      -> 200 记录采纳/忽略（append-only 账本）
  GET  /api/zhuge/review/status             -> 状态/模型/限额
  GET  /api/zhuge/review/<id>               -> 结果（含 adoption）

安全：
- feature flag 默认关（ZHUGE_IDE_REVIEW=off；未开时全部 404）；
- 需要门户登录 session（zb_session cookie）；测试可用
  ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS=1；
- 正文 ≤64KB；只读，不写代码/不合并；
- 限流：按 session cookie 的 hash 归因（zhuge_review.RATE_PER_SESSION 次/窗口），
  超限 429 + body.retry_after_s；不落明文 cookie；
- 兼容旧调用：POST /api/zhuge/review body.action=adopt（decision 型或
  adopted/ignored 型均可）；线上主路径为 /<id>/adoption 子资源。
"""
from __future__ import annotations

import hashlib
import json
import os
import re

import zhuge_review

MAX_BODY = 64 * 1024
_ADOPT_PATH_RE = re.compile(r"^/api/zhuge/review/([^/]+)/adoption$")
_REVIEW_BASE = "/api/zhuge/review"
# adoption 错误码 → HTTP 状态（不把内部异常当成功返回）
_ADOPTION_ERROR_CODES = {
    "review_not_found": 404,
    "review_not_ready": 409,
    "bad_review_id": 400,
    "bad_finding_id": 400,
    "conflict_finding_id": 400,
    "nothing_to_record": 400,
    "ledger_write_failed": 500,
}


def _authorized(handler) -> bool:
    if os.environ.get("ZHUGE_IDE_REVIEW_ALLOW_ANONYMOUS", "").strip() == "1":
        return True
    try:
        import auth_gateway
        sid = auth_gateway._cookie_value(handler)
        return bool(sid and auth_gateway.STORE.get(sid))
    except Exception:
        return False


def _session_id(handler) -> str:
    try:
        import auth_gateway
        return str(auth_gateway._cookie_value(handler) or "")
    except Exception:
        return ""


def _session_hash(handler) -> str:
    """门户 session cookie 的哈希（只作限流归因，不落明文 cookie）。"""
    sid = _session_id(handler)
    if not sid:
        return "anonymous"
    return hashlib.sha256(("zb_session:" + sid).encode("utf-8", "replace")).hexdigest()[:16]


def _actor(handler) -> str:
    """采纳记录 by：优先 session 里的 sub，其次 session hash 前 8 位。"""
    try:
        import auth_gateway
        sid = _session_id(handler)
        rec = auth_gateway.STORE.get(sid) if sid else None
        if isinstance(rec, dict):
            for key in ("sub", "user", "username", "name"):
                if rec.get(key):
                    return str(rec[key])[:120]
    except Exception:
        pass
    h = _session_hash(handler)
    return ("session:" + h[:8]) if h != "anonymous" else "anonymous"


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


def _read_json_body(handler):
    """读并解析正文；返回 (payload, (status, error_dict) | None)。"""
    try:
        ln = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        return None, (400, {"ok": False, "error": "bad_content_length"})
    if ln < 0:
        return None, (400, {"ok": False, "error": "bad_content_length"})
    if ln > MAX_BODY:
        return None, (413, {"ok": False, "error": "request_too_large"})
    raw = handler.rfile.read(ln) if ln else b""
    try:
        return json.loads(raw.decode("utf-8")), None
    except (UnicodeDecodeError, ValueError):
        return None, (400, {"ok": False, "error": "bad_json"})


def _adoption_result(handler, review_id: str, payload: dict) -> None:
    out = zhuge_review.adopt_findings(
        review_id,
        adopted=payload.get("adopted"),
        ignored=payload.get("ignored"),
        note=str(payload.get("note") or ""),
        by=str(payload.get("by") or payload.get("actor") or "").strip()
        or _actor(handler),
    )
    code = 200 if out.get("ok") else _ADOPTION_ERROR_CODES.get(
        str(out.get("error") or ""), 400)
    _json(handler, out, code)


def _handle_adoption(handler, review_id: str) -> None:
    payload, err = _read_json_body(handler)
    if err:
        _json(handler, err[1], err[0])
        return
    if not isinstance(payload, dict):
        _json(handler, {"ok": False, "error": "payload_must_be_object"}, 400)
        return
    _adoption_result(handler, review_id, payload)


def handle_post(handler, path: str = _REVIEW_BASE) -> None:
    path = str(path or _REVIEW_BASE).split("?", 1)[0]
    if not zhuge_review.enabled():
        _json(handler, {"ok": False, "error": "zhuge_review_disabled"}, 404)
        return
    if not _authorized(handler):
        _json(handler, {"ok": False, "error": "unauthorized"}, 403)
        return
    m = _ADOPT_PATH_RE.match(path)
    if m:
        _handle_adoption(handler, m.group(1))
        return
    if path != _REVIEW_BASE:
        _json(handler, {"ok": False, "error": "not_found", "path": path}, 404)
        return
    payload, err = _read_json_body(handler)
    if err:
        _json(handler, err[1], err[0])
        return
    if isinstance(payload, dict) and str(payload.get("action") or "") == "adopt":
        # 兼容旧调用形态：body.action=adopt（decision 型 / adopted+ignored 型）
        if "adopted" in payload or "ignored" in payload:
            _adoption_result(handler, str(payload.get("review_id") or ""), payload)
            return
        out = zhuge_review.adopt(
            str(payload.get("review_id") or ""),
            str(payload.get("decision") or ""),
            str(payload.get("note") or ""),
            str(payload.get("actor") or ""),
        )
        _json(handler, out, 200 if out.get("ok") else 400)
        return
    status, out = zhuge_review.start_review(payload,
                                            session_key=_session_hash(handler))
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
