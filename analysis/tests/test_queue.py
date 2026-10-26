# -*- coding: utf-8 -*-
"""队列冒烟测试（临时目录，不碰生产队列）。"""
import os
import sys
import tempfile
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import queue  # noqa: E402


class TestQueue(unittest.TestCase):
    def test_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            r = queue.submit("600337.SH", llm=False, base=td)
            self.assertTrue(r["ok"])
            self.assertEqual(queue.stats(base=td)["pending"], 1)
            job = queue.claim(base=td)
            self.assertIsNotNone(job)
            self.assertEqual(job["code"], "600337.SH")
            self.assertEqual(queue.stats(base=td)["running"], 1)
            queue.finish(job, {"ok": True, "rating": "观察"}, base=td)
            self.assertEqual(queue.stats(base=td)["running"], 0)
            self.assertEqual(queue.stats(base=td)["done"], 1)

    def test_bad_code(self):
        self.assertFalse(queue.submit("abc", base=tempfile.mkdtemp())["ok"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
