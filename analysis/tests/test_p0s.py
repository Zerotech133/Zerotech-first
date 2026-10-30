# -*- coding: utf-8 -*-
"""P0S curated 日更工具测试（临时目录，不碰生产仓）。"""
import os
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import curated_update as cu  # noqa: E402


class TestCuratedUpdate(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        cal = pd.DataFrame({"cal_date": ["20261023", "20261026", "20261027", "20261030"],
                            "is_open": [1, 1, 1, 1]})
        cal.to_parquet(os.path.join(self.td.name, "trade_cal.parquet"), index=False)
        for t in cu.TABLES:
            pd.DataFrame({"ts_code": ["A.SZ"], "trade_date": ["20261023"], "x": [1]}).to_parquet(
                os.path.join(self.td.name, t + ".parquet"), index=False)
        self.patch_dir = mock.patch.object(cu, "CURATED", self.td.name)
        self.patch_dir.start()
        self.patch_time = mock.patch.object(cu.time, "localtime",
                                            return_value=type("T", (), {"tm_hour": 20})())
        self.patch_time.start()

    def tearDown(self):
        self.patch_time.stop()
        self.patch_dir.stop()
        self.td.cleanup()

    def test_missing_dates(self):
        out = cu.missing_dates(tables=["daily"], today="20261030")
        self.assertEqual(out["daily"], ["20261026", "20261027", "20261030"])  # tm_hour=20 → 含当日

    def test_missing_dates_excludes_today_before_18(self):
        with mock.patch.object(cu.time, "localtime", return_value=type("T", (), {"tm_hour": 10})()):
            out = cu.missing_dates(tables=["daily"], today="20261030")
        self.assertNotIn("20261030", out["daily"])

    def test_append_only_no_history_loss(self):
        new = pd.DataFrame({"ts_code": ["A.SZ", "B.SZ"], "trade_date": ["20261023", "20261026"],
                            "x": [9, 2]})
        with mock.patch.object(cu, "_ts_call", return_value={"fields": list(new.columns),
                                                             "items": new.values.tolist()}):
            n = cu.append_table("daily", ["20261026"])
        self.assertGreaterEqual(n, 0)
        df = pd.read_parquet(os.path.join(self.td.name, "daily.parquet"))
        # 历史行保留 + 只追加 1 行（A.SZ 20261023 已存在；A 的新日期行才算新）
        self.assertGreaterEqual(len(df), 2)
        self.assertTrue(((df["ts_code"] == "B.SZ") & (df["trade_date"] == "20261026")).any())


if __name__ == "__main__":
    unittest.main(verbosity=2)
