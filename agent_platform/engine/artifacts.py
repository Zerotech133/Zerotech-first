# -*- coding: utf-8 -*-
"""统一产物登记仓（M3-P1）。

定位：三产线（软件 / 路演 PPT / 自媒体）交付物的唯一登记账本——
回答总纲验收句五问：来源（source_system/source_ref）、审查（登记本身
不设门，审查凭据走 ApprovalStore，M3-P3 绑 content_hash）、版本
（revision/supersedes）、负责人（produced_by）、可回滚（supersede 链
append-only，历史条目永不删除）。

判例「登记不设门」：登记是**事实记录**（观测≠承诺）——产物诞生即
登记，发布/替换才设 Approval 门。因此 record() 无任何状态校验，
只做 append-only + content_hash 去重。

去重语义：
- content_hash（空则回退 fact_hash）全局唯一索引——同一内容重复登记
  → replayed=True，复用既有 artifact_id（同 hash 复用 artifact_id）；
- 内容变更 → 新 hash → 新行（新 revision），supersedes 指回旧
  artifact_id，链路 append-only 可回放。

存储纪律（与 engine/evidence.py、engine/approvals.py 同款）：
- SQLite WAL + busy_timeout；
- 写事务用裸 ``BEGIN IMMEDIATE`` + 显式 commit/rollback；
- 并发场景保持常驻 keeper 连接（Windows WAL shm 重开竞争判例）；
- read_only=True：mode=ro 直连，跳过 mkdir/WAL/DDL（只读投影端点
  必传，否则 GET 产生建库写副作用——2026-10-17 审查判例）。

CLI：``python engine/artifacts.py stats`` / ``ls`` / ``chain <artifact_id>``。
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_ENGINE_DIR = Path(__file__).resolve().parent
if str(_ENGINE_DIR.parent) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR.parent))

from contracts.objects import Artifact  # type: ignore  # noqa: E402
import logging

SCHEMA = """
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id   TEXT PRIMARY KEY,
    kind          TEXT NOT NULL,
    path          TEXT NOT NULL DEFAULT '',
    content_hash  TEXT NOT NULL,
    fact_hash     TEXT NOT NULL DEFAULT '',
    revision      TEXT NOT NULL DEFAULT 'r1',
    supersedes    TEXT NOT NULL DEFAULT '',
    workitem_id   TEXT NOT NULL DEFAULT '',
    source_system TEXT NOT NULL DEFAULT '',
    source_ref    TEXT NOT NULL DEFAULT '',
    produced_by   TEXT NOT NULL DEFAULT '',
    missing       INTEGER NOT NULL DEFAULT 0,
    registered_at TEXT NOT NULL,
    payload       TEXT NOT NULL DEFAULT '{}',
    trace_id      TEXT NOT NULL DEFAULT '',
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_artifacts_source
    ON artifacts(source_system, registered_at);
CREATE INDEX IF NOT EXISTS idx_artifacts_supersedes
    ON artifacts(supersedes);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_hash
    ON artifacts(content_hash);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class ArtifactStore:
    """append-only 产物登记账本；content_hash 唯一去重。"""

    def __init__(self, db_path, read_only: bool = False):
        self.db_path = Path(db_path)
        if read_only:
            if not self.db_path.exists():
                raise FileNotFoundError(
                    "artifact db 不存在：%s（只读模式不建库）" % self.db_path
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
            logging.getLogger(__name__).warning("silent-except %s: %s", "agent_platform/engine/artifacts.py", exc)
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    # ------------------------------------------------------------------
    def record(self, art: Artifact) -> Tuple[Artifact, bool]:
        """登记一份产物；同 content_hash 幂等重放（replayed=True）。

        登记不设门：不做状态/来源校验，只保证 append-only 与去重。
        崩溃语义：content_hash 唯一索引是去重的真源——同事务内
        IntegrityError → 读回既有行按重放处理（与 ApprovalStore 同构，
        M3-P1 起同键异参语义不适用：产物无参数五元组，内容即身份）。
        """
        chash = art.effective_hash()
        if not chash:
            raise ValueError("artifact 缺内容指纹：content_hash/fact_hash 至少其一非空")
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            self._conn.execute(
                "INSERT INTO artifacts(artifact_id, kind, path,"
                " content_hash, fact_hash, revision, supersedes,"
                " workitem_id, source_system, source_ref, produced_by,"
                " missing, registered_at, payload, trace_id,"
                " schema_version) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    art.artifact_id,
                    art.kind,
                    art.path,
                    chash,
                    art.fact_hash,
                    art.revision,
                    art.supersedes,
                    art.workitem_id,
                    art.source_system,
                    art.source_ref,
                    art.produced_by,
                    1 if art.missing else 0,
                    _now_iso(),
                    # payload 列为扩展槽（Artifact 契约暂无此字段，恒写 '{}'；
                    # 未来扩展只加列不动契约，读侧不映射不丢真源信息）
                    "{}",
                    art.trace_id,
                    art.schema_version,
                ),
            )
            self._conn.commit()
            return art, False
        except sqlite3.IntegrityError:
            self._conn.rollback()
            row = self._conn.execute(
                "SELECT * FROM artifacts WHERE content_hash=?", (chash,)
            ).fetchone()
            return self._row_to_artifact(row), True
        except Exception:
            self._conn.rollback()
            raise

    def supersede(
        self, old_artifact_id: str, new_art: Artifact, produced_by: str = ""
    ) -> Tuple[Artifact, bool]:
        """以新内容替代旧产物：新 revision = 旧 + 1，supersedes 指旧。

        旧条目 append-only 保留（可回滚=沿链回溯），本方法不删不改
        任何既有行。old_artifact_id 不存在时 fail-visible 拒绝——
        防止伪造替代链（判例「无源 ID 不伪造」）。
        """
        old = self.get(old_artifact_id)
        if old is None:
            raise ValueError(
                "supersede 目标不存在：%s（无源 ID 不伪造）" % old_artifact_id
            )
        old_rev = int(old.revision.lstrip("r") or "1")
        new_art.supersedes = old_artifact_id
        new_art.revision = "r%d" % (old_rev + 1)
        if produced_by and not new_art.produced_by:
            new_art.produced_by = produced_by
        if not new_art.workitem_id:
            new_art.workitem_id = old.workitem_id
        if not new_art.source_system:
            new_art.source_system = old.source_system
        if not new_art.source_ref:
            new_art.source_ref = old.source_ref
        return self.record(new_art)

    # ------------------------------------------------------------------
    def get(self, artifact_id: str) -> Optional[Artifact]:
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE artifact_id=?", (artifact_id,)
        ).fetchone()
        return self._row_to_artifact(row) if row else None

    def by_hash(self, content_hash: str) -> Optional[Artifact]:
        row = self._conn.execute(
            "SELECT * FROM artifacts WHERE content_hash=?", (content_hash,)
        ).fetchone()
        return self._row_to_artifact(row) if row else None

    def list(
        self, source_system: Optional[str] = None, limit: int = 500
    ) -> List[Artifact]:
        sql = (
            "SELECT * FROM artifacts"  # nosec B608 - 动态标识符来自内部白名单/值已参数化（已抽查）
            + (" WHERE source_system=?" if source_system else "")
            + " ORDER BY registered_at, rowid LIMIT ?"
        )
        args = [source_system, limit] if source_system else [limit]
        rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_artifact(r) for r in rows]

    def chain(self, artifact_id: str) -> List[Artifact]:
        """沿 supersedes 链从当前条目回溯到首版（含自身，旧→新有序）。"""
        chain: List[Artifact] = []
        seen = set()
        cur = self.get(artifact_id)
        while cur is not None and cur.artifact_id not in seen:
            seen.add(cur.artifact_id)
            chain.append(cur)
            cur = self.get(cur.supersedes) if cur.supersedes else None
        chain.reverse()  # 旧→新
        return chain

    def stats(self) -> Dict:
        total = self._conn.execute("SELECT COUNT(*) FROM artifacts").fetchone()[0]
        by_source = {
            r["source_system"] or "(unspecified)": r["n"]
            for r in self._conn.execute(
                "SELECT source_system, COUNT(*) AS n"
                " FROM artifacts GROUP BY source_system"
                " ORDER BY source_system"
            )
        }
        by_kind = {
            r["kind"]: r["n"]
            for r in self._conn.execute(
                "SELECT kind, COUNT(*) AS n FROM artifacts GROUP BY kind ORDER BY kind"
            )
        }
        superseded = self._conn.execute(
            "SELECT COUNT(*) FROM artifacts WHERE supersedes!=''"
        ).fetchone()[0]
        return {
            "total": total,
            "by_source": by_source,
            "by_kind": by_kind,
            "superseded_count": superseded,
        }

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_artifact(row) -> Artifact:
        # payload 列为扩展槽（恒 '{}'），Artifact 契约无此字段，读侧忽略
        return Artifact(
            artifact_id=row["artifact_id"],
            workitem_id=row["workitem_id"],
            kind=row["kind"],
            path=row["path"],
            fact_hash=row["fact_hash"],
            version="1",
            missing=bool(row["missing"]),
            content_hash=row["content_hash"],
            revision=row["revision"],
            source_system=row["source_system"],
            source_ref=row["source_ref"],
            produced_by=row["produced_by"],
            supersedes=row["supersedes"],
            trace_id=row["trace_id"],
            schema_version=row["schema_version"],
        )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="统一产物登记仓 CLI（只读运维）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p_ls = sub.add_parser("ls")
    p_ls.add_argument("--source")
    p_ls.add_argument("--limit", type=int, default=30)
    p_ch = sub.add_parser("chain")
    p_ch.add_argument("artifact_id")
    args = ap.parse_args(argv)

    db = _ENGINE_DIR.parent / "data" / "artifacts" / "artifacts.db"
    if not db.exists():
        print(json.dumps({"ledger_exists": False, "total": 0}, ensure_ascii=False))
        return 0
    store = ArtifactStore(db)
    try:
        if args.cmd == "stats":
            print(json.dumps(store.stats(), ensure_ascii=False, indent=1))
        elif args.cmd == "chain":
            for a in store.chain(args.artifact_id):
                print(
                    "%s r=%s prev=%s hash=%s %s"
                    % (
                        a.artifact_id[:12],
                        a.revision,
                        a.supersedes[:12] or "-",
                        a.effective_hash()[:12],
                        a.path,
                    )
                )
        else:
            for a in store.list(source_system=args.source, limit=args.limit):
                print(
                    "%s | %s | %s | %s | %s"
                    % (
                        a.revision,
                        a.kind,
                        a.source_ref or a.workitem_id,
                        a.path[:40],
                        a.effective_hash()[:12],
                    )
                )
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
