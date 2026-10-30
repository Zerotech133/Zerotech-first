# -*- coding: utf-8 -*-
"""JEV 低置信复核工作台 API（2026-10-30 K-JEV-20261030-QUEUEUI）。

只读写 news.db 的 jev_review_queue，不触发 JEV 重判/回填。

GET  /api/jev/review/queue?status=pending&bias=bull&sort=conf_asc&limit=100&offset=0
GET  /api/jev/review/summary
POST /api/jev/review/resolve
     {"id":"...", "decision":"correct|wrong|uncertain", "reviewer":"老田", "note":""}
"""
from __future__ import annotations

import json
import os
import sqlite3
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.environ.get(
    "JEV_NEWS_DB",
    os.path.join(STATION, "news_collector", "news.db"),
)
STATUSES = ("pending", "resolved", "all")
BIASES = ("bull", "bear", "neutral")
SORTS = {
    "conf_asc": "conf ASC, queued_at ASC",
    "conf_desc": "conf DESC, queued_at ASC",
    "queued_desc": "queued_at DESC, conf ASC",
}
DECISIONS = ("correct", "wrong", "uncertain")
MAX_LIMIT = 500


def _db(write: bool = False) -> sqlite3.Connection:
    if not os.path.exists(NEWS_DB):
        raise RuntimeError("news.db 不存在：%s" % NEWS_DB)
    return sqlite3.connect(NEWS_DB, timeout=20)


def _int(raw, default: int, lo: int, hi: int) -> int:
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))


def query(status="pending", bias="", sort="conf_asc",
          limit=100, offset=0) -> dict:
    status = status if status in STATUSES else "pending"
    bias = bias if bias in BIASES else ""
    sort = sort if sort in SORTS else "conf_asc"
    where, args = [], []
    if status != "all":
        where.append("status=?")
        args.append(status)
    if bias:
        where.append("bias=?")
        args.append(bias)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    con = _db()
    try:
        total = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue" + where_sql, args
        ).fetchone()[0]
        rows = con.execute(
            "SELECT id, queued_at, conf, bias, title, status, reviewer, "
            "reviewed_at, decision, note FROM jev_review_queue"
            + where_sql + " ORDER BY " + SORTS[sort] + " LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        items = [dict(zip((
            "id", "queued_at", "conf", "bias", "title", "status",
            "reviewer", "reviewed_at", "decision", "note"), r)) for r in rows]
        summary = _summary(con)
    finally:
        con.close()
    return {"ok": True, "total": total, "returned": len(items),
            "limit": limit, "offset": offset,
            "filters": {"status": status, "bias": bias, "sort": sort},
            "summary": summary, "items": items}


def _summary(con: sqlite3.Connection) -> dict:
    total = con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM jev_review_queue WHERE status='pending'"
    ).fetchone()[0]
    by_bias = con.execute(
        "SELECT bias, COUNT(*) FROM jev_review_queue WHERE status='pending' "
        "GROUP BY bias ORDER BY 2 DESC"
    ).fetchall()
    resolved = total - pending
    return {"total": total, "pending": pending, "resolved": resolved,
            "by_bias": [{"bias": b or "", "n": n} for b, n in by_bias]}


def summary() -> dict:
    con = _db()
    try:
        return {"ok": True, **_summary(con)}
    finally:
        con.close()


def resolve(nid: str, decision: str, reviewer: str, note: str = "") -> dict:
    nid = str(nid or "").strip()
    decision = str(decision or "").strip().lower()
    reviewer = str(reviewer or "").strip()[:80]
    if not nid:
        raise ValueError("缺少 id")
    if decision not in DECISIONS:
        raise ValueError("decision 必须是 %s" % "/".join(DECISIONS))
    if not reviewer:
        raise ValueError("reviewer 必填")
    con = _db(write=True)
    try:
        cur = con.execute(
            "UPDATE jev_review_queue SET status='resolved', decision=?, "
            "reviewer=?, reviewed_at=datetime('now','localtime'), note=? "
            "WHERE id=? AND status='pending'",
            (decision, reviewer, str(note or "")[:500], nid),
        )
        con.commit()
        if not cur.rowcount:
            return {"ok": False, "error": "not_pending", "id": nid}
        row = con.execute(
            "SELECT id, status, reviewer, reviewed_at, decision, note "
            "FROM jev_review_queue WHERE id=?", (nid,)
        ).fetchone()
    finally:
        con.close()
    return {"ok": True, "item": dict(zip((
        "id", "status", "reviewer", "reviewed_at", "decision", "note"), row))}


def _json(handler, obj: dict, code: int = 200) -> None:
    handler._json(obj, code)


def _read_body(handler) -> dict:
    try:
        n = int(handler.headers.get("Content-Length") or 0)
    except (TypeError, ValueError):
        n = 0
    raw = handler.rfile.read(n) if n > 0 else b"{}"
    data = json.loads(raw.decode("utf-8"))
    return data if isinstance(data, dict) else {}


def handle_get(handler, path: str, qs_raw: str) -> None:
    qs = parse_qs(qs_raw or "")
    try:
        if path == "/api/jev/review/summary":
            _json(handler, summary())
            return
        if path != "/api/jev/review/queue":
            _json(handler, {"ok": False, "error": "not_found"}, 404)
            return
        _json(handler, query(
            status=(qs.get("status") or ["pending"])[-1],
            bias=(qs.get("bias") or [""])[-1],
            sort=(qs.get("sort") or ["conf_asc"])[-1],
            limit=_int((qs.get("limit") or ["100"])[-1], 100, 1, MAX_LIMIT),
            offset=_int((qs.get("offset") or ["0"])[-1], 0, 0, 10 ** 7),
        ))
    except RuntimeError as exc:
        _json(handler, {"ok": False, "error": "unavailable", "message": str(exc)}, 503)
    except Exception as exc:  # noqa: BLE001
        _json(handler, {"ok": False, "error": "internal",
                        "message": "%s: %s" % (type(exc).__name__, exc)}, 500)


def handle_post(handler, path: str) -> None:
    if path != "/api/jev/review/resolve":
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    try:
        body = _read_body(handler)
        result = resolve(
            body.get("id"), body.get("decision"),
            body.get("reviewer"), body.get("note") or "",
        )
    except ValueError as exc:
        _json(handler, {"ok": False, "error": "invalid_params", "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        _json(handler, {"ok": False, "error": "internal",
                        "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    _json(handler, result, 200 if result.get("ok") else 409)
