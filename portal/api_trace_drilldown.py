# -*- coding: utf-8 -*-
"""A 方案交付物：轻量 trace + 血缘 下钻看板（只读）。

定位：把 agent_platform/observability/trace.py 采集的 traces.sqlite 接出来，
      让运维/排错能直接看到「最近 N 次调用」的输入/输出(元数据)/耗时/失败，
      并把 api_lineage.py 已建好的数据血缘端点复用进同一面板。

铁律：
  · 全程只读：traces.sqlite 仅 SELECT；lineage 仅 SELECT；A0 影子点评仅文件读。
  · 不写库、不碰 nginx 配置、不碰 10.205.0.118 生产服务器、不读 _vault 密钥。
  · 绑定 127.0.0.1:8106（与既有 api_*.py 同范式：ThreadingHTTPServer + nginx 反代）。

路由：
  GET /api/trace/            -> 返回 trace_drilldown.html（同目录静态文件）
  GET /api/trace/data        -> JSON：recent traces / trace 详情 / 汇总 / A0 / JEV
  GET /api/trace/lineage     -> JSON：复用 lineage 三表（breaks|runs|upstream|downstream）
  GET /api/trace/kg          -> JSON：Neo4j 图谱健康/漂移（B1：总量/Top/本体注册表漂移/保鲜探针）

启动：python api_trace_drilldown.py   （默认 8106；可选 --port）
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import urllib.parse
from datetime import datetime, timezone, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent            # ...\portal
STATION = ROOT.parent                             # ...\aios (E:\老田的AI助手 同名树)
TRACE_DB = STATION / "agent_platform" / "data" / "observability" / "traces.sqlite"
SHADOW_DIR = STATION / "agent_platform" / "data" / "shadow"
HTML_FILE = ROOT / "trace_drilldown.html"

DEFAULT_PORT = int(os.environ.get("TRACE_DRILLDOWN_PORT", "8106"))

# ── C2：受治理 TBox + 门禁裁决（ontology.gate） ───────────────────────
import sys as _sys_kg  # noqa: E402
_KG_QB = str(STATION / "quant_backtest")
if _KG_QB not in _sys_kg.path:
    _sys_kg.path.insert(0, _KG_QB)
import ontology as _ontology  # noqa: E402  受治理 TBox + gate()
import cq_registry as _cq_reg   # noqa: E402  Competency Questions 注册表 (C3)
import kg_cq as _kg_cq          # noqa: E402  CQ 回归引擎 (C3)


# ── traces.sqlite 只读查询 ────────────────────────────────────────────────
def _trace_conn():
    if not TRACE_DB.exists():
        raise FileNotFoundError(str(TRACE_DB))
    con = sqlite3.connect(str(TRACE_DB), timeout=5)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=1")  # 硬只读：任何写语句直接报错
    return con


def _parse_ts(s: str | None):
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.strptime(s, "%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None


def recent_traces(limit=50, name_filter="", days=30):
    """返回最近 traces 列表（按最新 created_at 排序）。可按 span name 子串过滤。"""
    con = _trace_conn()
    try:
        if name_filter:
            # 仅取含该 name 的 trace_id，再聚合
            rows = con.execute(
                """SELECT t.trace_id,
                          COUNT(*) AS spans,
                          MIN(t.start_ts) AS start_ts,
                          MAX(t.end_ts) AS end_ts,
                          SUM(CASE WHEN t.status='error' THEN 1 ELSE 0 END) AS errs,
                          MAX(t.created_at) AS created_at
                   FROM spans t
                   WHERE t.trace_id IN (
                       SELECT DISTINCT trace_id FROM spans WHERE name LIKE ?)
                   GROUP BY t.trace_id
                   ORDER BY created_at DESC LIMIT ?""",
                ("%" + name_filter + "%", int(limit)),
            ).fetchall()
        else:
            since = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%S")
            rows = con.execute(
                """SELECT trace_id, COUNT(*) AS spans,
                          MIN(start_ts) AS start_ts, MAX(end_ts) AS end_ts,
                          SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) AS errs,
                          MAX(created_at) AS created_at
                   FROM spans
                   WHERE start_ts >= ?
                   GROUP BY trace_id
                   ORDER BY created_at DESC LIMIT ?""",
                (since, int(limit)),
            ).fetchall()
        items = []
        for r in rows:
            items.append({
                "trace_id": r["trace_id"],
                "spans": r["spans"],
                "start": r["start_ts"],
                "end": r["end_ts"],
                "errors": int(r["errs"] or 0),
                "created_at": r["created_at"],
            })
        return items
    finally:
        con.close()


def trace_detail(trace_id):
    con = _trace_conn()
    try:
        rows = con.execute(
            """SELECT span_id, parent_span_id, name, kind, start_ts, end_ts,
                      status, attrs_json, run_id
               FROM spans WHERE trace_id=? ORDER BY start_ts""",
            (trace_id,),
        ).fetchall()
        spans = []
        for r in rows:
            a = {}
            try:
                a = json.loads(r["attrs_json"] or "{}")
            except ValueError:
                a = {}
            s = _parse_ts(r["start_ts"])
            e = _parse_ts(r["end_ts"])
            lat = None
            if s and e:
                lat = round((e - s).total_seconds() * 1000, 1)
            spans.append({
                "span_id": r["span_id"],
                "parent": r["parent_span_id"],
                "name": r["name"],
                "kind": r["kind"],
                "status": r["status"],
                "start": r["start_ts"],
                "end": r["end_ts"],
                "latency_ms": lat,
                "attrs": a,
                "run_id": r["run_id"],
            })
        return {"trace_id": trace_id, "count": len(spans), "spans": spans}
    finally:
        con.close()


# 排除测试污染 span：
#  ① 测试库标记 zhaoyun.test='true'（未来任何泄漏）；
#  ② 历史测试污染：裸错误签名('down'/'empty_reply'/'429 quota') 且无 zhaoyun.request_id
#     （生产真实错误即便降级也会带 request_id；裸签名+无 request_id 是测试专属指纹）。
# error 信息存于 attrs_json 的 $.error，非独立列。
NON_TEST = (
    "COALESCE(json_extract(attrs_json,'$.\"zhaoyun.test\"'),'') != 'true'"
    " AND NOT (name='llm.call' AND status='error'"
    "   AND json_extract(attrs_json,'$.error') IN ('down','empty_reply','429 quota')"
    "   AND COALESCE(json_extract(attrs_json,'$.\"zhaoyun.request_id\"'),'') = '')"
)


def summary(days=30):
    con = _trace_conn()
    try:
        since = (datetime.now() - timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%S")
        total = con.execute(
            "SELECT COUNT(*) FROM spans WHERE start_ts >= ? AND %s" % NON_TEST, (since,)
        ).fetchone()[0]
        errs = con.execute(
            "SELECT COUNT(*) FROM spans WHERE start_ts >= ? AND status='error' AND %s" % NON_TEST,
            (since,),
        ).fetchone()[0]
        top = con.execute(
            """SELECT name, COUNT(*) c,
                      SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) e
               FROM spans WHERE start_ts >= ? AND %s
               GROUP BY name ORDER BY e DESC, c DESC LIMIT 8""" % NON_TEST,
            (since,),
        ).fetchall()
        top_err = [{"name": r["name"], "total": r["c"], "errors": int(r["e"] or 0)}
                   for r in top]
        # 请求级 LLM 失败率：按 zhaoyun.request_id 聚合 llm.call 尝试；
        # 一次请求任一档成功即算成功，全档失败才算失败（消除级联降级虚高）。
        # 仅统计带 request_id 的受治理请求（旧版无 request_id 的 span 不计入）。
        req_rows = con.execute(
            """SELECT json_extract(attrs_json,'$.\"zhaoyun.request_id\"') AS req,
                      MAX(CASE WHEN status='ok' THEN 1 ELSE 0 END) AS ok_flag
               FROM spans
               WHERE name='llm.call' AND start_ts >= ? AND %s
                 AND json_extract(attrs_json,'$.\"zhaoyun.request_id\"') IS NOT NULL
                 AND json_extract(attrs_json,'$.\"zhaoyun.request_id\"') != ''
               GROUP BY req""" % NON_TEST,
            (since,),
        ).fetchall()
        req_total = len(req_rows)
        req_fail = sum(1 for r in req_rows if r["ok_flag"] == 0)
        llm_req_fail_rate = round(req_fail / req_total, 4) if req_total else 0.0
        return {
            "window_days": int(days),
            "total_spans": total,
            "error_spans": errs,
            "error_rate": round(errs / total, 4) if total else 0.0,
            "llm_requests": req_total,
            "llm_request_failures": req_fail,
            "llm_request_fail_rate": llm_req_fail_rate,
            "top_errored": top_err,
        }
    finally:
        con.close()


def a0_panel():
    """A0 判读：取最新影子点评文件 + 相关能力钻取 span（cap.drill/approval）。"""
    reviews = sorted(SHADOW_DIR.glob("a0_review_*.md")) if SHADOW_DIR.exists() else []
    latest = None
    if reviews:
        latest_file = reviews[-1]
        try:
            latest = {
                "file": latest_file.name,
                "mtime": datetime.fromtimestamp(
                    latest_file.stat().st_mtime, timezone.utc
                ).astimezone().strftime("%Y-%m-%d %H:%M:%S"),
                "text": latest_file.read_text(encoding="utf-8-sig", errors="replace")[:8000],
            }
        except OSError:
            latest = None
    con = _trace_conn()
    try:
        spans = con.execute(
            """SELECT name, status, start_ts, attrs_json FROM spans
               WHERE name IN ('cap.drill','approval','zhuge.consult')
               ORDER BY start_ts DESC LIMIT 10"""
        ).fetchall()
    finally:
        con.close()
    drill = [{"name": r["name"], "status": r["status"], "start": r["start_ts"],
              "attrs": _safe_json(r["attrs_json"])} for r in spans]
    return {"latest_review": latest, "related_spans": drill}


def jev_panel():
    """JEV 诚实面板：当前 JEV_GATE/QD/REVIEW 无专属 trace 埋点。"""
    con = _trace_conn()
    try:
        # 在 attrs 中检索任何 jev 关键字（预期为空 -> 印证「静默」根因）
        hits = con.execute(
            """SELECT trace_id, name, status, start_ts, attrs_json FROM spans
               WHERE attrs_json LIKE '%jev%' OR attrs_json LIKE '%JEV%'
               ORDER BY start_ts DESC LIMIT 10"""
        ).fetchall()
    finally:
        con.close()
    return {
        "instrumented": False,
        "note": ("JEV_GATE/JEV_QD/JEV_REVIEW 运行体当前未向 traces 写专属 span；"
                 "其「快速判断」底层走通用 llm.call，但无席位标记，无法按 JEV 过滤。"
                 "这正是组织看板「静默」的根因之一。"),
        "trace_hits": [{"trace_id": r["trace_id"], "name": r["name"],
                        "status": r["status"], "start": r["start_ts"]} for r in hits],
    }


def _safe_json(s):
    try:
        return json.loads(s or "{}")
    except ValueError:
        return {}


# ── lineage 复用（只读 Postgres 5442 血缘三表）────────────────────────────
def _lineage_password() -> str:
    pwd = os.environ.get("LINEAGE_PASSWORD", "").strip()
    if pwd:
        return pwd
    env_local = STATION / "quant_backtest" / ".env.local"
    if env_local.exists():
        for line in env_local.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.startswith("LINEAGE_PASSWORD=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("LINEAGE_PASSWORD 未配置（env 或 quant_backtest/.env.local）")


def lineage_query(view="runs", table="", limit=50):
    try:
        import psycopg2
    except ImportError:
        return {"ok": False, "error": "psycopg2 未安装"}
    dsn = os.environ.get("LINEAGE_DSN") or (
        "host=127.0.0.1 port=5442 user=quant_user dbname=quant_system password="
        + _lineage_password()
    )
    try:
        con = psycopg2.connect(dsn, connect_timeout=5)
        cur = con.cursor()
        if view == "runs":
            cur.execute(
                "SELECT id, job, step, started_at, ended_at, status "
                "FROM lineage.job_run ORDER BY started_at DESC LIMIT %s", (int(limit),))
            cols = ["id", "job", "step", "started_at", "ended_at", "status"]
        elif view == "breaks":
            cur.execute(
                "SELECT e.output_table, j.status, j.started_at, j.ended_at, j.job "
                "FROM lineage.lineage_edge e JOIN lineage.job_run j ON j.id=e.job_run_id "
                "WHERE j.status <> 'ok' ORDER BY j.started_at DESC LIMIT %s", (int(limit),))
            cols = ["output_table", "status", "started_at", "ended_at", "job"]
        elif view in ("upstream", "downstream") and table:
            if view == "upstream":
                cur.execute(
                    "SELECT e.output_table, e.input_table, e.transform, j.job, j.started_at "
                    "FROM lineage.lineage_edge e JOIN lineage.job_run j ON j.id=e.job_run_id "
                    "WHERE e.input_table=%s ORDER BY j.started_at DESC LIMIT %s",
                    (table, int(limit)))
            else:
                cur.execute(
                    "SELECT e.output_table, e.input_table, e.transform, j.job, j.started_at "
                    "FROM lineage.lineage_edge e JOIN lineage.job_run j ON j.id=e.job_run_id "
                    "WHERE e.output_table=%s ORDER BY j.started_at DESC LIMIT %s",
                    (table, int(limit)))
            cols = ["output_table", "input_table", "transform", "job", "started_at"]
        else:
            return {"ok": False, "error": "view=upstream/downstream 必传 table=<URN>"}
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return {"ok": True, "view": view, "table": table, "count": len(rows), "rows": rows}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:200]}


# ── Neo4j 图谱健康 / 漂移（只读，B1 方案）─────────────────────────────
def _kg_creds():
    """从 quant_backtest/kg_schema 解析 Neo4j 凭据（env → .env.local），不打印密码。"""
    import sys as _sys
    qb = str(STATION / "quant_backtest")
    if qb not in _sys.path:
        _sys.path.insert(0, qb)
    import kg_schema as S  # noqa: F401 触发其凭据加载（不落盘、不打印）
    return S.NEO4J_URI, S.NEO4J_USER, S.NEO4J_PASSWORD, getattr(S, "NEO4J_DB", "neo4j")


def _registry_sets():
    """返回本体注册表里的预期标签 / 关系集合（来自 ontology 常量）。"""
    import sys as _sys
    qb = str(STATION / "quant_backtest")
    if qb not in _sys.path:
        _sys.path.insert(0, qb)
    import kg_schema as S

    def _co(v):
        return v[0] if isinstance(v, (tuple, list)) else v

    labels = {_co(v) for k, v in vars(S).items()
              if k.startswith("LBL_") and isinstance(v, (str, tuple, list))}
    rels = {_co(v) for k, v in vars(S).items()
            if k.startswith("REL_") and isinstance(v, (str, tuple, list))}
    return labels, rels


def _kg_gate_result(all_lbl, all_rel, fresh):
    """用 ontology 视角的预期（受治理 TBox）构造 mini snapshot 并裁决门禁。

    复用调用方已建立的 Neo4j session 实况（all_lbl/all_rel/fresh），
    不重复连接；异常时降级为 FAIL 并返回可读原因。
    """
    try:
        exp_lbl = set(_ontology.NODE_TYPES.keys())
        exp_rel = set(_ontology.EDGE_TYPES.keys())
        mini = {
            "ok": True,
            "drift": {
                "unregistered_labels": sorted(all_lbl - exp_lbl),
                "unregistered_rels": sorted(all_rel - exp_rel),
                "governance_gap": _ontology._governance_gap(),
            },
            "freshness": fresh,
        }
        return _ontology.gate(snap=mini)
    except Exception as e:  # noqa: BLE001
        return {"passed": False, "exit_code": 1, "rules": [
            {"id": "gate_error", "level": "FAIL", "passed": False,
             "actual": str(e)[:120], "threshold": "门禁可执行",
             "msg": "门禁裁决异常"}],
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S")}


def _kg_cq_result(session):
    """C3：Competency Questions 能力回归，复用调用方已连 Neo4j session。

    session 不可为 None（本函数仅在已连图时调用）。返回 kg_cq.assess 摘要；
    异常降级为 passed=False 并给出可读原因。无 DB 时由 kg_health 不调用本函数。
    """
    try:
        return _kg_cq.assess(cqs=_cq_reg.all_cqs(), session=session)
    except Exception as e:  # noqa: BLE001
        return {
            "passed": False, "exit_code": 1, "total": len(_cq_reg.all_cqs()),
            "schema_checked": len(_cq_reg.all_cqs()), "schema_failures": 0,
            "live_ran": 0, "live_skipped": 0, "live_failures": 0,
            "schema_issues": [], "live": None,
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "note": "CQ 回归异常: %s" % str(e)[:200],
        }


def kg_health():
    """只读健康/漂移：总量 / Top 标签 / Top 关系 / 本体注册表漂移 / 保鲜探针。

    全部走 counts store 或 schema 过程，瞬时返回；不写库、不碰生产服务器。
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        return {"ok": False, "error": "neo4j driver 未安装"}
    try:
        uri, user, pw, db = _kg_creds()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "无法解析 Neo4j 凭据: %s" % e}
    if not pw:
        return {"ok": False, "error": "Neo4j 口令为空（KG_NEO4J_PASSWORD 未配置）"}
    drv = None
    try:
        drv = GraphDatabase.driver(uri, auth=(user, pw), connection_timeout=5)
        with drv.session(database=db) as s:
            node_total = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
            rel_total = s.run("MATCH ()-[r]->() RETURN count(r) AS c").single()["c"]
            labels = [(r["lbl"], r["c"]) for r in
                      s.run("MATCH (n) RETURN labels(n)[0] AS lbl, count(*) AS c "
                            "ORDER BY c DESC LIMIT 25").data()]
            rels = [(r["t"], r["c"]) for r in
                    s.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c "
                          "ORDER BY c DESC LIMIT 25").data()]
            all_lbl = {r["label"] for r in
                       s.run("CALL db.labels() YIELD label RETURN label").data()}
            all_rel = {r["relationshipType"] for r in
                       s.run("CALL db.relationshipTypes() YIELD relationshipType "
                             "RETURN relationshipType").data()}
            exp_lbl, exp_rel = _registry_sets()
            fresh = {}
            try:
                rr = s.run("MATCH (n:Security) RETURN max(n.list_date) AS v LIMIT 1").single()
                fresh["market_max_list_date"] = rr["v"] if rr else None
            except Exception:
                fresh["market_max_list_date"] = None
            try:
                rr = s.run("MATCH (e:Event) RETURN max(e.pub_time) AS v LIMIT 1").single()
                fresh["event_max_pub_time"] = rr["v"] if rr else None
            except Exception:
                fresh["event_max_pub_time"] = None
            return {
                "ok": True,
                "uri": uri, "db": db,
                "node_total": node_total, "rel_total": rel_total,
                "top_labels": [{"label": l, "count": c} for l, c in labels],
                "top_rels": [{"type": t, "count": c} for t, c in rels],
                "registry": {
                    "expected_label_count": len(exp_lbl),
                    "expected_rel_count": len(exp_rel),
                    "graph_label_count": len(all_lbl),
                    "graph_rel_count": len(all_rel),
                    # 图里有、但本体未注册（K264 旁路防线需关注）
                    "drift_extra_labels": sorted(all_lbl - exp_lbl),
                    "drift_extra_rels": sorted(all_rel - exp_rel),
                    # 本体注册、但图里没有（断边/未建）
                    "drift_missing_labels": sorted(exp_lbl - all_lbl),
                    "drift_missing_rels": sorted(exp_rel - all_rel),
                },
                "freshness": fresh,
                # C2：门禁裁决（复用本 session 图实况，不重连）
                "gate": _kg_gate_result(all_lbl, all_rel, fresh),
                # C3：Competency Questions 能力回归（复用本 session，不重连）
                "cq": _kg_cq_result(s),
            }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)[:300]}
    finally:
        if drv:
            drv.close()


# ── HTTP handler ──────────────────────────────────────────────────────────
class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send(self, code, body: bytes, ctype="application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"))

    def log_message(self, *a):  # 静默访问日志
        return

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)
        q = lambda k, d="": (qs.get(k, [d])[0] if qs.get(k) else d)

        if path.rstrip("/") in ("/api/trace", "/api/trace/"):
            # 返回 HTML 看板（仅允许固定文件，杜绝路径穿越）
            if HTML_FILE.exists():
                self._send(200, HTML_FILE.read_bytes(), "text/html; charset=utf-8")
            else:
                self._json({"error": "trace_drilldown.html 缺失"}, 404)
            return

        if path == "/api/trace/data":
            try:
                limit = int(q("limit", "50"))
                days = int(q("days", "30"))
                name = q("name", "")
                trace = q("trace", "")
                if trace:
                    out = {"ok": True, "detail": trace_detail(trace),
                           "summary": summary(days)}
                else:
                    out = {"ok": True,
                           "recent": recent_traces(limit=limit, name_filter=name, days=days),
                           "summary": summary(days),
                           "a0": a0_panel(),
                           "jev": jev_panel()}
                self._json(out)
            except FileNotFoundError as e:
                self._json({"ok": False, "error": "traces.sqlite 不存在: %s" % e}, 404)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)[:300]}, 500)
            return

        if path == "/api/trace/lineage":
            try:
                out = lineage_query(view=q("view", "runs"), table=q("table", ""),
                                    limit=int(q("limit", "50")))
                self._json(out)
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)[:300]}, 500)
            return

        if path == "/api/trace/kg":
            try:
                self._json(kg_health())
            except Exception as e:  # noqa: BLE001
                self._json({"ok": False, "error": str(e)[:300]}, 500)
            return

        self._json({"error": "unknown path: %s" % path}, 404)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="A 方案 trace+血缘 下钻看板（只读）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--host", default="127.0.0.1")
    args = ap.parse_args(argv)
    srv = ThreadingHTTPServer((args.host, args.port), _H)
    print("[trace-drilldown] 只读服务已启动 http://%s:%d/api/trace/  (Ctrl+C 退出)"
          % (args.host, args.port))
    print("[trace-drilldown] traces DB: %s" % TRACE_DB)
    print("[trace-drilldown] 同源血缘复用: /api/trace/lineage")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[trace-drilldown] 已停止")
        srv.shutdown()


if __name__ == "__main__":
    main()
