# -*- coding: utf-8 -*-
"""[WO-QT-20261021-104] 代码审计修复：now 注入链 + 零扫描静默出口。

审计发现（2026-10-21 16:45）：
A. `render_default(now=NOW)` 的 now **没有传给 load_context** → `crypto_stats()`
   用真实 `datetime.now()`，而 regime 的 stale 用注入 NOW。同一页面两套时间
   基准：测试固定日期跑到别的日子时，币市行会因真实日期漂移突然变「⚠停更」，
   属**日期敏感测试缺陷族**（项目已有判例）。
B. `scan` 的 `if n_ok == 0 and files:` 在 `files` 为空时（数据目录空 / active
   清单把标的全裁掉）**一个字都不打** → 静默空转，与「今日无信号」无法区分。

纪律：注入合成数据，不读写真实池；B 用临时目录造 parquet。
运行：仓根 pytest quant_backtest/tests/test_audit_fixes_wo104.py -v
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import chanlun_scan as cs                                # noqa: E402
import quant_entry_page as qep                           # noqa: E402

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 21, 14, 30, 0, tzinfo=CST)


class TestNowPropagation(unittest.TestCase):
    """A：render_default 的 now 必须一路传到 crypto_stats。"""

    def setUp(self):
        self.captured = {}
        self.orig = qep.crypto_stats

    def tearDown(self):
        qep.crypto_stats = self.orig

    def _spy(self):
        def spy(now=None):
            self.captured["now"] = now
            return {"n": 1, "btc_date": "20261021", "stale": False}
        qep.crypto_stats = spy

    def test_render_default_passes_now(self):
        self._spy()
        qep.render_default(now=NOW)
        self.assertIsNotNone(self.captured.get("now"),
                             "now 未透传：币市会退回真实时间")
        self.assertEqual(self.captured["now"], NOW)

    def test_load_context_passes_now(self):
        self._spy()
        qep.load_context(now=NOW)
        self.assertEqual(self.captured["now"], NOW)

    def test_load_context_without_now_still_works(self):
        """不传 now → crypto_stats 收到 None，内部自行取当刻（portal 正常路径）。"""
        self._spy()
        qep.load_context()
        self.assertIsNone(self.captured.get("now"))

    def test_two_time_bases_no_longer_diverge(self):
        """回归本质：同一 NOW 下，币市 stale 判定必须由 NOW 决定。

        构造「末根=20260901」的数据：若 now 生效 → stale；
        若退回真实时间（测试环境通常是 2026-10 之后）也 stale，
        故再用「末根=NOW 当天」反证：now 生效时必须不 stale。
        """
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        old = qep.CRYPTO_DIR
        qep.CRYPTO_DIR = tmp.name
        self.addCleanup(lambda: setattr(qep, "CRYPTO_DIR", old))
        pd.DataFrame({"trade_date": ["20261021"], "close": [1.0]}).to_parquet(
            os.path.join(tmp.name, "BTC_USDT.parquet"), index=False)
        s = qep.crypto_stats(now=NOW)
        self.assertFalse(s["stale"])
        self.assertEqual(s["btc_date"], "20261021")


class TestZeroScanReport(unittest.TestCase):
    """B：n_ok==0 时三种情形必须可区分，不得静默。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = cs.ASHARE
        cs.ASHARE = self.tmp.name
        self.old_mkt = cs._CUR_MKT
        self.addCleanup(self._restore)

    def _restore(self):
        cs.ASHARE = self.old
        cs._CUR_MKT = self.old_mkt

    def _out(self, files):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cs._report_zero_scan(files)
        return buf.getvalue()

    def test_empty_files_says_not_scanned(self):
        cs._CUR_MKT = "US"
        out = self._out([])
        self.assertIn("0 只标的可扫", out)
        self.assertIn("不是", out)
        self.assertIn("今日无信号", out)
        self.assertNotIn("DEPTH GAP", out, "空目录不应报深度缺口")

    def test_shallow_files_report_depth_gap(self):
        cs._CUR_MKT = "HK"
        idx = pd.date_range("2026-01-01", periods=50, freq="D")
        pd.DataFrame({"close": [1.0] * 50}, index=idx).to_parquet(
            os.path.join(self.tmp.name, "a.parquet"), index=False)
        out = self._out(["a.parquet"])
        self.assertIn("DEPTH GAP [HK]", out)
        self.assertIn("不可扫", out)
        self.assertNotIn("0 只标的可扫", out)

    def test_never_silent(self):
        """任何一个分支都必须有输出——空输出即静默假绿。"""
        for files in ([], ["a.parquet"]):
            if files:
                idx = pd.date_range("2026-01-01", periods=50, freq="D")
                pd.DataFrame({"close": [1.0] * 50}, index=idx).to_parquet(
                    os.path.join(self.tmp.name, "a.parquet"), index=False)
            self.assertTrue(self._out(files).strip(), "files=%r 无任何输出" % files)


if __name__ == "__main__":
    unittest.main()
