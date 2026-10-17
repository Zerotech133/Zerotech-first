# -*- coding: utf-8 -*-
"""M1 统一审批注册表（ApprovalStore）。

真源纪律：
- Approval 是**审批事实**的唯一真源：append-only，幂等键唯一；
- 同一 idempotency_key 重复记录 → 返回既有记录（replayed=True），
  不报错、不重复落行（与 IdempotencyStore 四态 claim 同哲学：稳定结果）；
- 审批 ≠ 执行：本模块只记"谁批准了什么转移"，不负责改任何领域状态。

存储：SQLite WAL（与 engine/idempotency.py 同款纪律）；
表：approvals(approval_id PK, idempotency_key UNIQUE, ...)。

Windows 判例（2026-10-17 实测定案）：WAL 库所有连接关闭后 -shm 被删除，
多个连接**同时重开**重建 shm 存在竞争，部分连接会得到假性
"attempt to write a readonly database"。因此并发写场景必须保持至少一个
常驻连接（keeper，M0 测试同款模式）；单连接串行 CLI 用法不受影响。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

_AGENT_PLATFORM = str(Path(__file__).resolve().parents[1])
if _AGENT_PLATFORM not in sys.path:
    sys.path.insert(0, _AGENT_PLATFORM)

from contracts.objects import Approval  # noqa: E402

SCHEMA = """
CREATE TABLE IF NOT EXISTS approvals (
    approval_id     TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    target_type     TEXT NOT NULL,
    target_id       TEXT NOT NULL,
    from_status     TEXT NOT NULL,
    to_status       TEXT NOT NULL,
    decision        TEXT NOT NULL,
    decided_by      TEXT NOT NULL,
    reason          TEXT NOT NULL DEFAULT '',
    decided_at      TEXT NOT NULL,
    schema_version  INTEGER NOT NULL DEFAULT 1,
    created_at      TEXT NOT NULL,
    trace_id        TEXT NOT NULL DEFAULT '',
    content_hash    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_approvals_target
    ON approvals(target_type, target_id);
"""


def _ensure_columns(conn: sqlite3.Connection) -> None:
    """向后兼容加列（既有库不含 trace_id/content_hash → ALTER）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(approvals)")}
    if "trace_id" not in cols:
        conn.execute("ALTER TABLE approvals ADD COLUMN trace_id TEXT NOT NULL DEFAULT ''")
        conn.commit()
    if "content_hash" not in cols:
        conn.execute("ALTER TABLE approvals ADD COLUMN content_hash TEXT NOT NULL DEFAULT ''")
        conn.commit()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ApprovalStore:
    """审批注册表。record() 幂等；list/get 只读。"""

    def __init__(self, db_path):
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=10000")
        self._conn.executescript(SCHEMA)
        _ensure_columns(self._conn)
        self._conn.commit()

    # ------------------------------------------------------------------
    def record(self, approval: Approval) -> Tuple[Approval, bool]:
        """记录一条审批。幂等：同 idempotency_key 返回既有记录。

        同键同参（target_type/target_id/from_status/to_status/decision
        五元组一致）→ 重放语义返回既有记录；
        同键异参 → 拒绝（M0 IdempotencyRecord 契约：同键不同 payload
        必须 fail-visible，不得静默吞成重放——2026-10-17 审计补齐）。

        返回 (approval, replayed)。
        """
        if not approval.idempotency_key:
            raise ValueError("Approval.idempotency_key 必填（确定性键）")
        if approval.decision not in ("approved", "rejected"):
            raise ValueError("decision 只能是 approved / rejected")
        # 裸 BEGIN IMMEDIATE 前置拿写锁 + 显式 commit/rollback
        #（与 M0 idempotency.claim 同款事务纪律；勿改 with-上下文写法，
        #  Windows 多连接下会触发 WAL readonly 异常——2026-10-17 实测定案）
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO approvals(approval_id, idempotency_key,"
                " target_type, target_id, from_status, to_status,"
                " decision, decided_by, reason, decided_at,"
                " schema_version, created_at, trace_id, content_hash)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (approval.approval_id, approval.idempotency_key,
                 approval.target_type, approval.target_id,
                 approval.from_status, approval.to_status,
                 approval.decision, approval.decided_by,
                 approval.reason,
                 approval.decided_at or _now_iso(),
                 approval.schema_version, _now_iso(),
                 approval.trace_id or "", approval.content_hash or ""))
            self._conn.commit()
            return approval, False
        except sqlite3.IntegrityError:
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT * FROM approvals WHERE idempotency_key=?",
                (approval.idempotency_key,)).fetchone()
            existing = self._row_to_approval(row)
            # M0 契约：同键异参拒绝（粘性冲突语义与 IdempotencyStore 对齐）
            mismatch = {
                f: (getattr(existing, f), getattr(approval, f))
                for f in ("target_type", "target_id", "from_status",
                          "to_status", "decision")
                if getattr(existing, f) != getattr(approval, f)
            }
            # M3-P3：content_hash 绑定冲突——同键凭据已绑 H1，本次声明
            # H2 → 批 A 发 B 检测点（store 层统一防住，域层快路径另校验）
            if (approval.content_hash and existing.content_hash
                    and approval.content_hash != existing.content_hash):
                mismatch["content_hash"] = (existing.content_hash,
                                            approval.content_hash)
            if mismatch:
                raise ValueError(
                    "幂等键冲突（同键异参）：%s 既有 %r，本次 %r"
                    % (approval.idempotency_key, mismatch,
                       approval.decided_by))
            return existing, True
        except Exception:
            self._conn.rollback()
            raise

    def get(self, approval_id: str) -> Optional[Approval]:
        row = self._conn.execute(
            "SELECT * FROM approvals WHERE approval_id=?",
            (approval_id,)).fetchone()
        return self._row_to_approval(row) if row else None

    def list(self, target_type: str = None, target_id: str = None) -> List[Approval]:
        sql = "SELECT * FROM approvals"
        cond, args = [], []
        if target_type:
            cond.append("target_type=?")
            args.append(target_type)
        if target_id:
            cond.append("target_id=?")
            args.append(target_id)
        if cond:
            sql += " WHERE " + " AND ".join(cond)
        # rowid 兜底保证同秒记录的 append 顺序（审批账本按发生序回放）
        sql += " ORDER BY created_at, rowid"
        return [self._row_to_approval(r) for r in
                self._conn.execute(sql, args).fetchall()]

    def stats(self) -> dict:
        row = self._conn.execute(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT target_id) AS targets"
            " FROM approvals").fetchone()
        by_decision = {r["decision"]: r["n"] for r in self._conn.execute(
            "SELECT decision, COUNT(*) AS n FROM approvals"
            " GROUP BY decision").fetchall()}
        return {"count": row["n"], "distinct_targets": row["targets"],
                "by_decision": by_decision}

    def close(self):
        self._conn.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_approval(row) -> Approval:
        keys = row.keys()
        return Approval(
            approval_id=row["approval_id"],
            idempotency_key=row["idempotency_key"],
            target_type=row["target_type"],
            target_id=row["target_id"],
            from_status=row["from_status"],
            to_status=row["to_status"],
            decision=row["decision"],
            decided_by=row["decided_by"],
            reason=row["reason"],
            decided_at=row["decided_at"],
            schema_version=row["schema_version"],
            trace_id=row["trace_id"] if "trace_id" in keys else "",
            content_hash=row["content_hash"] if "content_hash" in keys else "",
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="统一审批注册表 CLI")
    ap.add_argument("--db", default=str(Path(__file__).resolve().parents[1]
                                        / "data" / "approvals" / "approvals.db"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p_list = sub.add_parser("ls")
    p_list.add_argument("--target-type", default=None)
    p_list.add_argument("--target-id", default=None)
    args = ap.parse_args(argv)

    store = ApprovalStore(args.db)
    try:
        if args.cmd == "stats":
            print(json.dumps(store.stats(), ensure_ascii=False, indent=2))
        elif args.cmd == "ls":
            rows = [a.to_dict() for a in store.list(args.target_type, args.target_id)]
            print(json.dumps(rows, ensure_ascii=False, indent=2))
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
