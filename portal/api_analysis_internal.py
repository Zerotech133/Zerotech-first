# -*- coding: utf-8 -*-
"""个股分析 · 内部网关（H1）：供 Hermes MCP / 本地自动化调用。

安全边界（fail-closed）：
- 仅接受 loopback（127.0.0.1/::1）来源；若带 `X-Portal-Proxy`（经 8734 反代），
  还要求 `X-Real-IP` 为回环，防止 LAN 会话借反代绕过 loopback 边界；
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

H2b 条件选股（只读计算，不自动批量分析；引擎 = analysis.screen_parse / analysis.screener，
H2a 未合并前缺失 → 503 fail-closed）：
  POST /api/analysis/internal/screen/parse  {"text": "自然语言筛选条件"}
  POST /api/analysis/internal/screen/run    {"dsl": {...}} 或 {"text": "..."}
  GET  /api/analysis/internal/screen/fields
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
SCREEN_MAX_TEXT = 2000
SCREEN_DISCLAIMER = "筛选结果仅供研究，不构成投资建议；历史数据不代表未来。"
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
    """loopback + 专属 token；反代来源还须 X-Real-IP 为回环（P1 修复）。

    经 8734 的请求 client_address 恒为 127.0.0.1（nginx 容器反代），只判 peer
    会让 LAN 会话绕过 loopback 边界；nginx 会注入 X-Portal-Proxy:1 与
    X-Real-IP=$remote_addr，故与 portal_api._client_loopback 同口径复核。
    """
    addr = getattr(handler, "client_address", None)
    ip = addr[0] if addr else ""
    if ip not in LOOPBACK:
        return False
    headers = handler.headers
    if (headers.get("X-Portal-Proxy") or "").strip():
        real_ip = (headers.get("X-Real-IP") or "").strip()
        if real_ip not in LOOPBACK:
            return False
    expected = _token()
    if not expected:
        return False
    provided = (headers.get("X-Analysis-Token") or "").strip()
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


def _audit(action, detail, result="ok") -> bool:
    """写审计；成功 True / 失败 False（调用方 fail-closed 处理）。"""
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from commercial import audit
        audit.append(action, tenant="", actor="hermes", resource="analysis", result=result, detail=detail)
        return True
    except Exception:
        return False


def _read_body(handler):
    ln = int(handler.headers.get("Content-Length") or 0)
    if ln > MAX_BODY:
        raise ValueError("body too large")
    raw = handler.rfile.read(ln).decode("utf-8") if ln else "{}"
    return json.loads(raw or "{}")


# ---------------- H2b 条件选股（惰性加载，缺失 fail-closed） ----------------
def _import_analysis(name):
    """按标准路径导入 analysis.<name>；生产进程以 ROOT 为准。"""
    import importlib
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    return importlib.import_module("analysis." + name)


def _load_screen_parse():
    """返回 analysis.screen_parse 模块；缺失/导入失败 → None（端点回 503）。"""
    try:
        return _import_analysis("screen_parse")
    except Exception:
        return None


def _load_screener():
    """返回 analysis.screener 模块；缺失/导入失败 → None（端点回 503）。"""
    try:
        return _import_analysis("screener")
    except Exception:
        return None


def _screen_error(handler, status, error, **extra):
    obj = {"ok": False, "error": error}
    obj.update(extra)
    return _json(handler, obj, status)


def _screen_text(body):
    """提取并校验 text；非法返回 (None, 错误说明)。"""
    text = body.get("text")
    if not isinstance(text, str) or not text.strip():
        return None, "text 不能为空"
    text = text.strip()
    if len(text) > SCREEN_MAX_TEXT:
        return None, "text 过长（≤%d 字）" % SCREEN_MAX_TEXT
    return text, ""


def _screen_industries(body):
    inds = body.get("industries")
    if not isinstance(inds, list):
        return None
    out = [str(x).strip() for x in inds if str(x or "").strip()]
    return out[:60] or None


def _post_screen_parse(handler, body):
    sp = _load_screen_parse()
    if sp is None:
        return _screen_error(handler, 503, "选股解析引擎未就绪（analysis.screen_parse 缺失）")
    text, err = _screen_text(body)
    if err:
        return _screen_error(handler, 400, err)
    kw = {}
    inds = _screen_industries(body)
    if inds:
        kw["industries"] = inds
    try:
        r = sp.parse(text, **kw)
    except Exception as e:  # noqa: BLE001
        return _screen_error(handler, 500, "解析失败: " + str(e)[:120])
    if not isinstance(r, dict):
        return _screen_error(handler, 500, "解析器返回非对象")
    if r.get("ok") is False:
        return _json(handler, {
            "ok": False,
            "error": str(r.get("error") or "无法解析为筛选条件"),
            "errors": r.get("errors") or [],
            "notes": r.get("notes") or [],
            "unmatched": r.get("unmatched") or [],
            "spec": r.get("spec"),
        }, 400)
    return _json(handler, {
        "ok": True,
        "spec": r.get("spec"),
        "notes": r.get("notes") or [],
        "unmatched": r.get("unmatched") or [],
        "source": r.get("source") or "rule",
        "text": text,
    })


def _post_screen_run(handler, body):
    sc = _load_screener()
    if sc is None:
        return _screen_error(handler, 503, "选股引擎未就绪（analysis.screener 缺失）")
    dsl = body.get("dsl")
    raw_text = body.get("text")
    has_dsl = isinstance(dsl, dict)
    has_text = isinstance(raw_text, str) and bool(raw_text.strip())
    if dsl is not None and not has_dsl:
        return _screen_error(handler, 400, "dsl 必须为对象")
    if has_dsl and has_text:
        return _screen_error(handler, 400, "dsl 与 text 只能二选一")
    if not has_dsl and not has_text:
        return _screen_error(handler, 400, "需要 dsl 或 text")
    parse_meta = None
    if has_text:
        sp = _load_screen_parse()
        if sp is None:
            return _screen_error(handler, 503, "选股解析引擎未就绪（analysis.screen_parse 缺失）")
        text, err = _screen_text(body)
        if err:
            return _screen_error(handler, 400, err)
        try:
            pr = sp.parse(text)
        except Exception as e:  # noqa: BLE001
            return _screen_error(handler, 500, "解析失败: " + str(e)[:120])
        if not isinstance(pr, dict):
            return _screen_error(handler, 500, "解析器返回非对象")
        if pr.get("ok") is False:
            return _json(handler, {
                "ok": False,
                "error": str(pr.get("error") or "无法解析为筛选条件"),
                "errors": pr.get("errors") or [],
                "notes": pr.get("notes") or [],
                "unmatched": pr.get("unmatched") or [],
            }, 400)
        spec = pr.get("spec")
        parse_meta = {"source": pr.get("source") or "rule",
                      "notes": pr.get("notes") or [],
                      "unmatched": pr.get("unmatched") or []}
    else:
        spec = dsl
    try:
        v = sc.validate(spec)
    except Exception as e:  # noqa: BLE001
        return _screen_error(handler, 500, "DSL 校验失败: " + str(e)[:120])
    if not isinstance(v, dict) or not v.get("ok"):
        return _json(handler, {
            "ok": False, "error": "DSL 校验失败",
            "errors": (v or {}).get("errors") or ["validate 返回非法结果"],
        }, 400)
    spec = v.get("spec") or spec
    try:
        r = sc.run(spec)
    except Exception as e:  # noqa: BLE001
        return _screen_error(handler, 500, "筛选执行失败: " + str(e)[:160])
    if not isinstance(r, dict):
        return _screen_error(handler, 500, "筛选引擎返回非对象")
    if r.get("ok") is False:
        return _screen_error(handler, 500, "筛选执行失败",
                             errors=r.get("errors") or [], warnings=r.get("warnings") or [])
    out = {
        "ok": True,
        "as_of": r.get("as_of"),
        "universe": r.get("universe"),
        "universe_size": r.get("universe_size"),
        "matched": r.get("matched"),
        "returned": r.get("returned", len(r.get("candidates") or [])),
        "candidates": r.get("candidates") or [],
        "warnings": r.get("warnings") or [],
        "disclaimer": r.get("disclaimer") or SCREEN_DISCLAIMER,
        "research_only": True,
        "spec": r.get("spec") or spec,
        "auto_batch": False,
    }
    if parse_meta is not None:
        out["parse"] = parse_meta
    return _json(handler, out)


def _get_screen_fields(handler):
    sc = _load_screener()
    if sc is None:
        return _screen_error(handler, 503, "选股引擎未就绪（analysis.screener 缺失）")
    raw = getattr(sc, "FIELDS", None)
    if not isinstance(raw, dict) or not raw:
        return _screen_error(handler, 503, "字段白名单不可用（screener.FIELDS 缺失）")
    fields = []
    for name in sorted(raw):
        meta = raw.get(name)
        meta = meta if isinstance(meta, dict) else {}
        fields.append({"name": name,
                       "label": str(meta.get("label") or name),
                       "kind": str(meta.get("kind") or "number")})
    return _json(handler, {
        "ok": True,
        "count": len(fields),
        "fields": fields,
        "universes": list(getattr(sc, "UNIVERSES", []) or []),
        "ops": list(getattr(sc, "OPS", []) or []),
        "disclaimer": SCREEN_DISCLAIMER,
    })


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
    if sub == "/screen/fields":
        return _get_screen_fields(handler)
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
    if sub not in ("/analyze", "/batch", "/screen/parse", "/screen/run"):
        return _json(handler, {"ok": False, "error": "unknown internal post: " + sub}, 404)
    try:
        body = _read_body(handler)
    except ValueError as e:
        return _json(handler, {"ok": False, "error": str(e)}, 413)
    except Exception:
        return _json(handler, {"ok": False, "error": "bad json"}, 400)
    if not isinstance(body, dict):
        return _json(handler, {"ok": False, "error": "body 必须为 JSON 对象"}, 400)
    if sub == "/screen/parse":
        return _post_screen_parse(handler, body)
    if sub == "/screen/run":
        return _post_screen_run(handler, body)

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
        job_id = jobs[0].get("job_id") if jobs else None
        if not _audit("analysis_submit", {"code": code, "job_id": job_id,
                                          "source": "hermes_mcp"}):
            # 审计失败 fail-closed：任务可能已入队，返回 job_id 供对账，但明确报错
            return _json(handler, {"ok": False, "error": "审计写入失败（fail-closed），任务已入队待人工对账",
                                   "code": code, "job_id": job_id}, 503)
        return _json(handler, {"ok": bool(jobs and jobs[0].get("ok")), "code": code,
                               "job_id": job_id,
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
    if not _audit("analysis_batch_submit", {"codes": codes, "count": len(codes),
                                            "source": "hermes_mcp"}):
        return _json(handler, {"ok": False, "error": "审计写入失败（fail-closed），任务已入队待人工对账",
                               "count": len(codes), "jobs": jobs}, 503)
    return _json(handler, {"ok": True, "count": len(codes), "jobs": jobs}, 202)
