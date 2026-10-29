# -*- coding: utf-8 -*-
"""代码审查补测：盈余方向解析 / 分位排名 / 边界。"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import research.event_study2 as es  # noqa: E402
import research.flow_divergence as fd  # noqa: E402


class TestEarnDirection(unittest.TestCase):
    def test_pos(self):
        self.assertEqual(es.earn_direction("2025年半年度业绩预增公告"), "pos")
        self.assertEqual(es.earn_direction("业绩预告：预计扭亏为盈"), "pos")

    def test_neg(self):
        self.assertEqual(es.earn_direction("2025年半年度业绩预亏公告"), "neg")
        self.assertEqual(es.earn_direction("业绩预告：预减"), "neg")

    def test_actual(self):
        self.assertEqual(es.earn_direction("2025年半年度业绩快报公告"), "actual")

    def test_unknown_and_non_earnings(self):
        self.assertEqual(es.earn_direction("2025年半年度业绩预告"), "unknown")
        self.assertEqual(es.earn_direction("关于董事会决议的公告"), "")


class TestRanks(unittest.TestCase):
    def test_ranks_monotonic(self):
        r = fd._ranks({"a": 1.0, "b": 2.0, "c": 3.0})
        self.assertEqual(set(r), {"a", "b", "c"})
        self.assertLess(r["a"], r["b"])
        self.assertLess(r["b"], r["c"])
        self.assertEqual(r["a"], 0.0)
        self.assertEqual(r["c"], 1.0)

    def test_ranks_single(self):
        self.assertEqual(fd._ranks({"a": 1.0}), {})


if __name__ == "__main__":
    unittest.main()
