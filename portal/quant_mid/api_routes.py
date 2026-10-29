# -*- coding: utf-8 -*-
"""K-QUANT-002: quant_mid REST API 子路由 —— 挂载到 8097 push_admin_server.

端点前缀: /api/quant/*
业务: trade_pool 状态机的 HTTP 入口 (投研拿票/决策/建仓埋点/盯盘触发/平仓闭环)
鉴权: X-Push-Hub-Key (复用 8097 现有鉴权), 或 X-Quant-Mid-Key (新 key, env 兜底)

不扩巨石: 业务逻辑在 portal/quant_mid/trade_pool.py, 本文件只做 HTTP 分发.
"""
from __future__ import annotations
import json
import os
import sys
from typing import Optional, Tuple

# 让 trade_pool 能被 import
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from quant_mid import trade_pool as tp  # noqa: E402
import logging

BODY_CAP = 1 << 20  # 1MB


def _auth_ok(headers: dict) -> bool:
    """鉴权: 复用 X-Push-Hub-Key, 或新 X-Quant-Mid-Key."""
    # 优先尝试 push_hub_key (8097 守卫已有), 再看 quant_mid_key
    for hk, sk in [
        ("X-Push-Hub-Key", "push_hub_key"),
        ("X-Quant-Mid-Key", "quant_mid_key"),
    ]:
        sent = (headers.get(hk) or "").strip()
        if not sent:
            continue
        # env 优先
        expected = os.environ.get(sk.upper()) or os.environ.get(sk)
        if expected and sent == expected:
            return True
        # push_secrets.json 兜底
        try:
            import json as _json
            import os as _os
            sp = _os.environ.get("PUSH_SECRETS_PATH",
                                 _os.path.join(_os.path.dirname(__file__),
                                              "..", "work", "push_secrets.json"))
            if _os.path.exists(sp):
                with open(sp, encoding="utf-8") as f:
                    sec = _json.load(f)
                exp2 = sec.get(sk)
                if exp2 and sent == exp2:
                    return True
        except Exception as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "portal/quant_mid/api_routes.py", exc)
    return False


def _parse_body(headers: dict, body_bytes: bytes) -> Tuple[Optional[dict], Optional[str]]:
    """解析 JSON body. 返回 (dict, None) 或 (None, error_str)."""
    n = int(headers.get("Content-Length") or len(body_bytes))
    if not (0 < n <= BODY_CAP):
        return None, "body 大小非法"
    try:
        obj = json.loads(body_bytes[:n].decode("utf-8"))
        if not isinstance(obj, dict):
            return None, "body 必须是对象"
        return obj, None
    except (ValueError, UnicodeError) as e:
        return None, f"body 解析失败: {e}"


# ── 路由表: (method, path_prefix) → handler ────────────────
def _routes() -> list:
    return [
        # GET /api/quant/health              — DB 探活
        ("GET", "/api/quant/health",         _h_health),
        # GET /api/quant/pending             — 投研拿票 (status=pending_research)
        ("GET", "/api/quant/pending",        _h_pending),
        # GET /api/quant/active              — 盯盘轮询
        ("GET", "/api/quant/active",         _h_active),
        # GET /api/quant/row/{id}            — 单条查询 (debug)
        ("GET", "/api/quant/row/",           _h_row),   # 注意: path 有尾部 /
        # POST /api/quant/research/{id}       — 投研决策 (approve/reject)
        ("POST", "/api/quant/research/",     _h_research),
        # POST /api/quant/position/{id}       — 建仓埋点 (status → active_watch)
        ("POST", "/api/quant/position/",     _h_position),
        # POST /api/quant/alert/{id}          — 盯盘触发异动 (status → watch_alert)
        ("POST", "/api/quant/alert/",        _h_alert),
        # POST /api/quant/close/{id}          — 平仓闭环
        ("POST", "/api/quant/close/",        _h_close),
        # POST /api/quant/push                — 选股Agent 推池 (批量)
        ("POST", "/api/quant/push",          _h_push),
        # POST /api/quant/feedback/{id}       — 双向 Agent Webhook: HERMES 反馈回流郭嘉
        ("POST", "/api/quant/feedback/",     _h_feedback),
    ]


def handle(method: str, path: str, headers: dict, body: bytes = b"") -> Tuple[int, dict]:
    """给 push_admin_server Handler 调用的统一入口.

    返回 (status_code, response_dict). 调用方再用 self._json() 写回.
    """
    if not path.startswith("/api/quant/"):
        return 404, {"ok": False, "error": "not found"}

    for m, prefix, handler in _routes():
        if method != m:
            continue
        if m in ("GET",) or prefix.endswith("/"):
            # prefix 尾部带 /: 匹配 /api/quant/xxx/{id}
            if path == prefix.rstrip("/") or path.startswith(prefix):
                break
        elif path == prefix:
            break
    else:
        return 404, {"ok": False, "error": "quant route not found",
                     "hint": "prefix must be /api/quant/*"}

    # 鉴权 (health 端点放行)
    if handler != _h_health and not _auth_ok(headers):
        return 403, {"ok": False, "error": "unauthorized"}

    try:
        return handler(method, path, headers, body)
    except Exception as e:  # noqa: BLE001
        return 500, {"ok": False, "error": str(e),
                     "handler": handler.__name__ if handler else None}


# ── handlers ───────────────────────────────────────────────
def _h_health(m, p, h, b) -> Tuple[int, dict]:
    hc = tp.health_check()
    hc["route"] = "quant_mid v1"
    hc["endpoints"] = ["/api/quant/pending", "/api/quant/active",
                        "/api/quant/push", "/api/quant/alert/{id}"]
    return 200, hc


def _h_pending(m, p, h, b) -> Tuple[int, dict]:
    limit = 200
    try:
        import urllib.parse
        q = urllib.parse.urlparse(p).query
        if q:
            params = urllib.parse.parse_qs(q)
            if "limit" in params:
                limit = max(1, min(1000, int(params["limit"][0])))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/quant_mid/api_routes.py", exc)
    rows = tp.get_pending_research(limit=limit)
    return 200, {"ok": True, "count": len(rows), "items": rows}


def _h_active(m, p, h, b) -> Tuple[int, dict]:
    limit = 500
    try:
        import urllib.parse
        q = urllib.parse.urlparse(p).query
        if q:
            params = urllib.parse.parse_qs(q)
            if "limit" in params:
                limit = max(1, min(2000, int(params["limit"][0])))
    except Exception as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "portal/quant_mid/api_routes.py", exc)
    rows = tp.get_active_watch(limit=limit)
    return 200, {"ok": True, "count": len(rows), "items": rows}


def _h_row(m, p, h, b) -> Tuple[int, dict]:
    """GET /api/quant/row/{id}"""
    import re
    m2 = re.match(r"^/api/quant/row/(\d+)$", p)
    if not m2:
        return 400, {"ok": False, "error": "GET /api/quant/row/{id}"}
    row_id = int(m2.group(1))
    try:
        rows = tp.get_pending_research(limit=1000000)  # 简化: 暂用现成接口
        row = next((r for r in rows if r["id"] == row_id), None)
        if row is None:
            return 404, {"ok": False, "error": f"row {row_id} not in pending_research"}
        return 200, {"ok": True, "item": row}
    except Exception as e:
        return 500, {"ok": False, "error": str(e)}


def _h_research(m, p, h, b) -> Tuple[int, dict]:
    """POST /api/quant/research/{id}  body: {"approve": true/false, "comment": "..."}"""
    import re
    m2 = re.match(r"^/api/quant/research/(\d+)$", p)
    if not m2:
        return 400, {"ok": False, "error": "POST /api/quant/research/{id}"}
    row_id = int(m2.group(1))
    body, err = _parse_body(h, b)
    if err:
        return 400, {"ok": False, "error": err}
    approve = bool(body.get("approve", True))
    comment = str(body.get("comment", ""))
    try:
        tp.research_decide(row_id, approve=approve, comment=comment)
        return 200, {"ok": True, "id": row_id, "status": "approved" if approve else "rejected"}
    except ValueError as e:
        return 404, {"ok": False, "error": str(e)}


def _h_position(m, p, h, b) -> Tuple[int, dict]:
    """POST /api/quant/position/{id}  body: {entry_price, stop_loss, take_profit, position_pct, entry_date?}"""
    import re
    m2 = re.match(r"^/api/quant/position/(\d+)$", p)
    if not m2:
        return 400, {"ok": False, "error": "POST /api/quant/position/{id}"}
    row_id = int(m2.group(1))
    body, err = _parse_body(h, b)
    if err:
        return 400, {"ok": False, "error": err}
    try:
        tp.set_position(
            row_id,
            entry_price=float(body["entry_price"]),
            stop_loss=float(body["stop_loss"]),
            take_profit=float(body["take_profit"]),
            position_pct=float(body["position_pct"]),
            entry_date=body.get("entry_date"),
        )
        return 200, {"ok": True, "id": row_id, "status": "active_watch"}
    except (KeyError, ValueError) as e:
        return 400, {"ok": False, "error": f"参数缺失或非法: {e}"}


def _h_alert(m, p, h, b) -> Tuple[int, dict]:
    """POST /api/quant/alert/{id}  body: {"detail": "...", "push_feishu": true}

    盯盘触发异动: DB 状态 → watch_alert + alert_triggered=TRUE.
    push_feishu=true → 走 8097 现有 Agent webhook 出口推飞书.
    """
    import re
    m2 = re.match(r"^/api/quant/alert/(\d+)$", p)
    if not m2:
        return 400, {"ok": False, "error": "POST /api/quant/alert/{id}"}
    row_id = int(m2.group(1))
    body, err = _parse_body(h, b)
    if err:
        return 400, {"ok": False, "error": err}
    detail = str(body.get("detail", ""))
    push_fs = bool(body.get("push_feishu", True))

    # 1) DB 写回
    try:
        tp.mark_alert(row_id, detail=detail)
    except ValueError as e:
        return 404, {"ok": False, "error": str(e)}

    # 2) 触发飞书 (走 8097 现有 Agent webhook 出口, K-ORG-204)
    fs_ok, fs_why = True, "not requested"
    if push_fs:
        try:
            import sys as _sys
            _sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
            from portal.push_admin_server import api_send  # noqa: WPS433
            # 简化: 读一条 active_watch 里包含此 row_id 的记录来取 stock_code
            # 实际可加 trade_pool.get_row_by_id() — 暂用现有接口
            row = next((r for r in tp.get_active_watch(limit=1000)
                        if r["id"] == row_id), None)
            code = row["stock_code"] if row else f"row_{row_id}"
            name = row.get("stock_name", "") if row else ""
            msg = f"【交易异动】{name}({code})\n{detail}"
            send_resp = api_send({
                "content": msg,
                "msg_type": "text",
                "kind": "quant:watch_alert",
                "receive_id": "agent-webhook",  # 8097 Agent webhook 出口占位
            })
            fs_ok = send_resp.get("ok", False)
            fs_why = send_resp.get("why", "")
        except Exception as e:  # noqa: BLE001
            fs_ok = False
            fs_why = f"飞书推送异常: {e}"

    return 200, {
        "ok": True, "id": row_id, "status": "watch_alert",
        "feishu": {"ok": fs_ok, "why": fs_why},
    }


def _h_close(m, p, h, b) -> Tuple[int, dict]:
    """POST /api/quant/close/{id}  body: {"close_price": N, "final_status": "closed_profit|loss|other"}"""
    import re
    m2 = re.match(r"^/api/quant/close/(\d+)$", p)
    if not m2:
        return 400, {"ok": False, "error": "POST /api/quant/close/{id}"}
    row_id = int(m2.group(1))
    body, err = _parse_body(h, b)
    if err:
        return 400, {"ok": False, "error": err}
    close_price = float(body["close_price"])
    final_status = str(body.get("final_status", "closed_other"))
    try:
        tp.close_position(row_id, close_price=close_price, final_status=final_status)
        return 200, {"ok": True, "id": row_id, "status": final_status, "closed_price": close_price}
    except ValueError as e:
        return 400, {"ok": False, "error": str(e)}


def _h_push(m, p, h, b) -> Tuple[int, dict]:
    """POST /api/quant/push  选股Agent 推池.

    body 两种形态:
      单条: {stock_code, signal, confidence, source_agent, rationale?, stock_name?}
      批量: {"items": [同上...]}
    """
    body, err = _parse_body(h, b)
    if err:
        return 400, {"ok": False, "error": err}
    if "items" in body and isinstance(body["items"], list):
        ids = tp.push_batch(body["items"])
        return 200, {"ok": True, "count": len(ids), "ids": ids}
    try:
        rid = tp.push_to_pool(**body)
        return 200, {"ok": True, "id": rid}
    except TypeError as e:
        return 400, {"ok": False, "error": f"字段缺失: {e}"}


def _h_feedback(m, p, h, b) -> Tuple[int, dict]:
    """POST /api/quant/feedback/{id} —— 双向 Agent Webhook: HERMES 反馈回流郭嘉.

    body: {"feedback_type": "approve|reject|calibrate|skip|other",
           "feedback_note": "...", "feedback_score": 0.0~1.0}
    语义: 飞书侧 HERMES / 用户 / 复盘系统 对某条 trade_pool 信号的评价/校准.
    """
    import re
    m2 = re.match(r"^/api/quant/feedback/(\d+)$", p)
    if not m2:
        return 400, {"ok": False, "error": "POST /api/quant/feedback/{id}"}
    row_id = int(m2.group(1))
    body, err = _parse_body(h, b)
    if err:
        return 400, {"ok": False, "error": err}
    ftype = str(body.get("feedback_type", ""))
    fnote = str(body.get("feedback_note", ""))
    fscore = body.get("feedback_score")
    try:
        tp.set_feedback(row_id, feedback_type=ftype,
                        feedback_note=fnote, feedback_score=fscore)
        return 200, {"ok": True, "id": row_id,
                     "feedback_type": ftype, "feedback_at": "now"}
    except ValueError as e:
        return 404, {"ok": False, "error": str(e)}
