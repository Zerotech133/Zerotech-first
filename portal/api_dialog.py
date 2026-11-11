# -*- coding: utf-8 -*-
"""个股分析对话入口（/api/analysis/dialog/*）——独立文件，portal_api.py 只加早分派。

C1 对话内核的 HTTP 包装：会话/多轮/反问/动作统一走 analysis.dialog.DialogService。

GET（只读）：
  /api/analysis/dialog/health              对话内核 + 内部分析网关健康
  /api/analysis/dialog/sessions            最近会话列表
  /api/analysis/dialog/session?id=<sid>    会话详情（turns + pending question）

POST（写，需 X-Portal-Token；页面 JS 由 portal_api 自动注入）：
  /api/analysis/dialog/message  {"text":"分析 600337.SH","session_id":"","channel":"pc"}
  /api/analysis/dialog/answer   {"session_id":"sess_x","question_id":"q_x","option_key":"risk","text":""}
  /api/analysis/dialog/profile  {"followup_enabled":false}
"""
import json
import sys
from urllib.parse import parse_qs

ROOT = r"C:\项目\老田的AI助手"
MAX_BODY = 32 * 1024
CUSTOMER = "laotian"
PROFILE_FIELDS = ("followup_enabled",)


def _json(handler, obj, status=200):
    try:
        handler._json(obj, status)
    except Exception:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)


def _dialog():
    """懒加载对话内核（模块缺失时返回 None → 503，fail-visible）。"""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        from analysis import dialog
        return dialog
    except Exception:
        return None


def _service(d):
    """每请求一个 Store/连接（HTTP 多线程下不共享 sqlite 连接）。"""
    return d.DialogService(customer_id=CUSTOMER, store=d.Store(), client=d.AnalysisClient())


def _qs(qs):
    parsed = parse_qs(qs or "", keep_blank_values=True)
    return {key: values[0] if values else "" for key, values in parsed.items()}


def handle_get(handler, path, qs):
    d = _dialog()
    if d is None:
        return _json(handler, {"ok": False, "error": "对话内核未就绪（analysis.dialog 缺失）"}, 503)
    q = _qs(qs)
    svc = None
    try:
        if path == "/api/analysis/dialog/health":
            svc = _service(d)
            return _json(handler, {"ok": True, "db": d.DB,
                                   "gateway": svc.client.health()})
        svc = _service(d)
        if path == "/api/analysis/dialog/sessions":
            return _json(handler, svc.list_sessions())
        if path == "/api/analysis/dialog/session":
            sid = q.get("id") or q.get("session_id")
            if not sid:
                return _json(handler, {"ok": False, "error": "缺少 id（session_id）"}, 400)
            res = svc.get_session(sid)
            return _json(handler, res, 200 if res.get("ok") else 404)
        return _json(handler, {"ok": False, "error": "unknown dialog get: " + path}, 404)
    except Exception as e:  # pragma: no cover - 兜底可观测
        return _json(handler, {"ok": False, "error": "对话读取失败: " + str(e)[:160]}, 500)
    finally:
        if svc is not None:
            try:
                svc.store.close()
            except Exception:
                pass


def _read_body(handler):
    """只负责读取并校验长度；JSON 解析交给调用方（便于区分 413 与 400）。"""
    ln = int(handler.headers.get("Content-Length") or 0)
    if ln > MAX_BODY:
        raise ValueError("body too large")
    return handler.rfile.read(ln) if ln else b"{}"


def handle_post(handler, path, qs):
    if not handler._check_portal_token():
        return _json(handler, {"ok": False,
                               "error": "forbidden: 缺失或不匹配的 X-Portal-Token"}, 403)
    d = _dialog()
    if d is None:
        return _json(handler, {"ok": False, "error": "对话内核未就绪（analysis.dialog 缺失）"}, 503)
    if path not in ("/api/analysis/dialog/message",
                    "/api/analysis/dialog/answer",
                    "/api/analysis/dialog/profile"):
        return _json(handler, {"ok": False, "error": "unknown dialog post: " + path}, 404)
    try:
        raw = _read_body(handler)
    except ValueError as e:
        return _json(handler, {"ok": False, "error": str(e)}, 413)
    try:
        body = json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return _json(handler, {"ok": False, "error": "bad json"}, 400)
    if not isinstance(body, dict):
        return _json(handler, {"ok": False, "error": "body 需为 JSON 对象"}, 400)

    svc = None
    try:
        svc = _service(d)
        if path == "/api/analysis/dialog/message":
            text = str(body.get("text") or "").strip()
            if not text:
                return _json(handler, {"ok": False, "error": "text 不能为空"}, 400)
            sid = str(body.get("session_id") or "").strip() or None
            channel = str(body.get("channel") or "pc").strip()[:16] or "pc"
            res = svc.handle_message(text, session_id=sid, channel=channel)
            return _json(handler, res, 200 if res.get("ok") else 400)
        if path == "/api/analysis/dialog/answer":
            sid = str(body.get("session_id") or "").strip()
            qid = str(body.get("question_id") or "").strip()
            if not sid or not qid:
                return _json(handler, {"ok": False,
                                       "error": "session_id 与 question_id 必填"}, 400)
            res = svc.handle_answer(sid, qid,
                                    option_key=(str(body.get("option_key") or "").strip() or None),
                                    text=(str(body.get("text") or "").strip() or None))
            return _json(handler, res, 200 if res.get("ok") else 400)
        # profile
        prefs = {k: body[k] for k in PROFILE_FIELDS if k in body}
        if not prefs:
            return _json(handler, {"ok": False,
                                   "error": "profile 可改字段: " + ",".join(PROFILE_FIELDS)}, 400)
        res = svc.set_profile(**prefs)
        return _json(handler, res)
    except Exception as e:  # pragma: no cover - 兜底可观测
        return _json(handler, {"ok": False, "error": "对话处理失败: " + str(e)[:160]}, 500)
    finally:
        if svc is not None:
            try:
                svc.store.close()
            except Exception:
                pass
