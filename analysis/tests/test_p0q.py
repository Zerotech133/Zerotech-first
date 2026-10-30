# -*- coding: utf-8 -*-
"""P0Q：申万行业权威映射（tushare 缓存）单元测试。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import industry  # noqa: E402


class TestIndustryCache(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        mem = pd.DataFrame([
            {"ts_code": "300308.SZ", "l1_code": "801770.SI", "l1_name": "通信",
             "l2_code": "801102.SI", "l2_name": "通信设备",
             "l3_code": "851024.SI", "l3_name": "通信网络设备及器件", "is_new": "Y"},
            {"ts_code": "300502.SZ", "l1_code": "801770.SI", "l1_name": "通信",
             "l2_code": "801102.SI", "l2_name": "通信设备",
             "l3_code": "851024.SI", "l3_name": "通信网络设备及器件", "is_new": "Y"},
            {"ts_code": "000001.SZ", "l1_code": "801780.SI", "l1_name": "银行",
             "l2_code": "801782.SI", "l2_name": "股份制银行Ⅱ", "l3_code": "", "l3_name": "", "is_new": "Y"},
        ])
        cls = pd.DataFrame([
            {"index_code": "801770.SI", "industry_name": "通信", "level": "L1"},
            {"index_code": "801102.SI", "industry_name": "通信设备", "level": "L2"},
        ])
        mem.to_parquet(os.path.join(self.td.name, "member.parquet"), index=False)
        cls.to_parquet(os.path.join(self.td.name, "classify.parquet"), index=False)
        self.patch = mock.patch.object(industry, "DIR", self.td.name)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.td.cleanup()

    def test_map_stock(self):
        m = industry.map_stock("300308.SZ")
        self.assertEqual(m["l1_name"], "通信")
        self.assertEqual(m["l2_name"], "通信设备")
        self.assertEqual(m["match"], "tushare_sw")
        self.assertEqual(industry.map_stock("999999.SZ"), {})

    def test_l2_peers(self):
        peers = industry.l2_peers("801102.SI", "300308.SZ")
        self.assertEqual(peers, ["300502.SZ"])
        self.assertEqual(industry.l2_peers("", "300308.SZ"), [])

    def test_check_coverage(self):
        r = industry.check(codes=["300308.SZ", "000001.SZ", "999999.SZ"])
        self.assertEqual(r["coverage"], round(2 / 3 * 100, 1))
        self.assertEqual(r["total"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
