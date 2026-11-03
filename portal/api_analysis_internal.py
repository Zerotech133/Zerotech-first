# -*- coding: utf-8 -*-
"""个股分析 · 内部网关（H1）：供 Hermes MCP / 本地自动化调用。

安全边界（fail-closed）：
- 仅接受 loopback（127.0.0.1/::1）来源；
- 必须携带 `X-Analysis-Token`，token 读 `ANALYSIS_INTERNAL_TOKEN` 或 token 文件
  （默认 `C:\\项目\\commercial\\analysis_internal.token`，文件不存在 → 503）；
- 只读端点 + 受控提交（analyze/batch 入文件队列），不新增执行能力；
- 审计：提交类动作写 `commercial.audit`（H1-6 哈希链）。

路由：
  GET  /api/analysis/internal/health
  GET  /api/analysis/internal/overview
  GET  /api/analysis/internal/watchlist
  GET  /api/analysis/internal/data-status?code=
  GET  /api/analysis/internal/report?run_id=&format=md|h5
  GET  /api/analysis/internal/events?code=&limit=&status=
  GET  /api/analysis/internal/job/<job_id>
  POST /api/analysis/internal/analyze   {"code": "...", "llm": false, "push": false}
  POST /api/analysis/internal/batch     {"codes": ["..."], "llm": false, "push": false}
"""
import hmac
import json
import os
import re
import sys

ROOT = r"C:\项目\老田的AI助手"
WORK = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")
QUEUE = os.path.join(WORK, "queue")
RUNS = os.path.join(WORK, "runs")
DB = os.path.join(WORK, "analysis.db")
BASE = "/api/analysis/internal"

TOKEN_ENV = "ANALYSIS_INTERNAL_TOKEN"
TOKEN_FILE = os.environ.get("ANALYSIS_INTERNAL_TOKEN_FILE") or r"C:\项目\commercial\analysis_internal.token"
CODE_RE = re.compile(r"^(60[0-9]{4}|68[0-9]{4}|00[0-9]{4}|30[0-9]{4}|8[0-9]{6})\.(SH|SZ|BJ)$")
RUN_RE = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")
JOB_RE = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")
MAX_BODY = 64 * 1024
LOOPBACK = ("127.0.0.1", "::1", "localhost")


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


def _token() -> str:
    tok = (os.environ.get(TOKEN_ENV) or "").strip()
    if tok:
        return tok
    try:
        with open(TOKEN_FILE, "r", encoding="utf-8-sig") as f:
            return f.read().strip()
    except Exception:
        return ""


def _deny(handler):
    _json(handler, {"ok": False, "error": "forbidden: 需 X-Analysis-Token（仅本机）"}, 403)


def _authorized(handler) -> bool:
    addr = getattr(handler, "client_address", None)
    ip = addr[0] if addr else ""
    if ip not in LOOPBACK:
        return False
    expected = _token()
    if not expected:
        return False
    provided = (handler.headers.get("X-Analysis-Token") or "").strip()
    return bool(provided) and hmac.compare_digest(provided, expected)


def _qs(qs):
    out = {}
    for part in (qs or "").split("&"):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k] = v
    return out


def _db_rows(sql, args=()):
    import sqlite3
    if not os.path.exists(DB):
        return []
    try:
        con = sqlite3.connect("file:%s?mode=ro" % DB.replace("\\", "/"), uri=True)
        con.row_factory = sqlite3.Row
        rows = [dict(r) for r in con.execute(sql, args).fetchall()]
        con.close()
        return rows
    except Exception:
        return []


def _run_dir(run_id):
    if not RUN_RE.match(run_id or ""):
        return None
    p = os.path.join(RUNS, run_id)
    return p if os.path.isdir(p) else None


def _watchlist():
    codes = []
    try:
        p = os.path.join(ROOT, "analysis", "watchlist.txt")
        if os.path.exists(p):
            for ln in open(p, encoding="utf-8"):
                t = ln.strip()
                if t and not t.startswith("#") and CODE_RE.match(t.split()[0].upper()):
                    codes.append(t.split()[0].upper())
    except Exception:
        pass
    return codes


def _queue_stats():
    out = {}
    for k in ("pending", "running", "done"):
        d = os.path.join(QUEUE, k)
        out[k] = len([f for f in os.listdir(d) if f.endswith(".json")]) if os.path.isdir(d) else 0
    return out


def _latest_run(code):
    rows = _db_rows("SELECT * FROM runs WHERE code=? ORDER BY started DESC LIMIT 1", (code,))
    if not rows:
        return None
    run = rows[0]
    d = _run_dir(run.get("run_id") or "")
    summary = None
    if d:
        try:
            aj = json.load(open(os.path.join(d, "analysis.json"), encoding="utf-8"))
            snap = aj.get("snapshot") or {}
            summary = {
                "rating": (aj.get("verdict") or {}).get("rating"),
                "score": (aj.get("verdict") or {}).get("score"),
                "coverage": snap.get("coverage_pct"),
                "as_of": (aj.get("meta") or {}).get("as_of"),
                "domains": {k: {"as_of": v.get("as_of"), "rows": v.get("rows"),
                                "status": v.get("status")}
                            for k, v in (snap.get("domains") or {}).items()},
                "required_missing": snap.get("required_missing") or [],
                "events": len(aj.get("events") or []),
                "checks": aj.get("checks"),
            }
        except Exception as e:
            summary = {"error": str(e)[:120]}
    return {"run": run, "summary": summary}


def _submit(codes, llm=False, push=False):
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from analysis import queue as aq
    jobs = []
    for code in codes:
        r = aq.submit(code, llm=llm, push=push)
        jobs.append(r)
    return jobs


def _audit(action, detail, result="ok"):
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from commercial import audit
        audit.append(action, tenant="", actor="hermes", resource="analysis", result=result, detail=detail)
    except Exception:
        pass


def _read_body(handler):
    ln = int(handler.headers.get("Content-Length") or 0)
    if ln > MAX_BODY:
        raise ValueError("body too large")
    raw = handler.rfile.read(ln).decode("utf-8") if ln else "{}"
    return json.loads(raw or "{}")


def handle_get(handler, path, qs):
    if not _token():
        return _json(handler, {"ok": False, "error": "internal token 未配置（fail-closed）"}, 503)
    if not _authorized(handler):
        return _deny(handler)
    q = _qs(qs)
    sub = path[len(BASE):] if path.startswith(BASE) else path

    if sub == "/health":
        last = _db_rows("SELECT run_id,code,name,as_of,status,rating,coverage,finished "
                        "FROM runs ORDER BY started DESC LIMIT 1")
        return _json(handler, {"ok": True, "db": os.path.exists(DB), "runs_dir": os.path.isdir(RUNS),
                               "queue": _queue_stats(), "last_run": last[0] if last else None,
                               "token_configured": True})
    if sub == "/overview":
        codes = _watchlist()
        latest = []
        for c in codes:
            lr = _latest_run(c)
            latest.append({"code": c, **(lr or {})})
        recent = _db_rows("SELECT run_id,code,name,as_of,started,status,rating,coverage "
                          "FROM runs ORDER BY started DESC LIMIT 8")
        try:
            from analysis import review
            st = review.stats(None, path=DB)
        except Exception:
            st = {}
        return _json(handler, {"ok": True, "watchlist": codes, "latest": latest,
                               "recent_runs": recent, "queue": _queue_stats(),
                               "events": st})
    if sub == "/watchlist":
        return _json(handler, {"ok": True, "codes": _watchlist()})
    if sub == "/data-status":
        code = (q.get("code") or "").strip().upper()
        if not CODE_RE.match(code):
            return _json(handler, {"ok": False, "error": "非法代码（示例 600337.SH）"}, 400)
        lr = _latest_run(code)
        if not lr:
            return _json(handler, {"ok": True, "code": code, "has_run": False,
                                   "note": "本地暂无该股票分析运行记录"}, 200)
        return _json(handler, {"ok": True, "code": code, "has_run": True, **lr})
    if sub == "/report":
        run_id = (q.get("run_id") or "").strip()
        fmt = (q.get("format") or "md").strip().lower()
        d = _run_dir(run_id)
        if not d:
            return _json(handler, {"ok": False, "error": "run 不存在"}, 404)
        name = "h5.html" if fmt == "h5" else "report.md"
        p = os.path.join(d, name)
        if not os.path.exists(p):
            return _json(handler, {"ok": False, "error": "%s 不存在" % name}, 404)
        if os.path.getsize(p) > 512 * 1024:
            return _json(handler, {"ok": False, "error": "文件过大"}, 413)
        return _json(handler, {"ok": True, "run_id": run_id, "format": fmt,
                               "content": open(p, encoding="utf-8").read()})
    if sub == "/events":
        code = (q.get("code") or "").strip().upper()
        if code and not CODE_RE.match(code):
            return _json(handler, {"ok": False, "error": "非法代码"}, 400)
        limit = max(1, min(int(q.get("limit", 30) or 30), 200))
        status = q.get("status", "pending")
        if status not in ("all", "pending", "approved", "rejected"):
            status = "all"
        try:
            from analysis import review
            items = review.list_events(code or None, limit, status, path=DB)
            st = review.stats(code or None, path=DB)
        except Exception as e:
            return _json(handler, {"ok": False, "error": "事件库读取失败: " + str(e)[:120]}, 500)
        out = [{"event_id": e.get("event_id"), "date": e.get("date"), "category": e.get("category"),
                "category_final": e.get("category_final"), "title": e.get("title"),
                "reviewed": e.get("reviewed")} for e in items]
        return _json(handler, {"ok": True, "events": out, "stats": st})
    if sub.startswith("/job/"):
        job_id = sub[len("/job/"):].strip()
        if not JOB_RE.match(job_id):
            return _json(handler, {"ok": False, "error": "非法 job_id"}, 400)
        for k in ("running", "done", "pending"):
            p = os.path.join(QUEUE, k, job_id + ".json")
            if os.path.exists(p):
                try:
                    data = json.load(open(p, encoding="utf-8"))
                except Exception as e:
                    return _json(handler, {"ok": False, "error": "job 读取失败: " + str(e)[:100]}, 500)
                return _json(handler, {"ok": True, "state": k, "job": data})
        return _json(handler, {"ok": True, "state": "unknown", "job": None}, 200)
    return _json(handler, {"ok": False, "error": "unknown internal api: " + sub}, 404)


def handle_post(handler, path, qs):
    if not _token():
        return _json(handler, {"ok": False, "error": "internal token 未配置（fail-closed）"}, 503)
    if not _authorized(handler):
        return _deny(handler)
    sub = path[len(BASE):] if path.startswith(BASE) else path
    if sub not in ("/analyze", "/batch"):
        return _json(handler, {"ok": False, "error": "unknown internal post: " + sub}, 404)
    try:
        body = _read_body(handler)
    except ValueError as e:
        return _json(handler, {"ok": False, "error": str(e)}, 413)
    except Exception:
        return _json(handler, {"ok": False, "error": "bad json"}, 400)

    llm = bool(body.get("llm", False))
    push = bool(body.get("push", False))
    if sub == "/analyze":
        code = str(body.get("code") or "").strip().upper()
        if not CODE_RE.match(code):
            return _json(handler, {"ok": False, "error": "非法代码（示例 600337.SH）"}, 400)
        try:
            jobs = _submit([code], llm=llm, push=push)
        except Exception as e:
            return _json(handler, {"ok": False, "error": "入队失败: " + str(e)[:120]}, 500)
        _audit("analysis_submit", {"code": code, "job_id": jobs[0].get("job_id") if jobs else None,
                                   "source": "hermes_mcp"})
        return _json(handler, {"ok": bool(jobs and jobs[0].get("ok")), "code": code,
                               "job_id": jobs[0].get("job_id") if jobs else None,
                               "llm": llm, "push": push,
                               "note": "已入队；由 analysis.batch --queue 或盘后批消费"}, 202)
    codes = body.get("codes") or []
    if not isinstance(codes, list) or not codes:
        return _json(handler, {"ok": False, "error": "codes 必须为非空数组"}, 400)
    codes = [str(c).strip().upper() for c in codes]
    codes = list(dict.fromkeys(codes))  # 去重保序，避免重复入队
    bad = [c for c in codes if not CODE_RE.match(c)]
    if bad:
        return _json(handler, {"ok": False, "error": "非法代码: " + ",".join(bad[:5])}, 400)
    if len(codes) > 20:
        return _json(handler, {"ok": False, "error": "单批最多 20 只"}, 400)
    try:
        jobs = _submit(codes, llm=llm, push=push)
    except Exception as e:
        return _json(handler, {"ok": False, "error": "入队失败: " + str(e)[:120]}, 500)
    _audit("analysis_batch_submit", {"codes": codes, "count": len(codes),
                                     "source": "hermes_mcp"})
    return _json(handler, {"ok": True, "count": len(codes), "jobs": jobs}, 202)
