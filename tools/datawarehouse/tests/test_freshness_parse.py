# -*- coding: utf-8 -*-
"""freshness_report._parse_day 单测（2026-10-28 BUG 修复回归）。"""
from __future__ import annotations

import sys
import unittest
from datetime import date, datetime
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOOLS))
import freshness_report as m  # noqa: E402


class TestParseDay(unittest.TestCase):
    def test_iso_date(self):
        self.assertEqual(m._parse_day("2026-10-26"), date(2026, 10, 26))

    def test_iso_datetime(self):
        self.assertEqual(m._parse_day("2026-10-26T00:30:00"), date(2026, 10, 26))

    def test_compact(self):
        self.assertEqual(m._parse_day("20261026"), date(2026, 10, 26))

    def test_slash(self):
        self.assertEqual(m._parse_day("2026/10/26"), date(2026, 10, 26))

    def test_date_object(self):
        self.assertEqual(m._parse_day(date(2026, 10, 26)), date(2026, 10, 26))
        self.assertEqual(m._parse_day(datetime(2026, 10, 26, 1, 2, 3)), date(2026, 10, 26))

    def test_epoch_seconds(self):
        self.assertEqual(m._parse_day(1791504000), date(2026, 10, 9))

    def test_epoch_millis(self):
        self.assertEqual(m._parse_day(1791504000000), date(2026, 10, 9))

    def test_none_and_garbage(self):
        self.assertIsNone(m._parse_day(None))
        self.assertIsNone(m._parse_day(""))
        self.assertIsNone(m._parse_day("not-a-date"))


if __name__ == "__main__":
    unittest.main()
