# -*- coding: utf-8 -*-
"""JEV 低置信复核工作台 API（2026-10-30 K-JEV-20261030-QUEUEUI）。

只读写 news.db 的 jev_review_queue，不触发 JEV 重判/回填。

2026-10-31 WO-INTEL-REVIEW-20261031（执行拍板 2/4）：
- 复核预算：human_budget=50（人工优先档）+ machine_budget=200（机器预审档）；
  超出预算的 pending 由 tools/intel_review_archive.py 分层为 unreviewed_legacy。
- summary/queue 返回 budget 常量与 tier_counts（human/machine/over_budget/
  pending/unreviewed_legacy/resolved/total）。
- status 新增 unreviewed_legacy：**不计入 resolved、不可被 resolve 当 pending**。
- legacy 可通过 POST /api/jev/review/reopen 重开为 pending（原文/裁决字段保留）。

GET  /api/jev/review/queue?status=pending&bias=bull&sort=conf_asc&limit=100&offset=0
GET  /api/jev/review/summary
POST /api/jev/review/resolve
     {"id":"...", "decision":"correct|wrong|uncertain", "reviewer":"老田", "note":""}
POST /api/jev/review/reopen
     {"id":"...", "reviewer":"老田", "note":""}
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from urllib.parse import parse_qs

STATION = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEWS_DB = os.environ.get(
    "JEV_NEWS_DB",
    os.path.join(STATION, "news_collector", "news.db"),
)
# 执行拍板 2/4（2026-10-31）：人工 50 + 机器预审 200；其余 pending 归 legacy。
HUMAN_BUDGET = 50
MACHINE_BUDGET = 200
PENDING_BUDGET = HUMAN_BUDGET + MACHINE_BUDGET
LEGACY_STATUS = "unreviewed_legacy"
STATUSES = ("pending", "resolved", LEGACY_STATUS, "all")
BIASES = ("bull", "bear", "neutral")
SORTS = {
    "conf_asc": "conf ASC, queued_at ASC",
    "conf_desc": "conf DESC, queued_at ASC",
    "queued_desc": "queued_at DESC, conf ASC",
}
DECISIONS = ("correct", "wrong", "uncertain")
MAX_LIMIT = 500


def budgets() -> dict:
    """复核预算常量（summary/queue 原样透出，前端/面板别自己写死数字）。"""
    return {"human_budget": HUMAN_BUDGET, "machine_budget": MACHINE_BUDGET,
            "pending_budget": PENDING_BUDGET}


def tier_counts(total: int, pending: int, legacy: int, resolved: int) -> dict:
    """预算档位计数（结构性口径，不依赖具体排序实现）。

    human      = 前 HUMAN_BUDGET 个 pending（人工优先档）
    machine    = 其后 MACHINE_BUDGET 个 pending（机器预审档）
    over_budget= 超出 human+machine 预算、待归档脚本转 legacy 的 pending
    unreviewed_legacy = 已分层归档、未经人工复核的历史堆积（**不算 resolved**）
    """
    human = min(pending, HUMAN_BUDGET)
    machine = min(max(pending - HUMAN_BUDGET, 0), MACHINE_BUDGET)
    over = max(pending - PENDING_BUDGET, 0)
    return {"human": human, "machine": machine, "over_budget": over,
            "pending": pending, "unreviewed_legacy": legacy,
            "resolved": resolved, "total": total}


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
            **budgets(), "tier_counts": summary["tier_counts"],
            "summary": summary, "items": items}


def _summary(con: sqlite3.Connection) -> dict:
    total = con.execute("SELECT COUNT(*) FROM jev_review_queue").fetchone()[0]
    pending = con.execute(
        "SELECT COUNT(*) FROM jev_review_queue WHERE status='pending'"
    ).fetchone()[0]
    # 2026-10-31：resolved 必须按真实 status 计数——旧口径 total-pending 会把
    # unreviewed_legacy 误算成"已复核"（legacy 只是归档，不等于人工复核过）。
    resolved = con.execute(
        "SELECT COUNT(*) FROM jev_review_queue WHERE status='resolved'"
    ).fetchone()[0]
    legacy = con.execute(
        "SELECT COUNT(*) FROM jev_review_queue WHERE status=?", (LEGACY_STATUS,)
    ).fetchone()[0]
    by_bias = con.execute(
        "SELECT bias, COUNT(*) FROM jev_review_queue WHERE status='pending' "
        "GROUP BY bias ORDER BY 2 DESC"
    ).fetchall()
    return {"total": total, "pending": pending, "resolved": resolved,
            LEGACY_STATUS: legacy,
            **budgets(),
            "tier_counts": tier_counts(total, pending, legacy, resolved),
            "by_bias": [{"bias": b or "", "n": n} for b, n in by_bias]}


def summary() -> dict:
    con = _db()
    try:
        payload = _summary(con)
        return {"ok": True, **payload}
    finally:
        con.close()


def resolve(nid: str, decision: str, reviewer: str, note: str = "") -> dict:
    """pending → resolved 的唯一写入口。

    只允许 status='pending'：unreviewed_legacy / resolved 均返回 not_pending
    （legacy 是归档档位，不是 pending，也不是"已复核"；重开走 reopen()）。
    """
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


def reopen(nid: str, reviewer: str, note: str = "") -> dict:
    """把 unreviewed_legacy 重开为 pending（保留原 id/标题/original_jev，不删数据）。

    - 仅允许 legacy → pending；pending/resolved 一律拒绝（不覆盖既有复核结论）。
    - note 记录重开人/时间（原文与历史裁决字段不丢）；重开后可再次 resolve。
    """
    nid = str(nid or "").strip()
    reviewer = str(reviewer or "").strip()[:80]
    if not nid:
        raise ValueError("缺少 id")
    if not reviewer:
        raise ValueError("reviewer 必填")
    con = _db(write=True)
    try:
        row = con.execute(
            "SELECT id, status, note FROM jev_review_queue WHERE id=?", (nid,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": "not_found", "id": nid}
        if row[1] != LEGACY_STATUS:
            return {"ok": False, "error": "not_legacy", "id": nid, "status": row[1]}
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        merged = ("[reopened by %s @ %s] %s"
                  % (reviewer, stamp, (str(note or "") or str(row[2] or "")))).strip()[:500]
        cur = con.execute(
            "UPDATE jev_review_queue SET status='pending', reviewer=NULL, "
            "reviewed_at=NULL, decision=NULL, note=? "
            "WHERE id=? AND status=?",
            (merged, nid, LEGACY_STATUS),
        )
        con.commit()
        if not cur.rowcount:
            return {"ok": False, "error": "not_legacy", "id": nid}
        out = con.execute(
            "SELECT id, status, reviewer, reviewed_at, decision, note "
            "FROM jev_review_queue WHERE id=?", (nid,)
        ).fetchone()
    finally:
        con.close()
    return {"ok": True, "item": dict(zip((
        "id", "status", "reviewer", "reviewed_at", "decision", "note"), out))}


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
    if path not in ("/api/jev/review/resolve", "/api/jev/review/reopen"):
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    try:
        body = _read_body(handler)
        if path == "/api/jev/review/reopen":
            result = reopen(
                body.get("id"), body.get("reviewer"),
                body.get("note") or "",
            )
        else:
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
