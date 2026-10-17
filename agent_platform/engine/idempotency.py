# -*- coding: utf-8 -*-
"""统一 IdempotencyRecord 账本（M0 P3）。

设计规格来源: docs/audit/2026-10-17_统一幂等语义盘点.md

核心原则:
- 独立账本（SQLite, WAL），不修改 Paper 旧 orders.ref / ux_orders_ref 语义；
- 记录级唯一维度: (workspace_id, scope, operation, idempotency_key)；
- attempt 追加式（append-only），失败不覆盖历史 attempt；
- 同键同 payload   -> 返回首次结果（outcome=completed），不重复副作用；
- 同键不同 payload -> conflict（粘性，需显式人工/调用方介入）；
- failed/retryable -> 允许新 attempt；
- 另一 attempt 仍在执行 -> 稳定返回 in_progress（不抛唯一约束异常）；
- payload_hash 使用 canonical JSON（禁止 NaN/Infinity），sha256。

时间字段一律 UTC 带时区 ISO8601，与 ai_operator v1 契约一致。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0"

DEADLETTER_PREFIX = "dead-letter: stale lease"

# attempt 状态
ST_CLAIMED = "claimed"      # 已认领，执行中
ST_COMPLETED = "completed"  # 有最终结果，不重试
ST_FAILED = "failed"        # 失败，允许新 attempt

# claim outcome
OUT_CLAIMED = "claimed"          # 新建 attempt，调用方应执行
OUT_COMPLETED = "completed"      # 命中既有结果，勿重复执行
OUT_CONFLICT = "conflict"        # 同键不同 payload，拒绝
OUT_IN_PROGRESS = "in_progress"  # 既有 attempt 执行中，稳定跳过

# record 状态（由 attempt 派生 + conflict 粘性标志）
REC_ACTIVE = "active"
REC_CONFLICT = "conflict"

# 默认允许重试的 attempt 终态
DEFAULT_RETRYABLE = (ST_FAILED,)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _age_seconds(from_iso: str, to_iso: str) -> float:
    """两个 ISO8601 时间差秒数；解析失败返回 inf（fail-visible，视为超龄）。"""
    try:
        a = datetime.fromisoformat(from_iso)
        b = datetime.fromisoformat(to_iso)
        return (b - a).total_seconds()
    except (ValueError, TypeError):
        return float("inf")


def canonical_payload_hash(payload) -> str:
    """canonical JSON sha256；NaN/Infinity 直接拒绝（与 v1 canonical_json 对齐）。"""
    try:
        blob = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (ValueError, TypeError) as exc:
        raise ValueError("payload not canonical-JSON serializable: %s" % exc) from exc
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _record_id(workspace_id: str, scope: str, operation: str, idempotency_key: str) -> str:
    raw = "\x1f".join([workspace_id, scope, operation, idempotency_key])
    return "IR-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


class IdempotencyStore:
    """统一幂等记录账本。独立于 Paper orders 表，仅新增、不修改旧语义。"""

    def __init__(self, db_path, workspace_id: str = "default"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.workspace_id = workspace_id
        self.con = sqlite3.connect(str(self.db_path), timeout=30)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=10000")
        self._init_schema()

    def _init_schema(self) -> None:
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS idempotency_records (
                record_id      TEXT PRIMARY KEY,
                workspace_id   TEXT NOT NULL,
                scope          TEXT NOT NULL,
                operation      TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                account_id     TEXT NOT NULL DEFAULT '',
                payload_hash   TEXT NOT NULL,
                status         TEXT NOT NULL,
                source_system  TEXT NOT NULL DEFAULT '',
                source_ref     TEXT NOT NULL DEFAULT '',
                trace_id       TEXT NOT NULL DEFAULT '',
                conflict_reason TEXT NOT NULL DEFAULT '',
                created_at     TEXT NOT NULL,
                updated_at     TEXT NOT NULL,
                UNIQUE(workspace_id, scope, operation, idempotency_key)
            );
            CREATE TABLE IF NOT EXISTS idempotency_attempts (
                attempt_id   TEXT PRIMARY KEY,
                record_id    TEXT NOT NULL,
                attempt      INTEGER NOT NULL,
                status       TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                result_ref   TEXT NOT NULL DEFAULT '',
                error        TEXT NOT NULL DEFAULT '',
                created_at   TEXT NOT NULL,
                updated_at   TEXT NOT NULL,
                UNIQUE(record_id, attempt)
            );
            CREATE TABLE IF NOT EXISTS idempotency_conflicts (
                conflict_id     TEXT PRIMARY KEY,
                record_id       TEXT NOT NULL,
                payload_hash    TEXT NOT NULL,
                existing_hash   TEXT NOT NULL,
                reason          TEXT NOT NULL,
                created_at      TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ir_records_key
                ON idempotency_records(workspace_id, scope, operation, idempotency_key);
            CREATE INDEX IF NOT EXISTS idx_ir_attempts_rec
                ON idempotency_attempts(record_id, attempt);
            """
        )
        self.con.commit()

    def close(self) -> None:
        self.con.close()

    # ------------------------------------------------------------------
    # 核心 API
    # ------------------------------------------------------------------
    def claim(
        self,
        scope: str,
        operation: str,
        idempotency_key: str,
        payload=None,
        payload_hash: str = "",
        account_id: str = "",
        trace_id: str = "",
        source_system: str = "",
        source_ref: str = "",
        retryable_statuses=DEFAULT_RETRYABLE,
        stale_seconds: float = 0.0,
    ) -> dict:
        """认领幂等键。返回:

        outcome=claimed     -> {attempt_id, attempt}，调用方执行副作用后调 complete()
        outcome=completed   -> {result_ref}，既有最终结果，勿重复执行
        outcome=in_progress -> 既有 attempt 执行中，稳定跳过
        outcome=conflict    -> 同键不同 payload，拒绝

        stale_seconds > 0 时：既有 claimed attempt 超过该秒数未回写终态，
        视为持有者已死（进程崩溃），死信化（标 failed）并开新 attempt。
        """
        if not scope or not operation or not idempotency_key:
            raise ValueError("scope/operation/idempotency_key 必须非空")
        ph = payload_hash or canonical_payload_hash(payload)
        rid = _record_id(self.workspace_id, scope, operation, idempotency_key)
        now = utc_now_iso()

        self.con.execute("BEGIN IMMEDIATE")
        try:
            row = self.con.execute(
                "SELECT * FROM idempotency_records WHERE record_id=?",
                (rid,),
            ).fetchone()

            if row is None:
                cur = self.con.execute(
                    "INSERT INTO idempotency_records"
                    " (record_id, workspace_id, scope, operation, idempotency_key,"
                    "  account_id, payload_hash, status, source_system, source_ref,"
                    "  trace_id, conflict_reason, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        rid, self.workspace_id, scope, operation, idempotency_key,
                        account_id, ph, REC_ACTIVE, source_system, source_ref,
                        trace_id, "", now, now,
                    ),
                )
                if cur.rowcount != 1:
                    raise sqlite3.IntegrityError("record insert failed")
                attempt = 1
                aid = "%s#%04d" % (rid, attempt)
                self.con.execute(
                    "INSERT INTO idempotency_attempts"
                    " (attempt_id, record_id, attempt, status, payload_hash,"
                    "  result_ref, error, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid, rid, attempt, ST_CLAIMED, ph, "", "", now, now),
                )
                self.con.commit()
                return {
                    "outcome": OUT_CLAIMED,
                    "record_id": rid,
                    "attempt_id": aid,
                    "attempt": attempt,
                    "payload_hash": ph,
                }

            # 记录已存在
            if row["status"] == REC_CONFLICT and row["payload_hash"] != ph:
                self.con.rollback()
                return {
                    "outcome": OUT_CONFLICT,
                    "record_id": rid,
                    "conflict_reason": row["conflict_reason"] or "sticky conflict",
                }

            if row["payload_hash"] != ph:
                reason = (
                    "payload_hash mismatch for same idempotency key:"
                    " existing=%s incoming=%s" % (row["payload_hash"][:12], ph[:12])
                )
                cid = "IC-" + uuid.uuid4().hex[:16]
                self.con.execute(
                    "INSERT INTO idempotency_conflicts"
                    " (conflict_id, record_id, payload_hash, existing_hash, reason, created_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (cid, rid, ph, row["payload_hash"], reason, now),
                )
                self.con.execute(
                    "UPDATE idempotency_records"
                    " SET status=?, conflict_reason=?, updated_at=? WHERE record_id=?",
                    (REC_CONFLICT, reason, now, rid),
                )
                self.con.commit()
                return {
                    "outcome": OUT_CONFLICT,
                    "record_id": rid,
                    "conflict_id": cid,
                    "conflict_reason": reason,
                }

            last = self.con.execute(
                "SELECT * FROM idempotency_attempts WHERE record_id=?"
                " ORDER BY attempt DESC LIMIT 1",
                (rid,),
            ).fetchone()

            if last is None:
                # 理论不可达（有记录必有 attempt），fail-visible
                self.con.rollback()
                raise RuntimeError("record without attempts: %s" % rid)

            if last["status"] == ST_COMPLETED:
                self.con.rollback()
                return {
                    "outcome": OUT_COMPLETED,
                    "record_id": rid,
                    "attempt_id": last["attempt_id"],
                    "attempt": last["attempt"],
                    "result_ref": last["result_ref"],
                }

            if last["status"] == ST_CLAIMED:
                # 死信判定：claimed 超过 stale_seconds 未回写 → 持有者已死
                if (
                    stale_seconds
                    and stale_seconds > 0
                    and _age_seconds(last["updated_at"], now) > stale_seconds
                ):
                    dead_reason = "%s >%ss (attempt %d, %s)" % (
                        DEADLETTER_PREFIX, stale_seconds, last["attempt"],
                        last["attempt_id"],
                    )
                    self.con.execute(
                        "UPDATE idempotency_attempts"
                        " SET status=?, error=?, updated_at=? WHERE attempt_id=?",
                        (ST_FAILED, dead_reason, now, last["attempt_id"]),
                    )
                    attempt = last["attempt"] + 1
                    aid = "%s#%04d" % (rid, attempt)
                    self.con.execute(
                        "INSERT INTO idempotency_attempts"
                        " (attempt_id, record_id, attempt, status, payload_hash,"
                        "  result_ref, error, created_at, updated_at)"
                        " VALUES (?,?,?,?,?,?,?,?,?)",
                        (aid, rid, attempt, ST_CLAIMED, ph, "", "", now, now),
                    )
                    self.con.execute(
                        "UPDATE idempotency_records SET updated_at=? WHERE record_id=?",
                        (now, rid),
                    )
                    self.con.commit()
                    return {
                        "outcome": OUT_CLAIMED,
                        "record_id": rid,
                        "attempt_id": aid,
                        "attempt": attempt,
                        "payload_hash": ph,
                        "retry_of": last["attempt_id"],
                        "dead_lettered": last["attempt_id"],
                    }
                self.con.rollback()
                return {
                    "outcome": OUT_IN_PROGRESS,
                    "record_id": rid,
                    "attempt_id": last["attempt_id"],
                    "attempt": last["attempt"],
                }

            if last["status"] in retryable_statuses:
                attempt = last["attempt"] + 1
                aid = "%s#%04d" % (rid, attempt)
                self.con.execute(
                    "INSERT INTO idempotency_attempts"
                    " (attempt_id, record_id, attempt, status, payload_hash,"
                    "  result_ref, error, created_at, updated_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?)",
                    (aid, rid, attempt, ST_CLAIMED, ph, "", "", now, now),
                )
                self.con.execute(
                    "UPDATE idempotency_records SET updated_at=? WHERE record_id=?",
                    (now, rid),
                )
                self.con.commit()
                return {
                    "outcome": OUT_CLAIMED,
                    "record_id": rid,
                    "attempt_id": aid,
                    "attempt": attempt,
                    "payload_hash": ph,
                    "retry_of": last["attempt_id"],
                }

            # 未知终态：拒绝并如实暴露，不猜
            self.con.rollback()
            return {
                "outcome": OUT_CONFLICT,
                "record_id": rid,
                "conflict_reason": "last attempt status not retryable: %s" % last["status"],
            }
        except sqlite3.IntegrityError:
            # 并发竞争：另一进程先建了记录/attempt。回滚后重读并给出稳定结果。
            self.con.rollback()
            return self._stable_reread(rid, ph, retryable_statuses)
        except Exception:
            self.con.rollback()
            raise

    def _stable_reread(self, rid: str, ph: str, retryable_statuses) -> dict:
        row = self.con.execute(
            "SELECT * FROM idempotency_records WHERE record_id=?", (rid,)
        ).fetchone()
        if row is None:
            raise RuntimeError("idempotency record vanished after race: %s" % rid)
        if row["payload_hash"] != ph:
            return {
                "outcome": OUT_CONFLICT,
                "record_id": rid,
                "conflict_reason": "payload_hash mismatch after race",
            }
        last = self.con.execute(
            "SELECT * FROM idempotency_attempts WHERE record_id=?"
            " ORDER BY attempt DESC LIMIT 1",
            (rid,),
        ).fetchone()
        if last is None:
            raise RuntimeError("record without attempts after race: %s" % rid)
        if last["status"] == ST_COMPLETED:
            return {
                "outcome": OUT_COMPLETED,
                "record_id": rid,
                "attempt_id": last["attempt_id"],
                "attempt": last["attempt"],
                "result_ref": last["result_ref"],
            }
        if last["status"] == ST_CLAIMED:
            return {
                "outcome": OUT_IN_PROGRESS,
                "record_id": rid,
                "attempt_id": last["attempt_id"],
                "attempt": last["attempt"],
            }
        return {
            "outcome": OUT_IN_PROGRESS,
            "record_id": rid,
            "attempt_id": last["attempt_id"],
            "attempt": last["attempt"],
            "note": "last attempt status=%s, treat as skip" % last["status"],
        }

    def complete(self, attempt_id: str, result_ref: str = "", ok: bool = True,
                 error: str = "") -> dict:
        """回写 attempt 终态。仅允许 claimed -> completed/failed，不覆盖终态。"""
        now = utc_now_iso()
        self.con.execute("BEGIN IMMEDIATE")
        try:
            row = self.con.execute(
                "SELECT * FROM idempotency_attempts WHERE attempt_id=?",
                (attempt_id,),
            ).fetchone()
            if row is None:
                self.con.rollback()
                return {"ok": False, "error": "unknown attempt_id: %s" % attempt_id}
            if row["status"] != ST_CLAIMED:
                self.con.rollback()
                return {
                    "ok": False,
                    "error": "attempt already terminal (%s), refuse overwrite"
                             % row["status"],
                }
            new_status = ST_COMPLETED if ok else ST_FAILED
            self.con.execute(
                "UPDATE idempotency_attempts"
                " SET status=?, result_ref=?, error=?, updated_at=? WHERE attempt_id=?",
                (new_status, result_ref, error, now, attempt_id),
            )
            self.con.execute(
                "UPDATE idempotency_records SET updated_at=? WHERE record_id=?",
                (now, row["record_id"]),
            )
            self.con.commit()
            return {"ok": True, "status": new_status, "attempt_id": attempt_id}
        except Exception:
            self.con.rollback()
            raise

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, scope: str, operation: str, idempotency_key: str) -> dict:
        rid = _record_id(self.workspace_id, scope, operation, idempotency_key)
        row = self.con.execute(
            "SELECT * FROM idempotency_records WHERE record_id=?", (rid,)
        ).fetchone()
        if row is None:
            return {"exists": False}
        attempts = [
            dict(a)
            for a in self.con.execute(
                "SELECT * FROM idempotency_attempts WHERE record_id=? ORDER BY attempt",
                (rid,),
            ).fetchall()
        ]
        conflicts = [
            dict(c)
            for c in self.con.execute(
                "SELECT * FROM idempotency_conflicts WHERE record_id=? ORDER BY created_at",
                (rid,),
            ).fetchall()
        ]
        rec = dict(row)
        rec["exists"] = True
        rec["attempts"] = attempts
        rec["conflicts"] = conflicts
        return rec

    def stats(self) -> dict:
        rec_n = self.con.execute(
            "SELECT COUNT(*) AS n FROM idempotency_records"
        ).fetchone()["n"]
        att_n = self.con.execute(
            "SELECT COUNT(*) AS n FROM idempotency_attempts"
        ).fetchone()["n"]
        con_n = self.con.execute(
            "SELECT COUNT(*) AS n FROM idempotency_conflicts"
        ).fetchone()["n"]
        by_outcome = {
            r["status"]: r["n"]
            for r in self.con.execute(
                "SELECT status, COUNT(*) AS n FROM idempotency_attempts GROUP BY status"
            ).fetchall()
        }
        dead_letters = self.con.execute(
            "SELECT COUNT(*) AS n FROM idempotency_attempts WHERE error LIKE ?",
            (DEADLETTER_PREFIX + "%",),
        ).fetchone()["n"]
        return {
            "records": rec_n,
            "attempts": att_n,
            "conflicts": con_n,
            "attempts_by_status": by_outcome,
            "dead_letters": dead_letters,
        }


def main(argv=None) -> int:
    """运维 CLI：stats / get / fail（人工恢复=将执行中 attempt 标记失败以允许重试）。"""
    import argparse

    ap = argparse.ArgumentParser(description="统一幂等账本运维 CLI")
    ap.add_argument("--db", required=True)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p_get = sub.add_parser("get")
    p_get.add_argument("--scope", required=True)
    p_get.add_argument("--operation", required=True)
    p_get.add_argument("--key", required=True)
    p_fail = sub.add_parser("fail")
    p_fail.add_argument("--attempt-id", required=True)
    p_fail.add_argument("--reason", default="manual: operator recovery")
    args = ap.parse_args(argv)

    store = IdempotencyStore(args.db)
    try:
        if args.cmd == "stats":
            print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        elif args.cmd == "get":
            print(json.dumps(store.get(args.scope, args.operation, args.key),
                             ensure_ascii=False, indent=2))
        elif args.cmd == "fail":
            print(json.dumps(store.complete(args.attempt_id, ok=False,
                                            error=args.reason),
                             ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
