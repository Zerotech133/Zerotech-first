# -*- coding: utf-8 -*-
"""D1 最终裁决 POST API（WO-D1-DECISION-20261101，2026-11-01）。

路由：POST /api/d1/decision
边界：只落 D1 decision 账；不下单/不执行/不接实盘。
鉴权：portal_api 的 _MUTATION_POSTS（X-Portal-Token；本机页面自动带）。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 8091 以脚本方式启动时 sys.path 只有 portal/；d1 模块在仓库根。
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import json

import ifs_envelope

MAX_BODY = 64 * 1024


def _json(handler, obj, code=200):
    ifs_envelope.send(handler, obj, code, fallback_interface="d1.decision.create")


def handle_post(handler):
    path = handler.path.split("?", 1)[0]
    if path != "/api/d1/decision":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        length = 0
    if length > MAX_BODY:
        _json(handler, {"ok": False, "error": "body_too_large",
                        "message": "decision body > 64KB"}, 413)
        return
    raw = handler.rfile.read(length) if length else b"{}"
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        _json(handler, {"ok": False, "error": "bad_json"}, 400)
        return
    try:
        from agent_platform import d1_decision as D
        row = D.append_decision(payload)
        _json(handler, {
            "ok": True,
            "decision": row,
            "summary": D.summary(),
            "not_a_trade": True,
            "paper_only": True,
        }, 200)
    except Exception as exc:  # noqa: BLE001
        # D1DecisionError 走 400；其余失败 fail-visible，不把异常吞成成功。
        code = getattr(exc, "code", "") or "decision_failed"
        status = 400 if code in {
            "bad_body", "missing_code", "bad_tier", "bad_confidence",
            "bad_rationale", "bad_decider", "not_paper",
            "execution_field_forbidden", "bad_decision_id", "duplicate_decision",
            "bad_evidence",
        } else 500
        _json(handler, {"ok": False, "error": code,
                        "message": str(exc)[:300]}, status)


def handle_get(handler, qs):
    """GET /api/d1/decisions?code=...：只读列裁决（latest/summary/list）。"""
    path = handler.path.split("?", 1)[0]
    if path != "/api/d1/decisions":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    from urllib.parse import parse_qs
    q = parse_qs(qs or "")
    code = (q.get("code") or [""])[0].strip()
    try:
        from agent_platform import d1_decision as D
        rows = D.read_decisions()
        if code:
            latest = D.latest_for(code)
            _json(handler, {"ok": True, "code": code, "latest": latest,
                            "summary": D.summary(), "readonly": True}, 200)
            return
        latest = {}
        for r in rows:
            c = str(r.get("code") or "")
            if c:
                latest[c] = r
        _json(handler, {"ok": True, "summary": D.summary(),
                        "latest_by_code": latest,
                        "decisions": rows[-100:],
                        "readonly": True}, 200)
    except Exception as exc:  # noqa: BLE001
        _json(handler, {"ok": False, "error": "decision_read_failed",
                        "message": str(exc)[:300]}, 500)
