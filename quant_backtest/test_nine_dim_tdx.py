# -*- coding: utf-8 -*-
"""nine_dim TDX 本地接线单元测试（零真实 IO，零 API 调用）。"""
import os
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tdx_read as R
import nine_dim as nd

TDAY = "20260108"
PREV = "20260107"


def _mk(root, tbl, frames, done=True):
    d = os.path.join(root, tbl)
    os.makedirs(d, exist_ok=True)
    for i, f in enumerate(frames):
        f.to_parquet(os.path.join(d, "part_%04d.parquet" % i), index=False)
    if done:
        R.MANIFEST = os.path.join(root, "_manifest.json")
        import json
        m = {}
        if os.path.exists(R.MANIFEST):
            m = json.load(open(R.MANIFEST, encoding="utf-8"))
        m[tbl] = {"done": True}
        with open(R.MANIFEST, "w", encoding="utf-8") as fh:
            json.dump(m, fh)


class TestNineDimTdx(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._old = (R.OUT, R.INFO_DB, R.MANIFEST,
                     nd.TDX_LOCAL, nd.tdxr)
        R.OUT = self.tmp
        R.INFO_DB = os.path.join(self.tmp, "tdx_info.db")
        R.MANIFEST = os.path.join(self.tmp, "_manifest.json")
        nd.TDX_LOCAL = True
        nd.tdxr = R
        self.calls = {"ts": [], "paged": [], "bytd": []}
        self._orig = (nd._ts, nd._paged, nd._by_trade_date)

        def _no_ts(api, **kw):
            self.calls["ts"].append(api)
            if api == "block_trade":
                raise RuntimeError("no api")   # 允许失败
            raise AssertionError("API 不应被调用: %s" % api)

        def _no_paged(api, **kw):
            self.calls["paged"].append(api)
            raise AssertionError("paged 不应被调用: %s" % api)

        def _no_bytd(api, tday, fields):
            self.calls["bytd"].append(api)
            return {}, False

        nd._ts, nd._paged, nd._by_trade_date = _no_ts, _no_paged, _no_bytd

    def tearDown(self):
        nd._ts, nd._paged, nd._by_trade_date = self._orig
        R.OUT, R.INFO_DB, R.MANIFEST, nd.TDX_LOCAL, nd.tdxr = self._old

    def test_tdx_day_gate_not_ready(self):
        self.assertIsNone(nd._tdx_day("ts_top_list", TDAY, ["net_amount"]))

    def test_flow_local_full_hit(self):
        _mk(self.tmp, "ts_top_list", [pd.DataFrame({
            "ts_code": ["600519.SH"], "trade_date": [TDAY],
            "net_amount": [12000.0]})])
        _mk(self.tmp, "ts_top_inst", [pd.DataFrame({
            "ts_code": ["600519.SH", "600519.SH", "000001.SZ"],
            "trade_date": [TDAY, TDAY, TDAY],
            "buy": [8000.0, 3000.0, float("nan")],
            "sell": [1000.0, float("nan"), 500.0]})])
        _mk(self.tmp, "ts_daily_basic", [pd.DataFrame({
            "ts_code": ["600519.SH", "000001.SZ"],
            "trade_date": [TDAY, TDAY],
            "pe": [30.5, float("nan")],
            "total_mv": [2.1e6, 2.3e5],
            "circ_mv": [2.0e6, 2.2e5]})])
        _mk(self.tmp, "ts_moneyflow", [pd.DataFrame({
            "ts_code": ["600519.SH"], "trade_date": [TDAY],
            "buy_lg_amount": [5000.0], "sell_lg_amount": [1000.0],
            "buy_elg_amount": [2000.0], "sell_elg_amount": [500.0]})])
        flow = nd._fetch_flow(TDAY)
        self.assertEqual(flow["basic"], {})  # basic 恒走 API（记录器空返回）
        # 本地命中：top/moneyflow/top_inst 不碰 API；basic 恒走 API
        # （TDX ts_daily_basic 'pe'=动态市盈率≠tushare pe_ttm，
        # 2026-09-27 单日窗口 parity 验证后按语义不接——非快照问题）
        self.assertEqual(self.calls["bytd"], ["daily_basic"])
        self.assertEqual([a for a in self.calls["ts"]
                          if a != "block_trade"], [])
        self.assertTrue(flow["top_ok"])
        self.assertEqual(flow["top"]["600519.SH"]["net_amount"], 12000.0)
        self.assertEqual(flow["inst_net"]["600519.SH"], 10000.0)
        self.assertEqual(flow["inst_net"]["000001.SZ"], -500.0)
        # moneyflow 净买 = (5000+2000)-(1000+500)
        self.assertEqual(flow["main_net"]["600519.SH"], 5500.0)
        self.assertFalse(flow["block_ok"])     # block_trade API 失败兜底

    def test_flow_fallback_when_not_ready(self):
        nd._by_trade_date = lambda api, tday, fields: ({}, False)
        flow = nd._fetch_flow(TDAY)
        self.assertEqual(flow["basic_ok"], False)
        self.assertEqual(flow["main_net"], {})
        self.assertIsNone(flow["inst_net"])
        self.assertFalse(flow["block_ok"])

    def test_flow_forced_api(self):
        nd.TDX_LOCAL = False
        _mk(self.tmp, "ts_top_list", [pd.DataFrame({
            "ts_code": ["600519.SH"], "trade_date": [TDAY],
            "net_amount": [12000.0]})])
        flow = nd._fetch_flow(TDAY)
        # moneyflow 四字段空结果后双字段重试，是原 API 语义（两次调用）
        self.assertEqual(self.calls["bytd"],
                         ["daily_basic", "moneyflow", "moneyflow",
                          "top_list"])
        self.assertEqual(flow["top"], {})

    def test_flow_uses_snapshot_block_trade_without_api(self):
        class SnapshotStore:
            def iter_records(self):
                return iter([{
                    "request_params": {"trade_date": TDAY},
                    "source_code": 0,
                    "err": 0,
                    "fields": ["ts_code", "trade_date", "price"],
                    "rows": [["600519.SH", TDAY, 100.0]],
                }])

        flow = nd._fetch_flow(TDAY, source_snapshot=SnapshotStore())

        self.assertEqual(flow["block"]["600519.SH"], [100.0])
        self.assertTrue(flow["block_ok"])
        self.assertNotIn("block_trade", self.calls["ts"])

    def test_flow_filters_date_range_snapshot_block_trade(self):
        class SnapshotStore:
            def iter_records(self):
                return iter([{
                    "request_params": {"start_date": PREV,
                                        "end_date": TDAY},
                    "source_code": 0,
                    "err": 0,
                    "fields": ["ts_code", "trade_date", "price"],
                    "rows": [["600519.SH", PREV, 99.0],
                             ["600519.SH", TDAY, 100.0]],
                }])

        flow = nd._fetch_flow(TDAY, source_snapshot=SnapshotStore())

        self.assertEqual(flow["block"]["600519.SH"], [100.0])
        self.assertTrue(flow["block_ok"])
        self.assertNotIn("block_trade", self.calls["ts"])

    def test_block_snapshot_normalizes_iso_dates(self):
        class SnapshotStore:
            def iter_records(self):
                return iter([{
                    "request_params": {"trade_date": "2026-01-08"},
                    "source_code": 0,
                    "err": 0,
                    "fields": ["ts_code", "trade_date", "price"],
                    "rows": [["600519.SH", "2026-01-08", 100.0]],
                }])

        block, ok = nd._block_from_snapshot(TDAY, SnapshotStore())

        self.assertEqual(block["600519.SH"], [100.0])
        self.assertTrue(ok)

    def test_block_snapshot_missing_receipt_fails_closed(self):
        class SnapshotStore:
            def iter_records(self):
                return iter([{
                    "request_params": {},
                    "source_code": None,
                    "err": 1,
                    "fields": [],
                    "rows": [],
                }])

        block, ok = nd._block_from_snapshot(TDAY, SnapshotStore())

        self.assertEqual(block, {})
        self.assertFalse(ok)

    def test_block_snapshot_malformed_row_fails_closed(self):
        class SnapshotStore:
            def iter_records(self):
                return iter([{
                    "request_params": {"trade_date": TDAY},
                    "source_code": 0,
                    "err": 0,
                    "fields": ["ts_code", "trade_date", "price"],
                    "rows": [["600519.SH", TDAY]],
                }])

        block, ok = nd._block_from_snapshot(TDAY, SnapshotStore())

        self.assertEqual(block, {})
        self.assertFalse(ok)

    def test_margin_local_detail(self):
        _mk(self.tmp, "ts_margin_detail", [pd.DataFrame({
            "ts_code": ["600519.SH", "000001.SZ"],
            "trade_date": [TDAY, PREV],
            "rzye": [1.5e6, 1.4e6]})])
        out = nd._fetch_margin(TDAY, PREV)
        self.assertEqual(out["mode"], "detail")
        self.assertEqual(self.calls["paged"], [])
        self.assertEqual(out["detail"]["600519.SH"]["cur"], 1.5e6)
        self.assertEqual(out["detail"]["000001.SZ"]["prev"], 1.4e6)

    def test_margin_nan_coerced_zero(self):
        _mk(self.tmp, "ts_margin_detail", [pd.DataFrame({
            "ts_code": ["600519.SH", "600519.SH"],
            "trade_date": [TDAY, PREV],
            "rzye": [1.5e6, float("nan")]})])
        out = nd._fetch_margin(TDAY, PREV)
        self.assertEqual(out["detail"]["600519.SH"]["prev"], 0)

    def test_margin_fallback_prev_missing(self):
        _mk(self.tmp, "ts_margin_detail", [pd.DataFrame({
            "ts_code": ["600519.SH"], "trade_date": [TDAY],
            "rzye": [1.5e6]})])
        out = nd._fetch_margin(TDAY, PREV)   # prev 不在本地 → API
        self.assertEqual(self.calls["ts"],
                         ["margin_detail", "margin"])
        self.assertEqual(out["mode"], None)

    def test_nan2none(self):
        d = {"a": {"x": 1.0, "y": float("nan")},
             "b": {"x": None}}
        out = nd._nan2none(d)
        self.assertEqual(out["a"]["x"], 1.0)
        self.assertIsNone(out["a"]["y"])
        self.assertIsNone(out["b"]["x"])


if __name__ == "__main__":
    unittest.main()
