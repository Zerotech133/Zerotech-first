# -*- coding: utf-8 -*-
"""FEEDBACK-L1 单测：obs_bridge 落观测 + 出口接入。"""
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
NC = os.path.normpath(os.path.join(HERE, "..", "..", "news_collector"))
INTEL = os.path.normpath(os.path.join(HERE, "..", "..", "intel_collector"))
for p in (NC, INTEL):
    if p not in sys.path:
        sys.path.insert(0, p)

import obs_bridge


class TestObsBridge(unittest.TestCase):
    def test_record_ok(self):
        """ok=True, got>0 → status=ok 观测落库。"""
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "o.db")
            self.assertTrue(obs_bridge.record("news_sina", True, got=5, db_path=db))
            con = sqlite3.connect(db)
            rows = con.execute("SELECT source_id, status, payload_json FROM observations").fetchall()
            con.close()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][0], "news_sina")
        self.assertEqual(rows[0][1], "ok")
        self.assertIn('"got": 5', rows[0][2])

    def test_record_error(self):
        """ok=False → status=error 观测落库。"""
        with tempfile.TemporaryDirectory() as td:
            db = os.path.join(td, "o.db")
            self.assertTrue(obs_bridge.record("news_x", False, err="timeout", db_path=db))
            con = sqlite3.connect(db)
            n, st = con.execute("SELECT source_id, status FROM observations").fetchone()
            con.close()
        self.assertEqual(st, "error")
        self.assertEqual(n, "news_x")

    def test_bridge_never_raises(self):
        """桥故障（非法 db 路径）→ False 不抛。"""
        with tempfile.TemporaryDirectory() as td:
            bad = os.path.join(td, "no_dir_sub", "o.db")  # 父目录不存在
            self.assertFalse(obs_bridge.record("news_y", True, got=1, db_path=bad))

    def test_collector_hook_in_source(self):
        """news_collector.main 出口已接 obs_bridge（源码断言）。"""
        src = open(os.path.join(NC, "news_collector.py"), encoding="utf-8").read()
        self.assertIn("import obs_bridge", src)
        self.assertIn("obs_bridge.record(", src)
        self.assertIn("FEEDBACK-L1", src)


if __name__ == "__main__":
    unittest.main()
