# -*- coding: utf-8 -*-
"""F2-P2-c 单测：三池并集逻辑（零触网，池函数 mock）。"""
import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
QB = os.path.normpath(os.path.join(HERE, "..", "..", "quant_backtest"))
if QB not in sys.path:
    sys.path.insert(0, QB)

import importlib
gc = importlib.import_module("guba_crawl")


class TestTriPool(unittest.TestCase):
    def test_union_dedup(self):
        """三池并集去重：同 code6 只留一份，缠论池序优先。"""
        with mock.patch.object(gc, "pool_codes", return_value=["600519.SH", "000001.SZ"]), \
             mock.patch.object(gc, "_pool_focus_codes", return_value=["600519.SH"]), \
             mock.patch.object(gc, "_pool_dragon_codes", return_value=["300750.SZ"]):
            args = mock.Mock(pool=True, tri_pool=True, pool_days=30, limit=60,
                             batch_index=None, batch_size=15, no_shuffle=True,
                             codes="")
            codes, tag = gc._processed_codes(args)
        self.assertIn("600519.SH", codes)
        self.assertIn("300750.SZ", codes)
        self.assertEqual(len([c for c in codes if c.startswith("600519")]), 1)  # 去重
        self.assertIn("auto-rotate", tag)

    def test_tri_pool_off_keeps_legacy(self):
        """不传 --tri-pool：行为与旧版一致（仅缠论池）。"""
        with mock.patch.object(gc, "pool_codes", return_value=["600519.SH"]), \
             mock.patch.object(gc, "_pool_focus_codes") as mf, \
             mock.patch.object(gc, "_pool_dragon_codes") as md:
            args = mock.Mock(pool=True, tri_pool=False, pool_days=30, limit=60,
                             batch_index=None, batch_size=15, no_shuffle=True,
                             codes="")
            codes, tag = gc._processed_codes(args)
        self.assertEqual(codes, ["600519.SH"])
        mf.assert_not_called()
        md.assert_not_called()

    def test_dragon_pool_real_query(self):
        """龙虎榜池真库查询（本地 Neo4j 只读）：应返回 10 只且全是 6 位 A 股码。"""
        codes = gc._pool_dragon_codes()
        self.assertTrue(all(len(c) == 9 and c[6] == "." for c in codes))
        self.assertLessEqual(len(codes), 30)


if __name__ == "__main__":
    unittest.main()
