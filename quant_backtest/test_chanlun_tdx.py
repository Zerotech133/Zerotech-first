# -*- coding: utf-8 -*-
"""chanlun TDX 兜底接线单元测试（零真实 IO，零 API 调用）。"""
import os
import sys
import types
import unittest

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import chanlun_scan as cs
import chanlun_data as cd


def _bar(dt, c, pc=None):
    return {"dt": dt, "o": c * 0.98, "h": c * 1.02, "l": c * 0.97,
            "c": c, "v": 1e5, "pc": pc}


class _FakeTdx(types.ModuleType):
    """tdx_read 替身：table_ready / day_map / read_range 可编程。"""

    def __init__(self, ready=True, day_maps=None, df=None, fail=False):
        super().__init__("tdx_read")
        self._ready = ready
        self._day_maps = day_maps or {}
        self._df = df
        self._fail = fail
        self.calls = []

    def table_ready(self, tbl):
        self.calls.append(("ready", tbl))
        return self._ready

    def day_map(self, tbl, day, fields=None, codes=None):
        self.calls.append(("day_map", tbl, day))
        if self._fail:
            raise RuntimeError("boom")
        return self._day_maps.get(day, {})

    def read_range(self, tbl, start=None, end=None, codes=None,
                   columns=None):
        self.calls.append(("read_range", tbl, tuple(codes or ())))
        if self._fail:
            raise RuntimeError("boom")
        return self._df if self._df is not None else pd.DataFrame()


class TestTdxTopup(unittest.TestCase):
    def test_topup_appends_new_day(self):
        bars = [_bar("2026-09-23", 10.0)]
        maps = {"20260924": {"600519.SH": {
            "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.5,
            "vol": 9e4, "pre_close": 10.0}}}
        out = cs._tdx_topup(bars, "600519.SH", maps)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[-1]["dt"], "2026-09-24")
        self.assertEqual(out[-1]["c"], 10.5)
        self.assertEqual(out[-1]["pc"], 10.0)

    def test_topup_dedup_and_drops_invalid(self):
        bars = [_bar("2026-09-23", 10.0), _bar("2026-09-24", 10.5)]
        nan = float("nan")
        maps = {
            "20260924": {"600519.SH": {          # 已有 → 去重不补
                "open": 10.2, "high": 10.8, "low": 10.1, "close": 10.5,
                "vol": 9e4, "pre_close": 10.0}},
            "20260925": {"600519.SH": {          # NaN → 剔除
                "open": nan, "high": 11.0, "low": 10.4, "close": 10.9,
                "vol": 1e5, "pre_close": None}},
            "20260926": {"600519.SH": {          # 停牌 vol=0 → 剔除
                "open": 11.0, "high": 11.2, "low": 10.9, "close": 11.0,
                "vol": 0.0, "pre_close": None}},
            "20260929": {"600519.SH": {          # 有效 → 补
                "open": 11.0, "high": 11.5, "low": 10.9, "close": 11.2,
                "vol": 8e4, "pre_close": nan}}}
        out = cs._tdx_topup(bars, "600519.SH", maps)
        self.assertEqual([b["dt"] for b in out],
                         ["2026-09-23", "2026-09-24", "2026-09-29"])
        self.assertIsNone(out[-1]["pc"])         # pre_close=NaN → None

    def test_topup_code_missing_or_maps_empty(self):
        bars = [_bar("2026-09-23", 10.0)]
        self.assertEqual(cs._tdx_topup(bars, "600519.SH", {}), bars)
        self.assertEqual(cs._tdx_topup(bars, "000001.SZ",
                                       {"20260924": {}}), bars)


class TestTdxRescue(unittest.TestCase):
    def setUp(self):
        self._old = sys.modules.get("tdx_read")

    def tearDown(self):
        if self._old is None:
            sys.modules.pop("tdx_read", None)
        else:
            sys.modules["tdx_read"] = self._old

    def _install(self, **kw):
        m = _FakeTdx(**kw)
        sys.modules["tdx_read"] = m
        return m

    def test_all_missing_days_covered(self):
        m = self._install(day_maps={
            "20260925": {"600519.SH": {"close": 10}},
            "20260926": {"600519.SH": {"close": 11}}})
        out = cs._tdx_rescue(["20260925", "20260926"])
        self.assertEqual(sorted(out), ["20260925", "20260926"])
        self.assertIn(("day_map", "ts_daily", "20260925"), m.calls)

    def test_partial_coverage_fails_closed(self):
        self._install(day_maps={"20260925": {"600519.SH": {"close": 10}}})
        self.assertEqual(cs._tdx_rescue(["20260925", "20260926"]), {})

    def test_table_not_ready_or_fail(self):
        self._install(ready=False)
        self.assertEqual(cs._tdx_rescue(["20260925"]), {})
        self._install(fail=True)
        self.assertEqual(cs._tdx_rescue(["20260925"]), {})


class TestTdxDailyFallback(unittest.TestCase):
    def setUp(self):
        self._old = sys.modules.get("tdx_read")

    def tearDown(self):
        if self._old is None:
            sys.modules.pop("tdx_read", None)
        else:
            sys.modules["tdx_read"] = self._old

    def _install(self, **kw):
        m = _FakeTdx(**kw)
        sys.modules["tdx_read"] = m
        return m

    def _df(self):
        return pd.DataFrame({
            "ts_code": ["600519.SH"] * 3,
            "trade_date": ["20260923", "20260924", "20260925"],
            "open": [10.0, 10.5, 10.8], "high": [10.6, 11.0, 11.2],
            "low": [9.9, 10.4, 10.7], "close": [10.5, 10.8, 11.0],
            "vol": [1e5, 1.1e5, 0.0]})

    def test_cn_bars_sorted_asc_and_suspended_dropped(self):
        m = self._install(df=self._df())
        out = cd._tdx_daily_fallback("CN", "600519.SH", 10)
        self.assertEqual([b["dt"] for b in out],
                         ["2026-09-23", "2026-09-24"])   # vol=0 剔除
        self.assertEqual(out[-1]["c"], 10.8)
        self.assertIn(("read_range", "ts_daily", ("600519.SH",)), m.calls)
        self.assertNotIn("pc", out[-1])                    # 与 _bar 形态一致

    def test_limit_tail_window(self):
        self._install(df=self._df())
        out = cd._tdx_daily_fallback("CN", "600519.SH", 1)
        self.assertEqual([b["dt"] for b in out], ["2026-09-24"])

    def test_hk_code_candidates(self):
        m = self._install(df=pd.DataFrame({
            "ts_code": ["00700.HK"], "trade_date": ["20260924"],
            "open": [300.0], "high": [305.0], "low": [298.0],
            "close": [302.0], "vol": [1e6]}))
        out = cd._tdx_daily_fallback("HK", "00700", 10)
        self.assertEqual(out[0]["c"], 302.0)
        self.assertIn("00700.HK", m.calls[0][2])
        self.assertIn("00700", m.calls[0][2])

    def test_attrs_missing_or_fail(self):
        sys.modules["tdx_read"] = types.ModuleType("tdx_bare")
        self.assertEqual(cd._tdx_daily_fallback("CN", "600519.SH", 10), [])
        self._install(df=None)
        self.assertEqual(cd._tdx_daily_fallback("CN", "600519.SH", 10), [])
        self._install(fail=True)
        self.assertEqual(cd._tdx_daily_fallback("CN", "600519.SH", 10), [])


class TestLoadKlineFallback(unittest.TestCase):
    def setUp(self):
        self._old = sys.modules.get("tdx_read")

    def tearDown(self):
        if self._old is None:
            sys.modules.pop("tdx_read", None)
        else:
            sys.modules["tdx_read"] = self._old

    def test_load_kline_uses_tdx_when_db_empty(self):
        sys.modules["tdx_read"] = _FakeTdx(df=pd.DataFrame({
            "ts_code": ["600519.SH"], "trade_date": ["20260925"],
            "open": [10.0], "high": [10.6], "low": [9.9],
            "close": [10.5], "vol": [1e5]}))
        orig = cd._db_query
        cd._db_query = lambda sql, params: []
        try:
            r = cd.load_kline("600519.SH", "1d", 100)
        finally:
            cd._db_query = orig
        self.assertTrue(r["ok"])
        self.assertEqual(r["source"], "TDX 日线兜底")
        self.assertEqual(r["bars"][-1]["dt"], "2026-09-25")


if __name__ == "__main__":
    unittest.main()
