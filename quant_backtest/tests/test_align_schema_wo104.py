# -*- coding: utf-8 -*-
"""[WO-QT-20261021-104] _align_schema 审计修复测试：inf 漏洞 + 版本耦合。

审计发现：
A. `pct_chg = (close / pre_close - 1) * 100`，当 pre_close==0 时得 **inf**
   并写入 parquet，污染下游均值/排序/归一。实测 CN 已有 609 行 pre_close=0。
B. `df.get("turnover", df["volume"])` —— DataFrame.get 是 pandas **3.x** 才有的
   方法，pandas 2.x 会 AttributeError（当前两套运行时都是 3.x 才没炸）。

运行：仓根 pytest quant_backtest/tests/test_align_schema_wo104.py -v
"""
import os
import sys
import unittest

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
QBT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import export_us_hk as ex  # noqa: E402


def _df(pre_closes, turnover=True):
    idx = pd.date_range("2026-10-01", periods=len(pre_closes), freq="D")
    n = len(pre_closes)
    d = {
        "open": [10.0] * n, "high": [11.0] * n, "low": [9.0] * n,
        "close": [10.0] * n, "volume": [1000.0] * n,
        "pre_close": pre_closes,
    }
    if turnover:
        d["turnover"] = [5e6] * n
    return pd.DataFrame(d, index=idx)


class TestPctChgNoInf(unittest.TestCase):
    def test_zero_pre_close_yields_nan_not_inf(self):
        """pre_close=0 → pct_chg 必须是 NaN，绝不能是 inf。"""
        out = ex._align_schema(_df([0.0, 10.0]), "AAA", "US")
        self.assertFalse(out["pct_chg"].isin([float("inf"), float("-inf")]).any(),
                         "出现了 inf：会把下游统计全盘污染")
        self.assertTrue(pd.isna(out["pct_chg"].iloc[0]))

    def test_none_pre_close_yields_nan(self):
        out = ex._align_schema(_df([None, 10.0]), "AAA", "US")
        self.assertTrue(pd.isna(out["pct_chg"].iloc[0]))
        self.assertFalse(out["pct_chg"].isin([float("inf")]).any())

    def test_normal_values_unchanged(self):
        """正常值口径不得改变（close=10 / pre_close=10 → 0%）。"""
        out = ex._align_schema(_df([10.0, 8.0]), "AAA", "US")
        self.assertAlmostEqual(float(out["pct_chg"].iloc[0]), 0.0)
        self.assertAlmostEqual(float(out["pct_chg"].iloc[1]), 25.0)

    def test_all_zero_column_entirely_nan(self):
        out = ex._align_schema(_df([0.0, 0.0, 0.0]), "AAA", "US")
        self.assertTrue(out["pct_chg"].isna().all())

    def test_string_pre_close_coerced(self):
        """pre_close 为字符串（库内可能出 object 列）→ 必须 coerce 成数值。

        M3 变异（去掉 to_numeric 只留 where）在此用例下会因 str/数值运算
        报 TypeError，故本用例专门钉住 coerce 这一层。
        """
        out = ex._align_schema(_df(["10.0", "8.0"]), "AAA", "US")
        self.assertAlmostEqual(float(out["pct_chg"].iloc[0]), 0.0)
        self.assertAlmostEqual(float(out["pct_chg"].iloc[1]), 25.0)

    def test_mixed_garbage_pre_close_yields_nan(self):
        """非数值垃圾串 → NaN，不得抛异常、不得得 0。"""
        out = ex._align_schema(_df(["10.0", "N/A"]), "AAA", "US")
        self.assertFalse(pd.isna(out["pct_chg"].iloc[0]))
        self.assertTrue(pd.isna(out["pct_chg"].iloc[1]))


class TestNoVersionCoupling(unittest.TestCase):
    def test_turnover_used_when_present(self):
        out = ex._align_schema(_df([10.0], turnover=True), "AAA", "US")
        self.assertEqual(float(out["amount"].iloc[0]), 5e6)

    def test_fallback_to_volume_without_turnover(self):
        """无 turnover 列 → 回落 volume，且不得依赖 pandas 3.x 的 DataFrame.get。"""
        out = ex._align_schema(_df([10.0], turnover=False), "AAA", "US")
        self.assertEqual(float(out["amount"].iloc[0]), 1000.0)

    def test_hk_code_zero_padded(self):
        out = ex._align_schema(_df([10.0]), "700", "HK")
        self.assertEqual(out["ts_code"].iloc[0], "00700")

    def test_us_code_kept_as_is(self):
        out = ex._align_schema(_df([10.0]), "AAPL", "US")
        self.assertEqual(out["ts_code"].iloc[0], "AAPL")


if __name__ == "__main__":
    unittest.main()
