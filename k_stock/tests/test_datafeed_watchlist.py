# -*- coding: utf-8 -*-
"""watchlist_from_quotes 代码归一化回归（2026-10-15 BUG 审计修复）。

背景：quotes 表 code 为小写前缀式（sz300750/bj920028/gb_be），湖文件为
点分下划线式（300750_SZ.parquet）。格式错位致 exists() 恒 False、
watchlist 恒空、07:05 主 PoC 聚合连续空跑（10-12/13/15 三日实证）。
"""
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from k_stock.kstock import datafeed as D


class TestLakeCode(unittest.TestCase):
    def test_sh_sz_bj_prefix_normalized(self):
        self.assertEqual(D._lake_code("sz300750"), "300750.SZ")
        self.assertEqual(D._lake_code("SH600519"), "600519.SH")
        self.assertEqual(D._lake_code("bj920028"), "920028.BJ")

    def test_dotted_passthrough_and_garbage(self):
        self.assertEqual(D._lake_code("600519.SH"), "600519.SH")   # 已点分式
        self.assertIsNone(D._lake_code("gb_be"))                   # 美股跳过
        self.assertIsNone(D._lake_code("gb_mrvl"))
        self.assertIsNone(D._lake_code(""))
        self.assertIsNone(D._lake_code(None))
        self.assertIsNone(D._lake_code("sz12345"))                 # 非 6 位数字
        self.assertIsNone(D._lake_code("hk00700"))                 # 未支持市场

    def test_mapping_hits_real_lake_files(self):
        for code in ("sz300750", "sh600519", "bj920028"):
            lake = D._lake_code(code)
            assert lake is not None
            self.assertTrue(D._code_to_file(lake).exists(),
                            "湖缺文件: %s -> %s" % (code, lake))


class TestWatchlistFromQuotes(unittest.TestCase):
    def test_prefix_codes_resurface_and_gb_skipped(self):
        rows = [("gb_be", 9e9), ("gb_mrvl", 8e9),           # 美股霸榜（应跳过）
                ("sz300750", 8.3e9), ("sh600519", 4.8e9),
                ("bj920028", 1.0e9)]
        with mock.patch.object(D.sqlite3, "connect") as mc:
            mc.return_value.__enter__ = mock.Mock()
            con = mc.return_value
            con.execute.return_value.fetchall.return_value = rows
            con.close = mock.Mock()
            out = D.watchlist_from_quotes(10)
        self.assertEqual(out, ["300750.SZ", "600519.SH", "920028.BJ"])

    def test_all_unmappable_returns_empty(self):
        rows = [("gb_%d" % i, 1e9 - i) for i in range(20)]
        with mock.patch.object(D.sqlite3, "connect") as mc:
            mc.return_value.__enter__ = mock.Mock()
            con = mc.return_value
            con.execute.return_value.fetchall.return_value = rows
            con.close = mock.Mock()
            self.assertEqual(D.watchlist_from_quotes(10), [])

    def test_real_db_smoke_nonempty(self):
        # 真库冒烟：修复后 watchlist 必须非空（quotes 有 10-14 盘后数据）
        if not D.NEWS_DB.exists():
            self.skipTest("news.db 不在本机")
        out = D.watchlist_from_quotes(10)
        self.assertGreater(len(out), 0, "真库 watchlist 仍为空——归一化未生效")
        for code in out:
            self.assertRegex(code, r"^\d{6}\.(SH|SZ|BJ)$")


if __name__ == "__main__":
    unittest.main()
