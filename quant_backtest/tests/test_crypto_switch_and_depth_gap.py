# -*- coding: utf-8 -*-
"""[WO-QT-20261021-094] 双轨收编：crypto_sync 默认停用开关 + 港股深度缺口明示。

背景（核查单 §二 第2点）：库内 CRYPTO 37 对 130 根/只、跨度 2026-06-14
起，与本地 parquet 完全同源同深度；供给层自有活水。crypto_sync.py 直连
Binance 属重复搬运，与 WO-085「禁双轨」冲突 → 默认停用，数据改走
export_us_hk.py --market CRYPTO。

背景（核查单 §二 第1点）：HK 3408 只全部 < MIN_BARS=120，scan 恒 ok=0，
但返回码恒 0（剧本任务 result=0）→ 长期假绿。扫描器补 depth gap 明示，
把「不可扫」与「今日无信号」分开。

纪律：全程 mock，不触网、不读写真实 data/crypto，不跑真实同步。
运行：仓根 pytest quant_backtest/tests/test_crypto_switch_and_depth_gap.py -v
"""
import io
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))        # quant_backtest/tests
QBT = os.path.dirname(HERE)                              # quant_backtest
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import chanlun_scan as cs                                # noqa: E402
import crypto_sync as cy                                 # noqa: E402


class TestCryptoSyncSwitch(unittest.TestCase):
    def _run_main(self, env):
        buf = io.StringIO()
        tmp = tempfile.TemporaryDirectory()       # 空目录：不得读到真实 crypto
        self.addCleanup(tmp.cleanup)
        with mock.patch.dict(os.environ, env, clear=False):
            if "CRYPTO_SYNC_ENABLED" not in env:
                os.environ.pop("CRYPTO_SYNC_ENABLED", None)
            with mock.patch.object(cy, "sync_pair") as sp, \
                    mock.patch.object(cy, "DIR", tmp.name):
                with redirect_stdout(buf):
                    rc = cy.main()
        return rc, buf.getvalue(), sp

    def test_default_is_disabled(self):
        """未设环境变量 → 默认停用，不进同步循环。"""
        rc, out, sp = self._run_main({})
        self.assertEqual(rc, 0)
        self.assertIn("DISABLED", out)
        self.assertIn("WO-QT-20261021-094", out)
        sp.assert_not_called()

    def test_explicit_zero_disabled(self):
        """显式 0/false/off → 仍停用。"""
        for v in ("0", "false", "off", ""):
            rc, out, sp = self._run_main({"CRYPTO_SYNC_ENABLED": v})
            self.assertIn("DISABLED", out, "值 %r 应判停用" % v)
            sp.assert_not_called()

    def test_flag_on_rolls_back(self):
        """置 1 → 恢复旧行为（进入同步循环）。"""
        rc, out, sp = self._run_main({"CRYPTO_SYNC_ENABLED": "1"})
        self.assertNotIn("DISABLED", out)
        self.assertIn("CRYPTO SYNC DONE", out)

    def test_truthy_variants(self):
        for v in ("true", "YES", "on", "On"):
            with mock.patch.dict(os.environ, {"CRYPTO_SYNC_ENABLED": v}):
                self.assertTrue(cy._enabled(), "值 %r 应判启用" % v)


class TestDepthGapReport(unittest.TestCase):
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

    def _mk(self, name, n):
        idx = pd.date_range("2026-01-01", periods=n, freq="D")
        pd.DataFrame({"close": [1.0] * n}, index=idx).to_parquet(
            os.path.join(self.tmp.name, name), index=False)

    def _report(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            cs._report_depth_gap(["a.parquet", "b.parquet", "c.parquet"])
        return buf.getvalue()

    def test_all_below_min_bars_flagged(self):
        """全部低于门槛 → 明确报 DEPTH GAP 且点明不可扫非无信号。"""
        cs._CUR_MKT = "HK"
        self._mk("a.parquet", 94)
        self._mk("b.parquet", 108)
        self._mk("c.parquet", 50)
        out = self._report()
        self.assertIn("DEPTH GAP [HK]", out)
        self.assertIn("3/3 只低于 MIN_BARS=120", out)
        self.assertIn("不可扫", out)
        self.assertIn("不是今日无信号", out)

    def test_partial_pass_counted_right(self):
        """部分达标 → 计数分列（300/130 达标、20 缺口 → 1/3 低于）。"""
        cs._CUR_MKT = "US"
        self._mk("a.parquet", 300)
        self._mk("b.parquet", 130)
        self._mk("c.parquet", 20)
        out = self._report()
        self.assertIn("DEPTH GAP [US]", out)
        self.assertIn("1/3 只低于", out)
        self.assertIn("中位 130 根", out)

    def test_unreadable_files_do_not_crash(self):
        """不可读文件不得抛异常（扫描链不得因此中断）。"""
        self._mk("a.parquet", 94)
        buf = io.StringIO()
        with redirect_stdout(buf):
            cs._report_depth_gap(["a.parquet", "ghost.parquet"])
        self.assertIn("DEPTH GAP", buf.getvalue())

    def test_parquet_rows_returns_none_on_garbage(self):
        p = os.path.join(self.tmp.name, "bad.parquet")
        with open(p, "w", encoding="utf-8") as f:
            f.write("not a parquet")
        self.assertIsNone(cs._parquet_rows(p))


if __name__ == "__main__":
    unittest.main()
