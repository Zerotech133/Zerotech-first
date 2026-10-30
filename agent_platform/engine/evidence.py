"""统一证据仓（M2-P1）。

定位：六大业务域可观测事实的唯一归集账本——谁（source/source_ref）、
观测到什么（statement）、内容指纹（fact_hash）、何时（observed_at/
recorded_at）。只做观测记录，不做承诺审批（承诺走 ApprovalStore，
判例"凭据不泛滥"）；不做真值校验（校验属 M2-P3 事实核验 Skill）。

存储纪律（与 engine/approvals.py、engine/idempotency.py 同款）：
- SQLite WAL + busy_timeout；
- 写事务用裸 ``BEGIN IMMEDIATE`` + 显式 commit/rollback；
- 并发场景保持常驻 keeper 连接（Windows WAL shm 重开竞争判例）。

去重语义：
- fact_hash = sha256(canonical(domain, kind, source, source_ref,
  statement))——同一事实重复观测 → replayed=True 零副作用；
- 真源变化 → statement 变 → fact_hash 变 → 新增一行（append-only
  观测日志，历史不覆盖不删除）。

CLI：``python engine/evidence.py stats`` / ``ls [--domain gzh]``。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR.parent))

from contracts import Evidence  # type: ignore  # noqa: E402
import logging

SCHEMA = """
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id    TEXT PRIMARY KEY,
    domain         TEXT NOT NULL,
    kind           TEXT NOT NULL,
    source         TEXT NOT NULL,
    source_ref     TEXT NOT NULL,
    statement      TEXT NOT NULL,
    fact_hash      TEXT NOT NULL,
    observed_at    TEXT,
    recorded_at    TEXT NOT NULL,
    payload        TEXT NOT NULL DEFAULT '{}',
    trace_id       TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_evidence_domain
    ON evidence(domain, recorded_at);
CREATE UNIQUE INDEX IF NOT EXISTS idx_evidence_fact
    ON evidence(fact_hash);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def fact_hash(
    domain: str, kind: str, source: str, source_ref: str, statement: str
) -> str:
    """事实指纹：canonical JSON sha256（M0 幂等账本同款，禁 NaN）。"""
    canonical = json.dumps(
        {
            "domain": domain,
            "kind": kind,
            "source": source,
            "source_ref": source_ref,
            "statement": statement,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class EvidenceStore:
    """append-only 证据账本；fact_hash 唯一去重。

    read_only=True（2026-10-17 审查判例）：mode=ro 直连，跳过
    mkdir/WAL 切换/DDL——只读投影端点必须传 True，否则每次 GET 都
    对事实源执行建表与日志模式写副作用。
    """

    def __init__(self, db_path, read_only: bool = False):
        self.db_path = Path(db_path)
        if read_only:
            if not self.db_path.exists():
                raise FileNotFoundError(
                    "evidence db 不存在：%s（只读模式不建库）" % self.db_path
                )
            self._conn = sqlite3.connect(
                "file:%s?mode=ro" % self.db_path.as_posix(), uri=True, timeout=30
            )
            self._conn.row_factory = sqlite3.Row
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout=30000")
        try:
            mode = self._conn.execute("PRAGMA journal_mode").fetchone()[0]
            if str(mode).lower() != "wal":
                self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError as exc:
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/engine/evidence.py", exc)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    def record(self, ev: Evidence) -> Tuple[Evidence, bool]:
        """登记一条证据；同 fact_hash 幂等重放（replayed=True）。"""
        fh = ev.payload.get("fact_hash") or fact_hash(
            ev.payload.get("domain", ""),
            ev.payload.get("kind", ""),
            ev.source,
            ev.source_ref,
            ev.statement,
        )
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO evidence(evidence_id, domain, kind, source,"
                " source_ref, statement, fact_hash, observed_at,"
                " recorded_at, payload, trace_id, schema_version)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    ev.evidence_id,
                    ev.payload.get("domain", ""),
                    ev.payload.get("kind", ""),
                    ev.source,
                    ev.source_ref,
                    ev.statement,
                    fh,
                    ev.observed_at,
                    _now_iso(),
                    json.dumps(
                        {
                            k: v
                            for k, v in ev.payload.items()
                            if k not in ("fact_hash", "domain", "kind")
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    ev.trace_id,
                    ev.schema_version,
                ),
            )
            self._conn.commit()
            return ev, False
        except sqlite3.IntegrityError:
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT * FROM evidence WHERE fact_hash=?", (fh,)
            ).fetchone()
            return self._row_to_evidence(row), True
        except Exception:
            self._conn.rollback()
            raise

    def list(self, domain: Optional[str] = None, limit: int = 500) -> List[Evidence]:
        sql = (
            "SELECT * FROM evidence"  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
            + (" WHERE domain=?" if domain else "")
            + " ORDER BY recorded_at, rowid LIMIT ?"
        )
        args = [domain, limit] if domain else [limit]
        rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_evidence(r) for r in rows]

    def stats(self) -> Dict:
        total = self._conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0]
        by_domain = {
            r["domain"]: r["n"]
            for r in self._conn.execute(
                "SELECT domain, COUNT(*) AS n FROM evidence"
                " GROUP BY domain ORDER BY domain"
            )
        }
        by_kind = {
            r["kind"]: r["n"]
            for r in self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM evidence GROUP BY kind ORDER BY kind"
            )
        }
        return {"total": total, "by_domain": by_domain, "by_kind": by_kind}

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_evidence(row) -> Evidence:
        payload = json.loads(row["payload"] or "{}")
        payload.update(
            {
                "domain": row["domain"],
                "kind": row["kind"],
                "fact_hash": row["fact_hash"],
            }
        )
        return Evidence(
            evidence_id=row["evidence_id"],
            source=row["source"],
            source_ref=row["source_ref"],
            statement=row["statement"],
            observed_at=row["observed_at"],
            payload=payload,
            trace_id=row["trace_id"],
            schema_version=row["schema_version"],
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="统一证据仓 CLI（只读运维）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p_ls = sub.add_parser("ls")
    p_ls.add_argument("--domain")
    p_ls.add_argument("--limit", type=int, default=30)
    args = ap.parse_args(argv)

    db = _ENGINE_DIR.parent / "data" / "evidence" / "evidence.db"
    if not db.exists():
        print(json.dumps({"ledger_exists": False, "total": 0}, ensure_ascii=False))
        return 0
    store = EvidenceStore(db)
    try:
        if args.cmd == "stats":
            print(json.dumps(store.stats(), ensure_ascii=False, indent=1))
        else:
            for ev in store.list(domain=args.domain, limit=args.limit):
                print(
                    "%s | %s | %s | %s | %s"
                    % (
                        ev.payload.get("domain", ""),
                        ev.payload.get("kind", ""),
                        ev.source_ref,
                        ev.statement[:60],
                        ev.payload.get("fact_hash", "")[:12],
                    )
                )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
