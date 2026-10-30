# -*- coding: utf-8 -*-
"""P2.6: SQLite 滑窗限流（多实例共享），+ 持久化任务测试。"""
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(WT, "portal"))
sys.path.insert(0, WT)

import rate_limit  # noqa: E402
from commercial import job_store  # noqa: E402


class TestSqliteRateLimit(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.td.name, "rate.db")

    def tearDown(self):
        self.td.cleanup()

    def test_shared_between_instances(self):
        a = rate_limit.SqliteSlidingWindow("login", 2, 60, db=self.db)
        b = rate_limit.SqliteSlidingWindow("login", 2, 60, db=self.db)
        self.assertTrue(a.allow("ip1"))
        self.assertTrue(b.allow("ip1"))   # 另一个实例看到共享计数
        self.assertFalse(a.allow("ip1"))  # 超出
        self.assertTrue(a.allow("ip2"))

    def test_window_expiry(self):
        w = rate_limit.SqliteSlidingWindow("k", 1, 0.5, db=self.db)
        base = 1000.0
        self.assertTrue(w.allow("x", now=base))
        self.assertFalse(w.allow("x", now=base + 0.1))
        self.assertTrue(w.allow("x", now=base + 0.6))


class TestJobStore(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = os.path.join(self.td.name, "jobs.db")

    def tearDown(self):
        self.td.cleanup()

    def test_create_update_get(self):
        job_store.create("job-1", "acme", "600337.SH", db=self.db)
        j = job_store.get("acme", "job-1", db=self.db)
        self.assertEqual(j["status"], "queued")
        job_store.update("job-1", db=self.db, status="running", started=123.0)
        job_store.update("job-1", db=self.db, status="done", finished=456.0,
                         result={"rating": "观察", "run_id": "r1"})
        j = job_store.get("acme", "job-1", db=self.db)
        self.assertEqual(j["status"], "done")
        self.assertEqual(j["result"]["rating"], "观察")
        # 其他租户不可见
        self.assertIsNone(job_store.get("other", "job-1", db=self.db))

    def test_mark_stale(self):
        job_store.create("job-old", "acme", "600337.SH", db=self.db)
        c = job_store._conn(self.db)
        c.execute("UPDATE jobs SET created=? WHERE job_id=?", (1.0, "job-old"))
        c.commit()
        n = job_store.mark_stale(db=self.db)
        self.assertGreaterEqual(n, 1)
        j = job_store.get("acme", "job-old", db=self.db)
        self.assertEqual(j["status"], "failed")
        self.assertIn("超时", j["error"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
