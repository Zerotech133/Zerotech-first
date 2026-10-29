# -*- coding: utf-8 -*-
"""freshness 期望模型（freq-aware）单测。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import freshness_report as m  # noqa: E402


class TestExpectedLag(unittest.TestCase):
    def test_daily(self):
        self.assertEqual(m.expected_lag("daily", {"freq": "D"}), 2)

    def test_weekly_monthly_quarterly_annual(self):
        self.assertEqual(m.expected_lag("x1", {"freq": "W"}), 10)
        self.assertEqual(m.expected_lag("x2", {"freq": "M"}), 45)
        self.assertEqual(m.expected_lag("x3", {"freq": "Q"}), 120)
        self.assertEqual(m.expected_lag("x4", {"freq": "A"}), 400)

    def test_override_margin(self):
        self.assertEqual(m.expected_lag("margin_detail", {"freq": "D"}), 4)

    def test_skipped_planned(self):
        self.assertIsNone(m.expected_lag("xq_comment", {"freq": "D"}))

    def test_skipped_deprecated(self):
        self.assertIsNone(m.expected_lag("quarantine_prod_overwrite", {"freq": "D"}))

    def test_skipped_static(self):
        self.assertIsNone(m.expected_lag("anns_d", {"freq": "A", "static_no_date": True}))
        self.assertIsNone(m.expected_lag("x_static", {"freq": "A", "deprecated": True}))

    def test_unknown_freq_defaults(self):
        self.assertEqual(m.expected_lag("x_unknown", {"freq": "?"}), m.EXPECTED_LAG_DAYS)


if __name__ == "__main__":
    unittest.main()
