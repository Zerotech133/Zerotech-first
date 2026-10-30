# -*- coding: utf-8 -*-
"""P3 增长事件 测试。"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from commercial import audit, growth  # noqa: E402


class TestGrowth(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.p = os.path.join(self.td.name, "audit.jsonl")
        self._orig = audit.AUDIT_FILE
        audit.AUDIT_FILE = self.p

    def tearDown(self):
        audit.AUDIT_FILE = self._orig
        self.td.cleanup()

    def test_record_requires_known_event(self):
        with self.assertRaises(ValueError):
            growth.record("unknown")
        growth.record("view", ref="landing")
        rows = audit.tail(10, path=self.p)
        self.assertEqual(rows[-1]["action"], "growth_view")

    def test_report_conversion(self):
        growth.record("view", ref="landing")
        growth.record("view", ref="landing")
        audit.append("trial_started", tenant="trial-a", path=self.p)
        audit.append("trial_started", tenant="trial-b", path=self.p)
        audit.append("report_generated", tenant="trial-a", path=self.p)
        audit.append("invoice_generated", tenant="trial-a", path=self.p)
        r = growth.report(path=self.p)
        self.assertEqual(r["view_total"], 2)
        self.assertEqual(r["trials"], 2)
        self.assertEqual(r["trial_active"], 1)
        self.assertEqual(r["trial_paid"], 1)
        self.assertEqual(r["trial_to_paid"], 50.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
