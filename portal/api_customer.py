# -*- coding: utf-8 -*-
"""P2.4 客户门户 API：/api/customer/*（登录/分析/报告下载/历史/配额）。

设计：
- 登录：POST {tenant, license} → 校验 license（HMAC）→ 签发短期会话 token（HMAC，8h）；
- 分析：POST {token, code} → 校验 token/license/配额 → 以**商业模式子进程**跑 analysis.run（租户隔离目录）；
- 报告：GET ?token&run_id&format=md|h5 → 仅限该租户 runs 目录，路径穿越防护；
- 历史：GET ?token → 该租户历史运行。
只读/受控写；内部门户部署，监听 127.0.0.1。
"""
import base64
import hashlib
import hmac
import json
import os
import re
import subprocess
import sys
import time

ROOT = r"C:\项目\老田的AI助手"
HOME = os.environ.get("COMMERCIAL_HOME") or r"C:\项目\commercial"
SECRET_FILE = os.path.join(HOME, "secret.key")
CODE_RE = re.compile(r"^(60[0-9]{4}|68[0-9]{4}|00[0-9]{4}|30[0-9]{4}|8[0-9]{6})\.(SH|SZ|BJ)$")
RUN_RE = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")
TOKEN_TTL = 8 * 3600


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


def _body(handler):
    ln = int(handler.headers.get("Content-Length") or 0)
    return json.loads(handler.rfile.read(ln).decode("utf-8") or "{}") if ln else {}


def _secret():
    s = os.environ.get("COMMERCIAL_LICENSE_SECRET")
    if s:
        return s
    try:
        if os.path.exists(SECRET_FILE):
            return open(SECRET_FILE, encoding="utf-8-sig").read().strip()
    except Exception:
        pass
    return ""


def _b64(b):
    return base64.urlsafe_b64encode(b).decode("ascii").rstrip("=")


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _sign(data: str) -> str:
    return _b64(hmac.new(_secret().encode("utf-8"), data.encode("ascii"), hashlib.sha256).digest())


def issue_token(tenant: str, license_key: str, ttl: int = TOKEN_TTL) -> str:
    payload = json.dumps({"t": tenant, "l": license_key, "exp": int(time.time()) + ttl},
                         ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    p = _b64(payload.encode("utf-8"))
    return "SAC2." + p + "." + _sign(p)


def verify_token(token: str):
    """返回 payload dict；失败返回 None。"""
    try:
        prefix, p, sig = (token or "").strip().split(".")
    except ValueError:
        return None
    if prefix != "SAC2" or not hmac.compare_digest(sig, _sign(p)):
        return None
    try:
        d = json.loads(_unb64(p).decode("utf-8"))
    except Exception:
        return None
    if int(d.get("exp", 0)) < time.time():
        return None
    return d


def _verify_license(license_key: str):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from commercial import license as lic
    return lic.verify(license_key, secret=_secret() or None)


def _workdir(tenant: str) -> str:
    p = os.path.join(HOME, "tenants", tenant, "work")
    os.makedirs(os.path.join(p, "runs"), exist_ok=True)
    return p


def _report_path(tenant: str, run_id: str, fmt: str):
    if not RUN_RE.match(run_id or ""):
        return None
    base = os.path.join(_workdir(tenant), "runs", run_id)
    if fmt == "h5":
        p = os.path.join(base, "h5.html")
    else:
        p = os.path.join(base, "report.md")
    return p if os.path.exists(p) else None


def handle_post(handler, path):
    if path == "/api/customer/login":
        try:
            body = _body(handler)
        except Exception:
            return _json(handler, {"ok": False, "error": "bad json"}, 400)
        tenant = str(body.get("tenant") or "").strip()
        license_key = str(body.get("license") or "").strip()
        if not tenant or not license_key:
            return _json(handler, {"ok": False, "error": "需要 tenant 与 license"}, 400)
        if not _secret():
            return _json(handler, {"ok": False, "error": "服务端未配置许可密钥（COMMERCIAL_LICENSE_SECRET）"}, 500)
        try:
            l = _verify_license(license_key)
        except Exception as e:
            return _json(handler, {"ok": False, "error": "license 校验失败: " + str(e)[:100]}, 401)
        if l.tenant != tenant:
            return _json(handler, {"ok": False, "error": "租户与 license 不匹配"}, 403)
        token = issue_token(tenant, license_key)
        from commercial import plans, quota, tenants
        try:
            if not tenants.get_tenant(tenant):
                tenants.add_tenant(tenant, plan=l.plan, license_key=license_key)
        except Exception:
            pass
        usage = quota.usage_report(tenant, plan=l.plan)
        return _json(handler, {"ok": True, "token": token, "plan": l.plan,
                               "plan_name": plans.PLANS.get(l.plan, {}).get("name", l.plan),
                               "seats": l.seats, "expires": l.expires, "usage": usage})

    if path == "/api/customer/analyze":
        try:
            body = _body(handler)
        except Exception:
            return _json(handler, {"ok": False, "error": "bad json"}, 400)
        d = verify_token(str(body.get("token") or ""))
        if not d:
            return _json(handler, {"ok": False, "error": "未登录或会话过期"}, 401)
        code = str(body.get("code") or "").strip().upper()
        if not CODE_RE.match(code):
            return _json(handler, {"ok": False, "error": "代码格式不正确（如 600337.SH）"}, 400)
        try:
            l = _verify_license(d["l"])
        except Exception as e:
            return _json(handler, {"ok": False, "error": "license 失效: " + str(e)[:100]}, 401)
        from commercial import quota
        if quota.remaining(d["t"], "reports_month", plan=l.plan) <= 0:
            return _json(handler, {"ok": False, "error": "本月报告配额已用完（%s）" % l.plan}, 429)
        env = os.environ.copy()
        env.update({
            "PYTHONIOENCODING": "utf-8",
            "COMMERCIAL_MODE": "commercial",
            "COMMERCIAL_TENANT": d["t"],
            "COMMERCIAL_LICENSE_KEY": d["l"],
            "COMMERCIAL_LICENSE_SECRET": _secret(),
            "COMMERCIAL_HOME": HOME,
            "ANALYSIS_WORK": _workdir(d["t"]),
        })
        try:
            p = subprocess.run([sys.executable, "-X", "utf8", "-m", "analysis.run",
                                "--code", code, "--no-llm"],
                               cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
        except subprocess.TimeoutExpired:
            return _json(handler, {"ok": False, "error": "分析超时"}, 504)
        out = ""
        for line in (p.stdout or "").strip().splitlines()[::-1]:
            if line.startswith("{"):
                out = line
                break
        if p.returncode != 0 or not out:
            return _json(handler, {"ok": False, "error": "分析失败", "stderr": (p.stderr or "")[-300:]}, 500)
        try:
            r = json.loads(out)
        except Exception:
            return _json(handler, {"ok": False, "error": "输出解析失败"}, 500)
        return _json(handler, {"ok": True, "run_id": r.get("run_id"), "rating": r.get("rating"),
                               "coverage": r.get("coverage"), "elapsed": r.get("elapsed"),
                               "report_url": "/api/customer/report?run_id=%s&format=md" % r.get("run_id"),
                               "h5_url": "/api/customer/report?run_id=%s&format=h5" % r.get("run_id"),
                               "usage": quota.usage_report(d["t"], plan=l.plan)})
    return _json(handler, {"ok": False, "error": "unknown customer post: " + path}, 404)


def handle_get(handler, path, qs):
    q = _qs(qs)
    if path == "/api/customer/health":
        return _json(handler, {"ok": True, "configured": bool(_secret()), "home": HOME})
    d = verify_token(q.get("token", ""))
    if not d:
        return _json(handler, {"ok": False, "error": "未登录或会话过期"}, 401)
    tenant = d["t"]
    if path == "/api/customer/history":
        base = os.path.join(_workdir(tenant), "runs")
        rows = []
        try:
            for name in sorted(os.listdir(base))[-30:]:
                aj = os.path.join(base, name, "analysis.json")
                if not os.path.exists(aj):
                    continue
                try:
                    j = json.load(open(aj, encoding="utf-8"))
                except Exception:
                    continue
                rows.append({"run_id": name, "code": j.get("meta", {}).get("code"),
                             "rating": j.get("verdict", {}).get("rating"),
                             "generated": j.get("meta", {}).get("generated_at")})
        except Exception:
            pass
        return _json(handler, {"ok": True, "rows": rows[::-1]})
    if path == "/api/customer/report":
        run_id = q.get("run_id", "")
        fmt = (q.get("format") or "md").lower()
        p = _report_path(tenant, run_id, fmt)
        if not p:
            return _json(handler, {"ok": False, "error": "报告不存在"}, 404)
        data = open(p, "rb").read()
        handler.send_response(200)
        handler.send_header("Content-Type", "text/html; charset=utf-8" if fmt == "h5"
                            else "text/markdown; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)
        return
    if path == "/api/customer/quota":
        from commercial import license as lic, quota
        try:
            l = lic.verify(d["l"], secret=_secret() or None)
            return _json(handler, {"ok": True, "plan": l.plan, "seats": l.seats,
                                   "usage": quota.usage_report(tenant, plan=l.plan)})
        except Exception as e:
            return _json(handler, {"ok": False, "error": "license 失效: " + str(e)[:100]}, 401)
    if path == "/api/customer/health":
        return _json(handler, {"ok": True, "configured": bool(_secret()), "home": HOME})
    return _json(handler, {"ok": False, "error": "unknown customer get: " + path}, 404)
