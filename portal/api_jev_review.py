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

2026-10-31 WO-INTEL-AI1ST-20261031（N1 AI 初筛）：
- 幂等迁移 `review_type TEXT NOT NULL DEFAULT ''`（旧 schema 兼容；现有行默认 ''，
  统计时 '' 计入 human=历史人工档，不破坏既有读数）。
- 分类：人工 resolve 写 `review_type='human'`；AI 初筛写 `review_type='ai_first_pass'`
  （reviewer='N1-AI'，由 tools/intel_ai_first_pass.py 写入，不冒充人工）。
- summary 增 `resolved_human` / `resolved_ai_first_pass` / `review_type_counts`
  （resolved 仍为总 resolved；人工 50 只数 human，AI 初筛不计人工 50）。

GET  /api/jev/review/queue?status=pending&bias=bull&sort=conf_asc&limit=100&offset=0
GET  /api/jev/review/summary
POST /api/jev/review/resolve
     {"id":"...", "decision":"correct|wrong|uncertain", "reviewer":"老田", "note":""}
POST /api/jev/review/reopen
     {"id":"...", "reviewer":"老田", "note":""}

2026-10-31 P0-B（K-ORG-20261031-APIHARDEN，审计 v2 M3）：
- resolve/reopen 写接口需门户登录会话（_authorized 同 api_zhuge_review 口径）；
- reviewer 一律取会话身份（sub），body 自报他人 → 403 reviewer_mismatch；
- 未登录 403 unauthorized；会话身份缺失 403。GET 只读接口不变。
"""
from __future__ import annotations

import hashlib
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

# N1 AI 初筛（WO-INTEL-AI1ST-20261031）：review_type 分类，人工/ai_first_pass 分账。
REVIEW_TYPE_COLUMN = "review_type"
REVIEW_TYPE_HUMAN = "human"
REVIEW_TYPE_AI_FIRST_PASS = "ai_first_pass"


def table_columns(con: sqlite3.Connection, table: str = "jev_review_queue"):
    """表列名（表不存在返回 []，便于旧库/坏库 fail-visible 而非直接炸）。"""
    try:
        return [str(r[1]) for r in con.execute("PRAGMA table_info(%s)" % table)]
    except sqlite3.Error:
        return []


def has_review_type(con: sqlite3.Connection) -> bool:
    return REVIEW_TYPE_COLUMN in table_columns(con)


def ensure_review_type_column(con: sqlite3.Connection) -> bool:
    """幂等迁移：无 review_type 列则 ALTER ADD（默认 ''）；已存在返回 False。

    只加列不改数据：旧行 review_type='' → 统计口径里计为 human（历史人工/未分类），
    不伪造 ai_first_pass。返回 True 表示本次确实新增了列。
    """
    if has_review_type(con):
        return False
    con.execute(
        "ALTER TABLE jev_review_queue ADD COLUMN %s TEXT NOT NULL DEFAULT ''"
        % REVIEW_TYPE_COLUMN)
    con.commit()
    return True


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
        cols = ["id", "queued_at", "conf", "bias", "title", "status",
                "reviewer", "reviewed_at", "decision", "note"]
        if has_review_type(con):
            cols.append(REVIEW_TYPE_COLUMN)
        rows = con.execute(
            "SELECT " + ", ".join(cols) + " FROM jev_review_queue"
            + where_sql + " ORDER BY " + SORTS[sort] + " LIMIT ? OFFSET ?",
            args + [limit, offset],
        ).fetchall()
        items = [dict(zip(cols, r)) for r in rows]
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
    # 2026-10-31 N1 AI 初筛：resolved 按 review_type 分账（'' 计 human=历史口径）。
    rt_present = has_review_type(con)
    if rt_present:
        resolved_human = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status='resolved' "
            "AND COALESCE(%s,'')!=?" % REVIEW_TYPE_COLUMN,
            (REVIEW_TYPE_AI_FIRST_PASS,)).fetchone()[0]
        resolved_ai = con.execute(
            "SELECT COUNT(*) FROM jev_review_queue WHERE status='resolved' "
            "AND %s=?" % REVIEW_TYPE_COLUMN,
            (REVIEW_TYPE_AI_FIRST_PASS,)).fetchone()[0]
    else:
        resolved_human, resolved_ai = resolved, 0
    return {"total": total, "pending": pending, "resolved": resolved,
            "resolved_human": resolved_human,
            "resolved_ai_first_pass": resolved_ai,
            "review_type_column": rt_present,
            "review_type_counts": {
                REVIEW_TYPE_HUMAN: resolved_human,
                REVIEW_TYPE_AI_FIRST_PASS: resolved_ai,
            },
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
    """pending → resolved 的唯一人工写入口（review_type 固定写 'human'）。

    只允许 status='pending'：unreviewed_legacy / resolved 均返回 not_pending
    （legacy 是归档档位，不是 pending，也不是"已复核"；重开走 reopen()）。
    AI 初筛（review_type='ai_first_pass'）由 tools/intel_ai_first_pass.py 写入，
    不走本函数、不冒充人工；本函数只写 human。
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
        ensure_review_type_column(con)  # 幂等迁移（旧库首次 resolve 自动补列）
        cur = con.execute(
            "UPDATE jev_review_queue SET status='resolved', decision=?, "
            "reviewer=?, reviewed_at=datetime('now','localtime'), note=?, "
            "%s=? WHERE id=? AND status='pending'" % REVIEW_TYPE_COLUMN,
            (decision, reviewer, str(note or "")[:500],
             REVIEW_TYPE_HUMAN, nid),
        )
        con.commit()
        if not cur.rowcount:
            return {"ok": False, "error": "not_pending", "id": nid}
        row = con.execute(
            "SELECT id, status, reviewer, reviewed_at, decision, note, %s "
            "FROM jev_review_queue WHERE id=?" % REVIEW_TYPE_COLUMN, (nid,)
        ).fetchone()
    finally:
        con.close()
    return {"ok": True, "item": dict(zip((
        "id", "status", "reviewer", "reviewed_at", "decision", "note",
        "review_type"), row))}


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


# ---------------------------------------------------------------- M3 鉴权
def _authorized(handler) -> bool:
    """M3（K-ORG-20261031-APIHARDEN）：写接口必须门户登录会话（zb_session）。

    与 api_zhuge_review._authorized 同款：不信任 body；无会话一律拒。
    """
    try:
        import auth_gateway
        sid = auth_gateway._cookie_value(handler)
        return bool(sid and auth_gateway.STORE.get(sid))
    except Exception:  # noqa: BLE001 - auth 依赖不可用 → 失败关闭
        return False


def _session_actor(handler) -> str:
    """会话身份：优先 identity sub，其次 session id 短哈希（不落明文 cookie）。"""
    try:
        import auth_gateway
        sid = auth_gateway._cookie_value(handler)
        rec = auth_gateway.STORE.get(sid) if sid else None
        if isinstance(rec, dict):
            for key in ("sub", "user", "username", "name"):
                if rec.get(key):
                    return str(rec[key])[:120]
        if sid:
            digest = hashlib.sha256(
                ("zb_session:" + sid).encode("utf-8", "replace")).hexdigest()
            return "session:" + digest[:8]
    except Exception:  # noqa: BLE001
        pass
    return ""


def handle_post(handler, path: str) -> None:
    # M3（K-ORG-20261031-APIHARDEN）：写接口进程内鉴权；reviewer 取会话身份，
    # 禁止 body 自报他人（审计 v2 M3）。
    if not _authorized(handler):
        _json(handler, {"ok": False, "error": "unauthorized"}, 403)
        return
    if path not in ("/api/jev/review/resolve", "/api/jev/review/reopen"):
        _json(handler, {"ok": False, "error": "not_found"}, 404)
        return
    actor = _session_actor(handler)
    try:
        body = _read_body(handler)
        supplied = str(body.get("reviewer") or "").strip()
        if not actor:
            _json(handler, {"ok": False, "error": "unauthorized",
                            "message": "会话身份缺失"}, 403)
            return
        if supplied and supplied != actor:
            _json(handler, {"ok": False, "error": "reviewer_mismatch",
                            "message": "reviewer 与会话身份不一致（禁止自报）"}, 403)
            return
        if path == "/api/jev/review/reopen":
            result = reopen(body.get("id"), actor, body.get("note") or "")
        else:
            result = resolve(
                body.get("id"), body.get("decision"), actor,
                body.get("note") or "",
            )
    except ValueError as exc:
        _json(handler, {"ok": False, "error": "invalid_params", "message": str(exc)}, 400)
        return
    except Exception as exc:  # noqa: BLE001
        _json(handler, {"ok": False, "error": "internal",
                        "message": "%s: %s" % (type(exc).__name__, exc)}, 500)
        return
    _json(handler, result, 200 if result.get("ok") else 409)
