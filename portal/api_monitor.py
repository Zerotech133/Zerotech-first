# -*- coding: utf-8 -*-
"""个股批量监控入口（/api/monitor/*）——独立文件，portal_api.py 只加早分派。

GET（只读）：
  /api/monitor/pools                 股票池列表 + summary
  /api/monitor/alerts?pool=&limit=&severity=   告警列表
  /api/monitor/summary               汇总（池/告警/最近运行）
POST（写，需 X-Portal-Token；页面 JS 本机自动注入）：
  /api/monitor/run     {"pool":"watchlist","as_of":"2026-11-04","dry_run":false}
  /api/monitor/pool    {"action":"add|remove|set","name":"watchlist","codes":["600337.SH"]}
"""
import json
import re
import sys

ROOT = r"C:\项目\老田的AI助手"
CODE_RE = re.compile(r"^(60[0-9]{4}|68[0-9]{4}|00[0-9]{4}|30[0-9]{4}|8[0-9]{6})\.(SH|SZ|BJ)$")
MAX_BODY = 32 * 1024


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


def _monitor():
    """懒加载监控引擎（未合并时返回 None → 503，fail-visible）。"""
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    try:
        from analysis import monitor
        return monitor
    except Exception:
        return None


def _qs(qs):
    out = {}
    for part in (qs or "").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def handle_get(handler, path, qs):
    m = _monitor()
    if m is None:
        return _json(handler, {"ok": False, "error": "监控引擎未就绪（analysis.monitor 缺失）"}, 503)
    q = _qs(qs)
    try:
        if path == "/api/monitor/pools":
            return _json(handler, {"ok": True, "pools": m.list_pools(), "summary": m.summary()})
        if path == "/api/monitor/alerts":
            limit = max(1, min(int(q.get("limit", 50) or 50), 200))
            severity = q.get("severity") or None
            if severity not in (None, "", "high", "medium", "low"):
                return _json(handler, {"ok": False, "error": "severity 需为 high/medium/low"}, 400)
            items = m.list_alerts(pool=q.get("pool") or None, limit=limit, severity=severity or None)
            return _json(handler, {"ok": True, "alerts": items, "count": len(items)})
        if path == "/api/monitor/summary":
            return _json(handler, {"ok": True, **m.summary()})
        return _json(handler, {"ok": False, "error": "unknown monitor get: " + path}, 404)
    except Exception as e:
        return _json(handler, {"ok": False, "error": "监控读取失败: " + str(e)[:160]}, 500)


def _read_body(handler):
    ln = int(handler.headers.get("Content-Length") or 0)
    if ln > MAX_BODY:
        raise ValueError("body too large")
    raw = handler.rfile.read(ln).decode("utf-8") if ln else "{}"
    return json.loads(raw or "{}")


def handle_post(handler, path, qs):
    if not handler._check_portal_token():
        return _json(handler, {"ok": False, "error": "forbidden: 缺失或不匹配的 X-Portal-Token"}, 403)
    m = _monitor()
    if m is None:
        return _json(handler, {"ok": False, "error": "监控引擎未就绪（analysis.monitor 缺失）"}, 503)
    if path not in ("/api/monitor/run", "/api/monitor/pool"):
        return _json(handler, {"ok": False, "error": "unknown monitor post"}, 404)
    try:
        body = _read_body(handler)
    except ValueError as e:
        return _json(handler, {"ok": False, "error": str(e)}, 413)
    except Exception:
        return _json(handler, {"ok": False, "error": "bad json"}, 400)
    if path == "/api/monitor/run":
        pool = str(body.get("pool") or "watchlist")
        as_of = body.get("as_of") or None
        dry = bool(body.get("dry_run", False))
        try:
            r = m.run_monitor(pool=pool, as_of=as_of, dry_run=dry)
        except Exception as e:
            return _json(handler, {"ok": False, "error": "运行失败: " + str(e)[:160]}, 500)
        return _json(handler, r, 200 if r.get("ok") else 400)
    # pool 管理
    action = str(body.get("action") or "").strip().lower()
    name = str(body.get("name") or "watchlist").strip()
    codes = body.get("codes") or []
    if action not in ("add", "remove", "set"):
        return _json(handler, {"ok": False, "error": "action 需为 add/remove/set"}, 400)
    if not isinstance(codes, list) or not codes:
        return _json(handler, {"ok": False, "error": "codes 必须为非空数组"}, 400)
    codes = [str(c).strip().upper() for c in codes]
    bad = [c for c in codes if not CODE_RE.match(c)]
    if bad:
        return _json(handler, {"ok": False, "error": "非法代码: " + ",".join(bad[:5])}, 400)
    try:
        if action == "set":
            m.set_pool(name, codes)
        elif action == "add":
            m.add_codes(name, codes)
        else:
            m.remove_codes(name, codes)
        return _json(handler, {"ok": True, "pools": m.list_pools(), "action": action, "name": name})
    except Exception as e:
        return _json(handler, {"ok": False, "error": "池更新失败: " + str(e)[:160]}, 500)
