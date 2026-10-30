# -*- coding: utf-8 -*-
"""P2.1 门户只读入口：/api/commercial/*（套餐/租户/用量/审计/健康）。

只读、无写操作；内部门户用。商业数据不存在时返回空/降级信息，不阻断。
"""
import json
import os
import sys

ROOT = os.environ.get("ANALYSIS_ROOT") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


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


def _qs(qs):
    out = {}
    for part in (qs or "").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _commercial():
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from commercial import audit, integration, plans, quota, tenants
    return {"audit": audit, "integration": integration, "plans": plans,
            "quota": quota, "tenants": tenants}


def _expected_token():
    t = os.environ.get("COMMERCIAL_ADMIN_TOKEN")
    if t:
        return t.strip()
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from commercial import tenants as _t
        p = os.path.join(_t.BASE, "admin.token")
        if os.path.exists(p):
            return open(p, encoding="utf-8-sig").read().strip()
    except Exception:
        pass
    return ""


def handle_get(handler, path, qs):
    q = _qs(qs)
    exp = _expected_token()
    if exp:
        got = ""
        try:
            got = str(handler.headers.get("X-Commercial-Token") or "")
        except Exception:
            got = ""
        if got != exp:
            return _json(handler, {"ok": False, "error": "需要 X-Commercial-Token（商业接口网关鉴权）"}, 401)
    try:
        m = _commercial()
    except Exception as e:
        return _json(handler, {"ok": False, "error": "commercial 模块不可用: " + str(e)[:120]}, 500)

    if path == "/api/commercial/health":
        return _json(handler, {"ok": True, **m["integration"].health(), "gateway_token": bool(_expected_token())})

    if path == "/api/commercial/plans":
        return _json(handler, {"ok": True, "mode": m["integration"].mode(), "plans": m["plans"].PLANS})

    if path == "/api/commercial/tenant":
        t = (q.get("tenant") or "").strip()
        if not t:
            return _json(handler, {"ok": False, "error": "需要 tenant 参数"}, 400)
        tenant = m["tenants"].get_tenant(t)
        if not tenant:
            return _json(handler, {"ok": True, "tenant": None, "usage": None})
        plan = tenant["plan"]
        usage = m["quota"].usage_report(t, plan=plan)
        return _json(handler, {"ok": True, "tenant": tenant, "usage": usage})

    if path == "/api/commercial/tenants":
        return _json(handler, {"ok": True, "tenants": m["tenants"].list_tenants()})

    if path == "/api/commercial/audit":
        n = max(1, min(int(q.get("n", 20) or 20), 200))
        rows = m["audit"].tail(n, tenant=(q.get("tenant") or None))
        return _json(handler, {"ok": True, "rows": rows})

    return _json(handler, {"ok": False, "error": "unknown commercial api: " + path}, 404)
