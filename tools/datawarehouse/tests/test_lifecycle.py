# -*- coding: utf-8 -*-
"""lifecycle 覆盖单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import lifecycle as lc  # noqa: E402


class TestLifecycle(unittest.TestCase):
    def test_planned_tables(self):
        for t in ("xq_comment", "xq_kline_d", "xq_capital_flow", "xq_northbound",
                  "xq_earningforecast", "xq_hot_deal", "xq_inner_trade"):
            self.assertEqual(lc.status_for(t), "planned", t)
            self.assertTrue(lc.skipped(t), t)
            self.assertTrue(lc.reason_for(t))

    def test_deprecated(self):
        self.assertEqual(lc.status_for("quarantine_prod_overwrite"), "deprecated")
        self.assertTrue(lc.skipped("quarantine_prod_overwrite"))

    def test_active_default(self):
        self.assertEqual(lc.status_for("daily"), "active")
        self.assertFalse(lc.skipped("daily"))
        self.assertEqual(lc.reason_for("daily"), "")

    def test_unknown_table(self):
        self.assertEqual(lc.status_for("no_such_table_xyz"), "active")

    def test_overrides_count(self):
        self.assertGreaterEqual(len(lc.all_overrides()), 8)


if __name__ == "__main__":
    unittest.main()
