# -*- coding: utf-8 -*-
"""2026-10-17 复盘审查修复回归（只读面写副作用专项）。

覆盖三个修复：
1. replay_query._load_approvals：库缺失时返回空且**不静默建 approvals.db**
   （原经 ApprovalStore 打开 → mkdir+建表写路径，14:45 判例同源）；
2. replay_query._load_idem_rows：mode=ro 打开；
3. engine.evidence.EvidenceStore(read_only=True)：库缺失拒绝打开、
   有库时读路径零 DDL。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

_AGENT_PLATFORM = Path(__file__).resolve().parents[1]
if str(_AGENT_PLATFORM) not in sys.path:
    sys.path.insert(0, str(_AGENT_PLATFORM))

from adapters import replay_query as rq          # noqa: E402
from engine import evidence as ev_mod            # noqa: E402
from contracts import Evidence                   # noqa: E402


class ReplayQueryReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._orig_apv = rq._APPROVALS_DB
        self._orig_idem = rq._IDEM_DB

    def tearDown(self):
        rq._APPROVALS_DB = self._orig_apv
        rq._IDEM_DB = self._orig_idem

    def test_load_approvals_missing_db_no_creation(self):
        """库缺失 → 空列表，且绝不静默建 approvals.db。"""
        ghost = self.tmp / "approvals.db"
        rq._APPROVALS_DB = ghost
        self.assertFalse(ghost.exists())
        self.assertEqual(rq._load_approvals(), [])
        self.assertFalse(ghost.exists(), "只读查询路径不得建库")

    def test_load_approvals_reads_real_db(self):
        """库在 → mode=ro 直连能读且字段映射正确。"""
        import sqlite3
        db = self.tmp / "approvals.db"
        conn = sqlite3.connect(db)
        conn.executescript(
            "CREATE TABLE approvals (approval_id TEXT PRIMARY KEY,"
            " idempotency_key TEXT UNIQUE, target_type TEXT, target_id TEXT,"
            " from_status TEXT, to_status TEXT, decision TEXT,"
            " decided_by TEXT, reason TEXT DEFAULT '', decided_at TEXT,"
            " schema_version INTEGER DEFAULT 1, created_at TEXT,"
            " trace_id TEXT DEFAULT '');"
            "INSERT INTO approvals (approval_id, idempotency_key,"
            " target_type, target_id, from_status, to_status, decision,"
            " decided_by, reason, decided_at, created_at) VALUES"
            " ('a1','k1','workitem','gzh:d1','draft','reviewed','approved',"
            "'老田','ok','2026-10-17T10:00:00+08:00','2026-10-17T10:00:00Z');")
        conn.commit()
        conn.close()
        rq._APPROVALS_DB = db
        rows = rq._load_approvals()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ref"], "a1")
        self.assertEqual(rows[0]["kind"], "approval:reviewed")
        self.assertEqual(rows[0]["target_id"], "gzh:d1")


class EvidenceReadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_read_only_missing_db_refused(self):
        """库缺失 → FileNotFoundError，不建文件不建目录。"""
        db = self.tmp / "sub" / "evidence.db"
        with self.assertRaises(FileNotFoundError):
            ev_mod.EvidenceStore(db, read_only=True)
        self.assertFalse(db.exists())

    def test_read_only_no_ddl_on_existing(self):
        """库在 → 只读连接可读，零 DDL（无写事务）。"""
        db = self.tmp / "evidence.db"
        w = ev_mod.EvidenceStore(db)
        w.record(Evidence(source="s", source_ref="r1", statement="st1",
                          payload={"domain": "d", "kind": "k"}))
        w.close()
        ro = ev_mod.EvidenceStore(db, read_only=True)
        n = ro._conn.execute(
            "SELECT COUNT(*) FROM evidence").fetchone()[0]
        ro.close()
        self.assertEqual(n, 1)
        # 只读连接再开一次仍可读（WAL 附属文件重建不影响数据）
        ro2 = ev_mod.EvidenceStore(db, read_only=True)
        n2 = ro2._conn.execute(
            "SELECT COUNT(*) FROM evidence").fetchone()[0]
        ro2.close()
        self.assertEqual(n2, 1)


if __name__ == "__main__":
    unittest.main()
