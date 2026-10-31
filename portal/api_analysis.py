# -*- coding: utf-8 -*-
"""个股分析门户入口（/api/analysis/*）——独立文件，portal_api.py 只加早分派。

GET（只读）：
  /api/analysis/health               流水线健康（db/runs/queue/cache 概要）
  /api/analysis/list?code=&limit=    运行台账（SQLite analysis.db）
  /api/analysis/latest?code=         最近一次运行（含 analysis.json 摘要）
  /api/analysis/report?run_id=       返回 report.md 文本（限 300KB）
  /api/analysis/queue                队列计数
POST（写队列，需 X-Portal-Token，已加入 _MUTATION_POSTS）：
  /api/analysis/submit   {"code":"600337.SH","llm":false,"push":false}
"""
import json
import os
import re
import sqlite3

ROOT = r"C:\项目\老田的AI助手"
WORK = os.environ.get("ANALYSIS_WORK", r"C:\项目\analysis_work")  # 与 analysis/config.py 同源口径（ANALYSIS_WORK）
DB = os.path.join(WORK, "analysis.db")
RUNS = os.path.join(WORK, "runs")
QUEUE = os.path.join(WORK, "queue")
DELIVER = r"C:\项目\deliverables"

CODE_RE = re.compile(r"^(60[0-9]{4}|68[0-9]{4}|00[0-9]{4}|30[0-9]{4}|8[0-9]{6})\.(SH|SZ|BJ)$")
RUN_RE = re.compile(r"^[A-Za-z0-9_\-]{6,80}$")


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


def _db_rows(sql, args=()):
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
    if os.path.isdir(p):
        return p
    return None


def handle_get(handler, path, qs):
    q = _qs(qs)
    if path == "/api/analysis/watchlist":
        codes = []
        try:
            p = os.path.join(ROOT, "analysis", "watchlist.txt")
            if os.path.exists(p):
                for ln in open(p, encoding="utf-8"):
                    t = ln.strip()
                    if t and not t.startswith("#") and CODE_RE.match(t.split()[0].upper()):
                        codes.append(t.split()[0].upper())
        except Exception as e:
            return _json(handler, {"ok": False, "error": "watchlist 读取失败: " + str(e)[:80]}, 500)
        return _json(handler, {"ok": True, "codes": codes})

    if path == "/api/analysis/health":
        stats = {}
        for k in ("pending", "running", "done"):
            d = os.path.join(QUEUE, k)
            stats[k] = len([f for f in os.listdir(d) if f.endswith(".json")]) if os.path.isdir(d) else 0
        last = _db_rows("SELECT run_id,code,name,as_of,status,rating,coverage,finished FROM runs ORDER BY started DESC LIMIT 1")
        return _json(handler, {"ok": True, "db": os.path.exists(DB), "runs_dir": os.path.isdir(RUNS),
                               "queue": stats, "last_run": last[0] if last else None,
                               "deliver_root": DELIVER})
    if path == "/api/analysis/list":
        limit = max(1, min(int(q.get("limit", 20) or 20), 100))
        code = q.get("code", "")
        if code:
            rows = _db_rows("SELECT run_id,code,name,as_of,started,finished,status,rating,coverage,artifacts_dir "
                            "FROM runs WHERE code=? ORDER BY started DESC LIMIT ?", (code, limit))
        else:
            rows = _db_rows("SELECT run_id,code,name,as_of,started,finished,status,rating,coverage,artifacts_dir "
                            "FROM runs ORDER BY started DESC LIMIT ?", (limit,))
        return _json(handler, {"ok": True, "count": len(rows), "runs": rows})
    if path == "/api/analysis/latest":
        code = q.get("code", "")
        if not CODE_RE.match(code or ""):
            return _json(handler, {"ok": False, "error": "非法代码"}, 400)
        rows = _db_rows("SELECT * FROM runs WHERE code=? ORDER BY started DESC LIMIT 1", (code,))
        if not rows:
            return _json(handler, {"ok": True, "code": code, "run": None})
        run = rows[0]
        d = _run_dir(run.get("run_id") or "")
        summary = None
        if d:
            try:
                aj = json.load(open(os.path.join(d, "analysis.json"), encoding="utf-8"))
                summary = {"rating": (aj.get("verdict") or {}).get("rating"),
                           "thesis": (aj.get("verdict") or {}).get("thesis"),
                           "coverage": (aj.get("snapshot") or {}).get("coverage_pct"),
                           "checks": aj.get("checks"), "events": len(aj.get("events") or []),
                           "scenarios": aj.get("scenarios"), "watch": aj.get("watch"),
                           "artifacts": aj.get("artifacts")}
            except Exception as e:
                summary = {"error": str(e)[:120]}
        return _json(handler, {"ok": True, "code": code, "run": run, "summary": summary,
                               "run_dir": d, "archive": run.get("artifacts_dir")})
    if path == "/api/analysis/report":
        d = _run_dir(q.get("run_id", ""))
        if not d:
            return _json(handler, {"ok": False, "error": "run 不存在"}, 404)
        p = os.path.join(d, "report.md")
        if not os.path.exists(p):
            return _json(handler, {"ok": False, "error": "报告不存在"}, 404)
        if os.path.getsize(p) > 300 * 1024:
            return _json(handler, {"ok": False, "error": "报告过大"}, 413)
        return _json(handler, {"ok": True, "run_id": q.get("run_id"), "markdown": open(p, encoding="utf-8").read()})
    if path == "/api/analysis/queue":
        stats = {}
        for k in ("pending", "running", "done"):
            d = os.path.join(QUEUE, k)
            stats[k] = sorted([f for f in os.listdir(d) if f.endswith(".json")])[-5:] if os.path.isdir(d) else []
        return _json(handler, {"ok": True, "queue": stats})
    if path == "/api/analysis/events":
        limit = max(1, min(int(q.get("limit", 30) or 30), 200))
        code = q.get("code", "")
        status = q.get("status", "pending")
        if code and not CODE_RE.match(code):
            return _json(handler, {"ok": False, "error": "非法代码"}, 400)
        try:
            import sys
            if ROOT not in sys.path:
                sys.path.insert(0, ROOT)
            from analysis import review
            items = review.list_events(code or None, limit, status if status in ("all", "pending", "approved", "rejected") else "all", path=DB)
            st = review.stats(code or None, path=DB)
        except Exception as e:
            return _json(handler, {"ok": False, "error": "事件库读取失败: " + str(e)[:120]}, 500)
        out = [{"event_id": e.get("event_id"), "date": e.get("date"), "category": e.get("category"),
                "category_final": e.get("category_final"), "title": e.get("title"), "url": e.get("url"),
                "reviewed": e.get("reviewed"), "reviewed_at": e.get("reviewed_at"),
                "review_note": e.get("review_note"), "confidence": e.get("confidence")} for e in items]
        return _json(handler, {"ok": True, "events": out, "total": st.get("total"), "pending_total": st.get("pending"),
                               "approved": st.get("approved"), "rejected": st.get("rejected")})
    if path == "/api/analysis/h5":
        d = _run_dir(q.get("run_id", ""))
        if not d:
            return _json(handler, {"ok": False, "error": "run 不存在"}, 404)
        p = os.path.join(d, "h5.html")
        if not os.path.exists(p):
            return _json(handler, {"ok": False, "error": "H5 不存在"}, 404)
        body = open(p, encoding="utf-8").read()
        try:
            handler._html(body)
        except Exception:
            data = body.encode("utf-8")
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(data)))
            handler.end_headers()
            handler.wfile.write(data)
        return
    return _json(handler, {"ok": False, "error": "unknown analysis api: " + path}, 404)


def _read_body(handler):
    ln = int(handler.headers.get("Content-Length") or 0)
    return json.loads(handler.rfile.read(ln).decode("utf-8") or "{}") if ln else {}


def handle_post(handler, path, qs):
    # H3（K-ORG-20261031-APIHARDEN）：进程内鉴权兜底。即使 portal_api 将来又出现
    # 早分支/其它调用方，analysis 写接口也不得绕过 token 闸门（审计 v2 H3）。
    if not handler._check_portal_token():
        return _json(handler, {"ok": False,
                               "error": "forbidden: 缺失或不匹配的 X-Portal-Token"}, 403)
    if path not in ("/api/analysis/submit", "/api/analysis/review"):
        return _json(handler, {"ok": False, "error": "unknown analysis post"}, 404)
    try:
        body = _read_body(handler)
    except Exception:
        return _json(handler, {"ok": False, "error": "bad json"}, 400)
    import sys
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)

    if path == "/api/analysis/review":
        event_id = str(body.get("event_id") or "").strip()
        action = str(body.get("action") or "").strip().lower()
        if not event_id or action not in ("approve", "reject"):
            return _json(handler, {"ok": False, "error": "需要 event_id 与 action(approve/reject)"}, 400)
        try:
            from analysis import review
            r = review.apply(event_id, action, category=body.get("category"), note=body.get("note"), path=DB)
        except Exception as e:
            return _json(handler, {"ok": False, "error": "复核失败: " + str(e)[:120]}, 500)
        return _json(handler, r, 200 if r.get("ok") else 404)

    code = str(body.get("code") or "").strip().upper()
    if not CODE_RE.match(code):
        return _json(handler, {"ok": False, "error": "非法代码（示例 600337.SH）"}, 400)
    llm = bool(body.get("llm", False))
    push = bool(body.get("push", False))
    try:
        from analysis import queue as aq
        r = aq.submit(code, llm=llm, push=push)
    except Exception as e:
        return _json(handler, {"ok": False, "error": "队列写入失败: " + str(e)[:120]}, 500)
    return _json(handler, {"ok": bool(r.get("ok")), "job_id": r.get("job_id"), "code": code,
                           "llm": llm, "note": "已入队；由 analysis.batch --queue 或盘后批消费"}, 202)
