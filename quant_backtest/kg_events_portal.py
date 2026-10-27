# -*- coding: utf-8 -*-
"""K266 事件图谱查询数据层（JSON，纯查询不写库）。

供 portal_api.py 的 /kg/events/* 端点调用：api(kind, arg, qp)；
也可 CLI 自测：
  python kg_events_portal.py situ
  python kg_events_portal.py graph 主板
  python kg_events_portal.py list 002050.SZ
  python kg_events_portal.py doc db86c33cd4551i25
  python kg_events_portal.py search 奥普

数据口径（与 k266_m3a_events.py 入图端同源）：
  · DOC_EVENT 边挂在 Security 对之间，属性 rel/dir/n/date/amount/ts_last/src_doc
  · src_doc 是条级 doc_id（如 d19b9f5a6fcfai07），去末尾 iNN 后缀得
    文档级 doc_id → doc_texts.jsonl 取原文 text；结构化摘要优先按条级
    精确查 structure_shadow.jsonl，miss 按文档级前缀兜底
连接：模块级懒加载单例 KgClient(retry=1)——门户场景快失败，
Neo4j 未启动时立即返回错误 JSON 而非等 75s。影子/原文文件按 mtime
失效的进程内缓存（门户为 ThreadingHTTPServer，读写均持锁）。
"""

import json
import os
import re
import sys
import threading
from datetime import datetime
import logging

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE, "data", "k266_docs")
TEXTS_FP = os.path.join(DOCS_DIR, "doc_texts.jsonl")
STRUCT_FP = os.path.join(DOCS_DIR, "structure_shadow.jsonl")
EVENT_FP = os.path.join(DOCS_DIR, "event_shadow.jsonl")

_lock = threading.Lock()
_cli = None

# 文档摄取节奏（K266-DocServices 计划任务，分钟/拍）——态势条"下次更新"用
INGEST_CADENCE_MIN = 5


def _client():
    global _cli
    with _lock:
        if _cli is None:
            from kg_client import KgClient

            _cli = KgClient(retry=1, retry_wait=1)
        return _cli


def _q(cy, **kw):
    with _client()._session() as s:
        return [dict(r) for r in s.run(cy, **kw)]


# ── 影子/原文文件缓存（mtime 失效）────────────────────────
_txt_cache = {"mtime": -1.0, "map": {}}
_struct_cache = {"mtime": -1.0, "map": {}}


def _load_map(fp, cache):
    try:
        mtime = os.path.getmtime(fp)
    except OSError:
        with _lock:
            if cache["mtime"] != -1.0:
                cache["mtime"], cache["map"] = -1.0, {}
        return cache["map"]
    with _lock:
        if cache["mtime"] == mtime:
            return cache["map"]
    m = {}
    try:
        with open(fp, encoding="utf-8") as f:
            for line in f:
                try:
                    r = json.loads(line)
                    m[r.get("doc_id")] = r
                except Exception as exc:
                    logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_events_portal.py", exc)
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_events_portal.py", exc)
    with _lock:
        cache["mtime"], cache["map"] = mtime, m
    return cache["map"]


def _doc_level(did):
    """条级 doc_id（…iNN）→ 文档级 doc_id（去 iNN 后缀）。"""
    return re.sub(r"i\d+$", "", did or "")


def _fmeta(fp):
    try:
        st = os.stat(fp)
        return {
            "mtime": datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            "count": _count_jsonl(fp),
        }
    except OSError:
        return {"mtime": None, "count": 0}


def _count_jsonl(fp):
    n = 0
    try:
        with open(fp, encoding="utf-8") as f:
            for _ in f:
                n += 1
    except OSError as exc:
        logging.getLogger(__name__).warning("silent-except %s: %s", "quant_backtest/kg_events_portal.py", exc)
    return n


# ── 过滤条件（None = 不过滤；空串/“全部”归一为 None）────
def _fx(v):
    v = (v or "").strip()
    return v or None


def _fm(v):
    """market 专用：页面默认 Tab 值“全部”不过滤。"""
    v = _fx(v)
    return None if v == "全部" else v


# ── /kg/events/situ：态势感知 ──────────────────────────────
def situ():
    out = {
        "ok": True,
        "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "neo4j_up": False,
        "doc_edges": 0,
        "latest_event_ts": None,
        "sec_involved": 0,
        "inv_markets": {},
        "rels": {},
        "dirs": {},
        "cadence_min": INGEST_CADENCE_MIN,
    }
    try:
        r = _q(
            "MATCH ()-[r:DOC_EVENT]->() RETURN count(r) AS n, max(r.ts_last) AS latest"
        )[0]
        out.update(neo4j_up=True, doc_edges=r["n"], latest_event_ts=r["latest"])
        for row in _q(
            "MATCH (a:Security)-[:DOC_EVENT]->(b:Security) "
            "UNWIND [a, b] AS s RETURN count(DISTINCT s) AS n"
        ):
            out["sec_involved"] = row["n"]
        for row in _q(
            "MATCH (a:Security)-[:DOC_EVENT]->(b:Security) "
            "UNWIND [a, b] AS s RETURN coalesce(s.market,'未分类') AS m, "
            "count(DISTINCT s) AS n ORDER BY n DESC"
        ):
            out["inv_markets"][row["m"]] = row["n"]
        for row in _q(
            "MATCH ()-[r:DOC_EVENT]->() "
            "RETURN r.rel AS k, count(*) AS n ORDER BY n DESC"
        ):
            out["rels"][row["k"]] = row["n"]
        for row in _q(
            "MATCH ()-[r:DOC_EVENT]->() "
            "RETURN r.dir AS k, count(*) AS n ORDER BY n DESC"
        ):
            out["dirs"][row["k"]] = row["n"]
    except Exception as ex:
        out["ok"] = False
        out["error"] = str(ex)[:200]
    # 文件侧（独立降级，不随 Neo4j 失败）
    out["struct"] = _fmeta(STRUCT_FP)
    out["event_shadow"] = _fmeta(EVENT_FP)
    out["texts"] = _fmeta(TEXTS_FP)
    return out


# ── /kg/events/graph：G6 图数据（含过滤）──────────────────
_GRAPH_CY = (
    "MATCH (a:Security)-[r:DOC_EVENT]->(b:Security) "
    "WHERE ($m IS NULL OR a.market=$m OR b.market=$m) "
    "  AND ($q IS NULL OR a.name CONTAINS $q OR b.name CONTAINS $q "
    "       OR a.ts_code CONTAINS $q OR b.ts_code CONTAINS $q "
    "       OR any(x IN coalesce(a.aliases,[]) WHERE x CONTAINS $q) "
    "       OR any(x IN coalesce(b.aliases,[]) WHERE x CONTAINS $q)) "
    "  AND ($dir IS NULL OR r.dir=$dir) "
    "  AND ($rel IS NULL OR r.rel=$rel) "
    "  AND ($d0 IS NULL OR r.date>=$d0) "
    "  AND ($d1 IS NULL OR r.date<=$d1) "
    "RETURN a.name AS an, a.ts_code AS ac, a.market AS am, "
    "       b.name AS bn, b.ts_code AS bc, b.market AS bm, "
    "       r.rel AS rel, r.dir AS dir, r.n AS n, r.date AS date, "
    "       r.amount AS amount, r.src_doc AS src_doc, r.ts_last AS ts_last "
    "ORDER BY r.ts_last DESC LIMIT $lim"
)


def _edges_rows(qp, lim=300):
    return _q(
        _GRAPH_CY,
        m=_fm(qp.get("market") if qp else None),
        q=_fx(qp.get("q") if qp else None),
        dir=_fx(qp.get("dir") if qp else None),
        rel=_fx(qp.get("rel") if qp else None),
        d0=_fx(qp.get("d0") if qp else None),
        d1=_fx(qp.get("d1") if qp else None),
        lim=lim,
    )


def graph(qp=None):
    try:
        rows = _edges_rows(qp or {})
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}
    nodes, seen = {}, {}
    edges = []
    for r in rows:
        for nm, code, mk in ((r["an"], r["ac"], r["am"]), (r["bn"], r["bc"], r["bm"])):
            if code and code not in seen:
                seen[code] = True
                nodes[code] = {
                    "id": code,
                    "data": {
                        "name": nm,
                        "code": code,
                        "market": mk or "未分类",
                        "deg": 0,
                    },
                }
        eid = "%s>>%s:%s|%s" % (r["ac"], r["bc"], r["rel"], r["dir"])
        edges.append(
            {
                "id": eid,
                "source": r["ac"],
                "target": r["bc"],
                "data": {
                    "rel": r["rel"],
                    "dir": r["dir"],
                    "n": r["n"] or 1,
                    "date": r["date"] or "",
                    "amount": r["amount"] or "",
                    "src_doc": r["src_doc"] or "",
                    "ts_last": str(r["ts_last"] or ""),
                },
            }
        )
        nodes[r["ac"]]["data"]["deg"] += 1
        if r["bc"] in nodes:
            nodes[r["bc"]]["data"]["deg"] += 1
    return {
        "ok": True,
        "nodes": list(nodes.values()),
        "edges": edges,
        "total": len(rows),
    }


# ── /kg/events/list：事件行（点公司下钻 / 列表区）──────────
def list_rows(qp=None):
    qp = qp or {}
    c = _fx(qp.get("c"))
    cy = (
        "MATCH (a:Security)-[r:DOC_EVENT]->(b:Security) "
        "WHERE ($c IS NULL OR a.ts_code=$c OR b.ts_code=$c) "
        "  AND ($m IS NULL OR a.market=$m OR b.market=$m) "
        "  AND ($q IS NULL OR a.name CONTAINS $q OR b.name CONTAINS $q "
        "       OR a.ts_code CONTAINS $q OR b.ts_code CONTAINS $q) "
        "  AND ($dir IS NULL OR r.dir=$dir) "
        "  AND ($rel IS NULL OR r.rel=$rel) "
        "  AND ($d0 IS NULL OR r.date>=$d0) "
        "  AND ($d1 IS NULL OR r.date<=$d1) "
        "RETURN a.name AS an, a.ts_code AS ac, a.market AS am, "
        "       b.name AS bn, b.ts_code AS bc, b.market AS bm, "
        "       r.rel AS rel, r.dir AS dir, r.n AS n, r.date AS date, "
        "       r.amount AS amount, r.src_doc AS src_doc, "
        "       r.ts_last AS ts_last "
        "ORDER BY r.ts_last DESC LIMIT $lim"
    )
    try:
        rows = _q(
            cy,
            c=c,
            m=_fm(qp.get("market")),
            q=_fx(qp.get("q")),
            dir=_fx(qp.get("dir")),
            rel=_fx(qp.get("rel")),
            d0=_fx(qp.get("d0")),
            d1=_fx(qp.get("d1")),
            lim=500,
        )
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}
    return {"ok": True, "rows": rows, "total": len(rows)}


# ── /kg/events/doc/{doc_id}：原文 + 结构化摘要 ─────────────
def doc(arg):
    """arg: 条级或文档级 doc_id。返回原文 text + structure 摘要字段。"""
    did = (arg or "").strip()
    if not did:
        return {"ok": False, "error": "missing doc_id"}
    lvl = _doc_level(did)
    texts = _load_map(TEXTS_FP, _txt_cache)
    t = texts.get(did) or texts.get(lvl)
    struct = _load_map(STRUCT_FP, _struct_cache)
    s = struct.get(did)
    if s is None:  # 条级 miss → 文档级前缀兜底
        for k, v in struct.items():
            if k and lvl and k.startswith(lvl):
                s = v
                break
    out = {
        "ok": bool(t or s),
        "doc_id": did,
        "doc_level": lvl,
        "found_text": bool(t),
        "found_struct": bool(s),
    }
    if t:
        out.update(
            text=(t.get("text") or "")[:20000],
            text_chars=t.get("text_chars") or 0,
            name=t.get("name", ""),
            kind=t.get("kind", ""),
            src=t.get("src", ""),
            ts=t.get("ts", ""),
        )
    if s:

        def _kn(x):
            if isinstance(x, dict):  # {"value": "8票同意", "context": "…"}
                v = str(x.get("value") or x.get("v") or "")
                c = str(x.get("context") or x.get("k") or "")
                return ("%s：%s" % (c, v)) if c and v else (v or c)
            return str(x)

        out["struct"] = {
            "doc_type": s.get("doc_type", ""),
            "title_guess": s.get("title_guess", ""),
            "direction": s.get("direction", ""),
            "direction_why": s.get("direction_why", ""),
            "key_numbers": [
                k for k in (_kn(x) for x in s.get("key_numbers", [])[:12]) if k
            ],
            "tags": s.get("tags", [])[:12],
            "companies": s.get("companies", [])[:8],
            "events": s.get("events", [])[:10],
        }
    if not out["ok"]:
        out["error"] = "doc_id 未命中 doc_texts/structure_shadow"
    return out


# ── /kg/events/search?q=：公司联想（入图标的优先）─────────
def search(qp=None):
    q = _fx((qp or {}).get("q"))
    if not q:
        return {"ok": True, "hits": []}
    cy = (
        "MATCH (s:Security) WHERE s.name CONTAINS $q OR s.ts_code "
        "CONTAINS $q OR s.symbol CONTAINS $q OR any(x IN coalesce"
        "(s.aliases,[]) WHERE x CONTAINS $q) "
        "OPTIONAL MATCH (s)-[r:DOC_EVENT]-() "
        "RETURN s.name AS name, s.ts_code AS code, s.market AS market, "
        "count(r) AS n_events ORDER BY n_events DESC, name LIMIT 15"
    )
    try:
        rows = _q(cy, q=q)
    except Exception as ex:
        return {"ok": False, "error": str(ex)[:200]}
    return {
        "ok": True,
        "hits": [
            {
                "name": r["name"],
                "code": r["code"],
                "market": r["market"] or "未分类",
                "n_events": r["n_events"],
            }
            for r in rows
        ],
    }


# ── 统一分发 ────────────────────────────────────────────────
def api(kind, arg=None, qp=None):
    if kind == "situ":
        return situ()
    if kind == "graph":
        return graph(qp)
    if kind == "list":
        return list_rows(qp)
    if kind == "doc":
        return doc(arg)
    if kind == "search":
        return search(qp)
    return {"ok": False, "error": "unknown kind: %s" % kind}


if __name__ == "__main__":
    kind = sys.argv[1] if len(sys.argv) > 1 else "situ"
    arg, qpx = None, {}
    for kv in sys.argv[2:]:
        if "=" in kv:
            k, v = kv.split("=", 1)
            qpx[k] = v
        elif arg is None:
            arg = kv
    r = api(kind, arg, qpx)
    print(json.dumps(r, ensure_ascii=False, indent=1)[:4000])
