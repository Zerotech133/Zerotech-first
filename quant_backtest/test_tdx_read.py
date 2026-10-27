# -*- coding: utf-8 -*-
"""tdx_read 读取层单元测试（零真实 IO，临时目录）。"""

import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tdx_read as R


def _mk_parts(root, tbl, frames):
    d = os.path.join(root, tbl)
    os.makedirs(d, exist_ok=True)
    for i, f in enumerate(frames):
        f.to_parquet(os.path.join(d, "part_%04d.parquet" % i), index=False)


class TestTdxRead(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = (R.OUT, R.INFO_DB, R.MANIFEST)
        R.OUT = self.tmp
        R.INFO_DB = os.path.join(self.tmp, "tdx_info.db")

    def tearDown(self):
        R.OUT, R.INFO_DB, R.MANIFEST = self._old

    def test_norm_day_variants(self):
        self.assertEqual(R._norm_day("2026-09-26"), "20260926")
        self.assertEqual(R._norm_day("20260926"), "20260926")
        self.assertEqual(R._norm_day(date(2026, 9, 26)), "20260926")
        self.assertEqual(R._norm_day(datetime(2026, 9, 26, 15, 0)), "20260926")
        self.assertIsNone(R._norm_day(None))

    def test_read_day_filters_and_codes(self):
        df1 = pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ", "300750.SZ"],
                "trade_date": ["20260925", "20260925", "20260925"],
                "pe": [30.1, 8.2, 45.0],
            }
        )
        df2 = pd.DataFrame(
            {"ts_code": ["600519.SH"], "trade_date": ["20260926"], "pe": [29.8]}
        )
        _mk_parts(self.tmp, "ts_daily_basic", [df1, df2])
        out = R.read_day("ts_daily_basic", "2026-09-25")
        self.assertEqual(len(out), 3)
        out = R.read_day("ts_daily_basic", "20260926")
        self.assertEqual(len(out), 1)
        self.assertEqual(out.iloc[0]["pe"], 29.8)
        out = R.read_day("ts_daily_basic", "20260925", codes=["600519.SH", "300750.SZ"])
        self.assertEqual(sorted(out["ts_code"]), ["300750.SZ", "600519.SH"])
        out = R.read_day("ts_daily_basic", "20260925", codes="000001")
        self.assertEqual(list(out["ts_code"]), ["000001.SZ"])

    def test_read_day_columns_and_empty(self):
        df = pd.DataFrame(
            {
                "ts_code": ["600519.SH"],
                "trade_date": ["20260925"],
                "pe": [30.1],
                "total_mv": [2.1e6],
            }
        )
        _mk_parts(self.tmp, "ts_daily_basic", [df])
        out = R.read_day("ts_daily_basic", "20260925", columns=["pe"])
        self.assertIn("pe", out.columns)
        self.assertNotIn("total_mv", out.columns)
        self.assertTrue(R.read_day("ts_daily_basic", "20200101").empty)
        self.assertTrue(R.read_day("ts_no_such", "20260925").empty)

    def test_read_range_inclusive(self):
        df = pd.DataFrame(
            {
                "ts_code": ["600519.SH"] * 4,
                "trade_date": ["20260922", "20260923", "20260924", "20260925"],
                "rzye": [1.0, 2.0, 3.0, 4.0],
            }
        )
        _mk_parts(self.tmp, "ts_margin_detail", [df])
        out = R.read_range("ts_margin_detail", "20260923", "2026-09-25")
        self.assertEqual(len(out), 3)
        self.assertEqual(out.iloc[-1]["rzye"], 4.0)
        out = R.read_range("ts_margin_detail", start="20260924")
        self.assertEqual(len(out), 2)

    def test_fin_table_uses_end_date(self):
        df = pd.DataFrame(
            {
                "ts_code": ["600519.SH", "600519.SH"],
                "end_date": ["20251231", "20260630"],
                "eps": [68.0, 35.2],
            }
        )
        _mk_parts(self.tmp, "ts_fina_indicator", [df])
        out = R.read_day("ts_fina_indicator", "20260630")
        self.assertEqual(len(out), 1)
        self.assertEqual(R.date_col_of("ts_fina_indicator"), "end_date")
        self.assertEqual(R.date_col_of("ts_moneyflow"), "trade_date")

    def test_day_map_shape(self):
        df = pd.DataFrame(
            {
                "ts_code": ["600519.SH", "000001.SZ"],
                "trade_date": ["20260925", "20260925"],
                "pe": [30.1, 8.2],
                "total_mv": [2.1e6, 2.3e5],
            }
        )
        _mk_parts(self.tmp, "ts_daily_basic", [df])
        m = R.day_map("ts_daily_basic", "20260925", ["pe", "total_mv"])
        self.assertEqual(set(m), {"600519.SH", "000001.SZ"})
        self.assertEqual(m["600519.SH"]["pe"], 30.1)
        self.assertTrue(R.day_map("ts_daily_basic", "20200101", ["pe"]) == {})

    def test_read_all_mapping_table(self):
        df = pd.DataFrame(
            {
                "ts_code": ["881101.TI", "881102.TI"],
                "con_code": ["600519.SH", "000001.SZ"],
                "con_name": ["贵州茅台", "平安银行"],
            }
        )
        _mk_parts(self.tmp, "ts_ths_member", [df])
        out = R.read_all("ts_ths_member", codes=["600519"])
        self.assertEqual(list(out["con_name"]), ["贵州茅台"])

    def test_table_ready_and_fresh(self):
        df = pd.DataFrame(
            {"ts_code": ["600519.SH"], "trade_date": ["20260926"], "pe": [30.1]}
        )
        _mk_parts(self.tmp, "ts_daily_basic", [df])
        with open(os.path.join(self.tmp, "_manifest.json"), "w") as f:
            pass  # 空 manifest：未 done
        R.MANIFEST = os.path.join(self.tmp, "_manifest.json")
        self.assertFalse(R.table_ready("ts_daily_basic"))
        self.assertFalse(R.fresh("ts_daily_basic", "20260926"))
        with open(R.MANIFEST, "w", encoding="utf-8") as f:
            f.write('{"ts_daily_basic": {"done": true}}')
        self.assertTrue(R.table_ready("ts_daily_basic"))
        self.assertTrue(R.fresh("ts_daily_basic", "20260926"))
        self.assertFalse(R.fresh("ts_daily_basic", "20260927"))

    def test_read_info_sqlite(self):
        conn = sqlite3.connect(R.INFO_DB)
        conn.execute(
            "CREATE TABLE stock_report (ts_code TEXT, pub_date TEXT,"
            " rating TEXT, org TEXT, eps_2026 TEXT)"
        )
        conn.executemany(
            "INSERT INTO stock_report VALUES (?,?,?,?,?)",
            [
                ("600519.SH", "2026-09-26", "买入", "东吴", "68.1"),
                ("600519.SH", "2026-09-20", "增持", "中信", "67.5"),
                ("000001.SZ", "2026-09-26", "持有", "国泰", "1.8"),
            ],
        )
        conn.commit()
        conn.close()
        rows = R.read_info("stock_report", codes="600519", limit=10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["pub_date"], "2026-09-26")  # 倒序
        rows = R.read_info("stock_report", codes=["600519.SH"], since="20260925")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["org"], "东吴")
        R.INFO_DB = os.path.join(self.tmp, "no_such.db")
        self.assertEqual(R.read_info("stock_report"), [])
        with self.assertRaises(ValueError):
            R.read_info("ts_daily_basic")
        with self.assertRaises(ValueError):
            R.read_day("stock_report", "20260926")


if __name__ == "__main__":
    unittest.main()
