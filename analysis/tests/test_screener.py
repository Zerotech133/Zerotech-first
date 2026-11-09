# -*- coding: utf-8 -*-
"""H2 筛选引擎测试：DSL 校验/过滤/排序/解释/输出（纯函数 + 注入 provider，离线）。"""
import os
import sys
import unittest

import pandas as pd

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(WT))
sys.path.insert(0, WT)

from analysis import screener  # noqa: E402


def _df():
    return pd.DataFrame([
        {"ts_code": "600337.SH", "name": "ST美克", "industry_l1": "轻工制造", "ps_pct_hist": 92.0,
         "revenue_yoy": -0.368, "net_profit_yoy": -3.12, "debt_ratio": 0.875, "flag_st": True,
         "quality_score": 36.0},
        {"ts_code": "603816.SH", "name": "顾家家居", "industry_l1": "轻工制造", "ps_pct_hist": 38.0,
         "revenue_yoy": 0.062, "net_profit_yoy": 0.11, "debt_ratio": 0.45, "flag_st": False,
         "quality_score": 72.4},
        {"ts_code": "603833.SH", "name": "欧派家居", "industry_l1": "轻工制造", "ps_pct_hist": 44.0,
         "revenue_yoy": 0.031, "net_profit_yoy": 0.08, "debt_ratio": 0.42, "flag_st": False,
         "quality_score": 69.8},
        {"ts_code": "688023.SH", "name": "安恒信息", "industry_l1": "计算机", "ps_pct_hist": 21.0,
         "revenue_yoy": 0.0086, "net_profit_yoy": 0.0922, "debt_ratio": 0.46, "flag_st": False,
         "quality_score": 49.4},
    ])


class TestValidate(unittest.TestCase):
    def test_ok(self):
        v = screener.validate({"universe": "WATCHLIST",
                               "filters": [{"field": "ps_pct_hist", "op": "<=", "value": 50}],
                               "rank_by": [{"field": "quality_score", "order": "desc"}],
                               "limit": 10, "as_of": "2026-11-02"})
        self.assertTrue(v["ok"], v["errors"])
        self.assertEqual(v["spec"]["limit"], 10)

    def test_bad_field_and_op(self):
        v = screener.validate({"universe": "WATCHLIST",
                               "filters": [{"field": "nope", "op": "<=", "value": 1}]})
        self.assertFalse(v["ok"])
        v = screener.validate({"universe": "WATCHLIST",
                               "filters": [{"field": "ps", "op": "contains", "value": "x"}]})
        self.assertFalse(v["ok"])

    def test_limit_clamped(self):
        v = screener.validate({"universe": "WATCHLIST",
                               "filters": [{"field": "ps", "op": ">", "value": 0}], "limit": 9999})
        self.assertEqual(v["spec"]["limit"], 200)


class TestFilterAndRun(unittest.TestCase):
    def _spec(self, **kw):
        spec = {"universe": "A_SHARE", "as_of": "2026-11-02",
                "filters": [
                    {"field": "ps_pct_hist", "op": "<=", "value": 50},
                    {"field": "revenue_yoy", "op": ">=", "value": 0},
                    {"field": "flag_st", "op": "=", "value": False},
                ],
                "rank_by": [{"field": "quality_score", "order": "desc"}],
                "limit": 20, "explain": True}
        spec.update(kw)
        return spec

    def test_apply_filters_and_reasons(self):
        sp = screener.validate(self._spec())["spec"]
        out = screener.apply_filters(_df(), sp)
        self.assertEqual(len(out), 3)  # 顾家/欧派/安恒；ST美克被 flag_st 排除
        self.assertTrue(all(out["flag_st"] == False))  # noqa: E712
        self.assertTrue(all(len(r) >= 1 for r in out["_reasons"]))

    def test_run_with_provider(self):
        r = screener.run(self._spec(), provider=lambda sp: _df())
        self.assertTrue(r["ok"], r.get("errors"))
        self.assertEqual(r["matched"], 3)
        self.assertEqual([c["code"] for c in r["candidates"]],
                         ["603816.SH", "603833.SH", "688023.SH"])
        self.assertIn("研究", r["disclaimer"])
        self.assertTrue(r["candidates"][0]["reasons"])

    def test_run_empty_universe(self):
        r = screener.run(self._spec(), provider=lambda sp: pd.DataFrame())
        self.assertTrue(r["ok"])
        self.assertEqual(r["matched"], 0)

    def test_run_bad_spec(self):
        r = screener.run({"filters": []})
        self.assertFalse(r["ok"])



class TestDataHelpers(unittest.TestCase):
    def test_apply_names_flags_st(self):
        df = pd.DataFrame({"ts_code": ["600337.SH", "688023.SH"]}).set_index("ts_code")
        out = screener._apply_names(df, {"600337.SH": "ST美克", "688023.SH": "安恒信息"})
        self.assertTrue(bool(out.loc["600337.SH", "flag_st"]))
        self.assertFalse(bool(out.loc["688023.SH", "flag_st"]))
        self.assertEqual(out.loc["600337.SH", "name"], "ST美克")

    def test_stock_basic_loader(self):
        import os
        import tempfile
        td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            d = os.path.join(td.name, "stock_basic")
            os.makedirs(d)
            pd.DataFrame({"ts_code": ["600000.SH"], "name": ["浦发银行"]}).to_parquet(
                os.path.join(d, "part_0000.parquet"), index=False)
            names = screener._stock_basic(root=td.name)
            self.assertEqual(names.get("600000.SH"), "浦发银行")
        finally:
            td.cleanup()

    def test_event_types_uses_code_column(self):
        import os
        import sqlite3
        import tempfile
        td = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        try:
            db = os.path.join(td.name, "analysis.db")
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE events(event_id TEXT, code TEXT, date TEXT, category TEXT, "
                        "category_final TEXT)")
            con.execute("INSERT INTO events VALUES('e1','600337.SH','2026-11-03','回购','回购')")
            con.execute("INSERT INTO events VALUES('e2','688023.SH','2026-10-01','业绩','业绩')")
            con.commit()
            con.close()
            ev = screener._event_types({"600337.SH", "688023.SH"}, "2026-11-03", db_path=db)
            self.assertEqual(ev.get("600337.SH"), ["回购"])
            self.assertEqual(ev.get("688023.SH"), ["业绩"])
            ev2 = screener._event_types({"600000.SH"}, "2026-11-03", db_path=db)
            self.assertEqual(ev2, {})
        finally:
            td.cleanup()


class TestReviewFixes(unittest.TestCase):
    """2026-11-04 代码审查 P1 修复回归：单位口径/日期/行业白名单/缺列不静默。"""

    def test_fmt_units_percent_vs_ratio(self):
        self.assertEqual(screener._fmt("dv_ratio", 1.18), "1.2%")
        self.assertEqual(screener._fmt("turnover_rate", 2.44), "2.4%")
        self.assertEqual(screener._fmt("revenue_yoy", 0.062), "6.2%")
        self.assertEqual(screener._fmt("ps_pct_hist", 92.0), "92.0%")
        u = screener.units()
        self.assertEqual(u["dv_ratio"], "percent")
        self.assertEqual(u["revenue_yoy"], "ratio")

    def test_reason_uses_unit(self):
        sp = screener.validate({"universe": "WATCHLIST", "as_of": "2026-11-02",
                                "filters": [{"field": "dv_ratio", "op": ">=", "value": 1.0}]})["spec"]
        out = screener.apply_filters(pd.DataFrame([{"ts_code": "600000.SH", "dv_ratio": 1.18}]), sp)
        reason = out.iloc[0]["_reasons"][0]
        self.assertIn("1.2%", reason)
        self.assertNotIn("117.8", reason)

    def test_as_of_strict(self):
        for bad in ("9999-99-99", "2026-13-45", "2026-02-30", "2999-01-01"):
            v = screener.validate({"universe": "WATCHLIST", "as_of": bad,
                                   "filters": [{"field": "ps", "op": ">", "value": 0}]})
            self.assertFalse(v["ok"], bad)

    def test_industry_whitelist_rejects_regex(self):
        v = screener.validate({"universe": "industry:.*",
                               "filters": [{"field": "ps", "op": ">", "value": 0}]})
        self.assertFalse(v["ok"])
        v = screener.validate({"universe": "WATCHLIST",
                               "filters": [{"field": "industry_l1", "op": "contains", "value": ".*"}]})
        self.assertFalse(v["ok"])
        v = screener.validate({"universe": "industry:轻工制造",
                               "filters": [{"field": "ps", "op": ">", "value": 0}]})
        self.assertTrue(v["ok"], v["errors"])

    def test_industry_filter_missing_column_not_silent(self):
        df = _df().drop(columns=["industry_l1"])
        r = screener.run({"universe": "WATCHLIST", "as_of": "2026-11-02",
                          "filters": [{"field": "industry_l1", "op": "contains", "value": "轻工制造"}]},
                         provider=lambda sp: df)
        self.assertFalse(r["ok"])
        self.assertIn("行业", r["errors"][0])

    def test_run_has_units(self):
        sp = {"universe": "A_SHARE", "as_of": "2026-11-02",
              "filters": [{"field": "ps_pct_hist", "op": "<=", "value": 100}], "limit": 5}
        r = screener.run(sp, provider=lambda s: _df())
        self.assertTrue(r["ok"], r.get("errors"))
        self.assertEqual(r["units"]["ps_pct_hist"], "percent")


if __name__ == "__main__":
    unittest.main(verbosity=2)
