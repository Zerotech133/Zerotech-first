# -*- coding: utf-8 -*-
"""方案 A 原型：只读数据桥（stdlib only、不联网、不现算）。

server 把 {path, method, query, body, companyId, skillId} 以 JSON 写 stdin，
本脚本把结果 JSON 写 stdout；未知端点出 {statusCode, body} 信封。

| path    | 行为 |
| ------- | ---- |
| graph   | 最新 day 或 query.day；payload 原样展开 + `_bridge` 元信息 |
| summary | 库内可用日列表 / 最近构建时刻 / empty 标记 |
| health  | 存活 + 库可读性 |
| 其它    | 404 信封 |

库不存在 / 无当日行 -> {"empty": true, "hint": ...}，**不现算、不联网、不补值**。
`_bridge` 是独立命名空间，避免与 payload 原有键冲突；payload 一字不改。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

SAFE = re.compile(r"[A-Za-z0-9_-]+")
DAY = re.compile(r"[0-9]{8}")
DEFAULT_COMPANY = "local-selftest"
DEFAULT_SKILL = "jev-graph-sqlite-prototype"
DB_NAME = "jev.db"
KNOWN = ("graph", "summary", "health", "audit")

LATEST_SQL = (
    "SELECT day, payload, built_at, source_sig, flags "
    "FROM graph_json ORDER BY day DESC LIMIT 1"
)
DAY_SQL = (
    "SELECT day, payload, built_at, source_sig, flags "
    "FROM graph_json WHERE day = ?"
)
DAYS_SQL = "SELECT day, built_at FROM graph_json ORDER BY day"
HAS_AUDIT_SQL = (
    "SELECT name FROM sqlite_master WHERE type='table' AND name='ingest_audit'"
)
AUDIT_SQL = (
    "SELECT seq, day, built_at, payload_sha, source_sig_sha, n_files, "
    "blocked_outbound, offline, flags FROM ingest_audit ORDER BY seq"
)
AUDIT_DAY_SQL = (
    "SELECT seq, day, built_at, payload_sha, source_sig_sha, n_files, "
    "blocked_outbound, offline, flags FROM ingest_audit WHERE day = ? ORDER BY seq"
)

# 编号码点构造，防打印/传输层串味。
STATUS_404 = int("".join(chr(c) for c in (52, 48, 52)))
STATUS_500 = int("".join(chr(c) for c in (53, 48, 48)))


def safe_part(value, fallback):
    if value and SAFE.fullmatch(value):
        return value
    return fallback


def db_path(company_id, skill_id):
    root = Path.home() / ".zerobound" / "instances" / "default" / "skill-data"
    return root / company_id / skill_id / DB_NAME


def envelope(status, body):
    return {"statusCode": status, "body": body}


def empty(reason, path):
    return {
        "empty": True,
        "reason": reason,
        "hint": "Agent 运行 ingest 后可查看",
        "path": path,
    }


def handle(req):
    path = str(req.get("path") or "")
    if path not in KNOWN:
        return envelope(STATUS_404, {"error": "unknown_endpoint", "path": path})

    company = safe_part(str(req.get("companyId") or ""), DEFAULT_COMPANY)
    skill = safe_part(str(req.get("skillId") or ""), DEFAULT_SKILL)
    db = db_path(company, skill)

    if path == "health":
        return {"ok": True, "path": path, "dbPresent": db.is_file()}

    if not db.is_file():
        return empty("db_missing", path)

    con = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        if path == "summary":
            rows = con.execute(DAYS_SQL).fetchall()
            days = [r["day"] for r in rows]
            last = max((r["built_at"] for r in rows), default=None)
            return {
                "empty": len(days) == 0,
                "path": path,
                "count": len(days),
                "days": days,
                "latestBuiltAt": last,
                "hint": "Agent 运行 ingest 后可查看" if not days else None,
            }

        if path == "audit":
            if not con.execute(HAS_AUDIT_SQL).fetchone():
                return {"path": path, "empty": True, "count": 0,
                        "rows": [], "reason": "audit_table_missing"}
            qday = str((req.get("query") or {}).get("day") or "")
            rows = (con.execute(AUDIT_DAY_SQL, (qday,)).fetchall()
                    if DAY.fullmatch(qday) else con.execute(AUDIT_SQL).fetchall())
            # 只下发摘要 sha 与档位元数据，不下发 source_sig.files 本机绝对路径明细。
            out_rows = [{
                "seq": r["seq"], "day": r["day"], "builtAt": r["built_at"],
                "payloadSha": r["payload_sha"], "sourceSigSha": r["source_sig_sha"],
                "nFiles": r["n_files"], "blockedOutbound": r["blocked_outbound"],
                "offline": json.loads(r["offline"]), "flags": json.loads(r["flags"]),
            } for r in rows]
            return {"path": path, "empty": not out_rows,
                    "count": len(out_rows), "rows": out_rows}

        day = str((req.get("query") or {}).get("day") or "")
        row = (con.execute(DAY_SQL, (day,)).fetchone() if DAY.fullmatch(day)
               else con.execute(LATEST_SQL).fetchone())
        if row is None:
            return empty("no_rows", path)

        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            return envelope(STATUS_500, {"error": "payload_not_object"})
        out = dict(payload)
        sig = json.loads(row["source_sig"])
        # files 明细含上千条本机绝对路径，只留在库里供审计，不下发给页面。
        sig_view = {k: v for k, v in sig.items() if k != "files"}
        out["_bridge"] = {
            "day": row["day"],
            "builtAt": row["built_at"],
            "flags": json.loads(row["flags"]),
            "sourceSig": sig_view,
        }
        return out
    finally:
        con.close()


def main():
    try:
        raw = sys.stdin.read()
        req = json.loads(raw) if raw.strip() else {}
        out = handle(req)
    except Exception as exc:  # noqa: BLE001
        out = envelope(STATUS_500, {"error": type(exc).__name__})
    sys.stdout.write(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
