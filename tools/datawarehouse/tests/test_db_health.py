# -*- coding: utf-8 -*-
"""db_health 单测：用临时 SQLite 验证 ok/warn/fail 三态（不依赖真实业务库）。"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import db_health as m  # noqa: E402


class TestDbHealth(unittest.TestCase):
    def test_wal_ok(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "x.db"
            con = sqlite3.connect(str(p))
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("CREATE TABLE t(a TEXT)")
            con.execute("INSERT INTO t VALUES('x')")
            con.commit()
            con.close()
            r = m.check_one("demo", p, True, True)
            self.assertEqual(r["issues"], [], msg=str(r))
            self.assertEqual(r["journal_mode"], "wal")
            self.assertEqual(r["quick_check"], "ok")

    def test_missing_fail(self):
        r = m.check_one("missing", Path(r"C:\definitely\not\exist.db"), True, True)
        self.assertIn("missing_db", r["issues"])

    def test_delete_mode_warns_when_wal_expected(self):
        with TemporaryDirectory() as td:
            p = Path(td) / "y.db"
            con = sqlite3.connect(str(p))
            con.execute("CREATE TABLE t(a TEXT)")
            con.execute("PRAGMA journal_mode=DELETE")
            con.commit()
            con.close()
            r = m.check_one("demo2", p, True, True)
            self.assertIn("journal_mode_delete_expect_wal", r["warnings"])

    def test_collect_aggregate_codes(self):
        res = m.collect(["news"])
        self.assertEqual(res["tool"], "db_health")
        self.assertIn("dbs", res)
        self.assertIn(res["code"], (0, 1, 2))


if __name__ == "__main__":
    unittest.main()
