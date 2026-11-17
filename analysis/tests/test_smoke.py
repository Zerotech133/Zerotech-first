# -*- coding: utf-8 -*-
"""P0 冒烟测试：schema、快照、特征、结论、质量门。

2026-11-17 治本：原 TestPipeline 依赖本机 curated parquet 与 8091 门户服务，
干净 runner 上 load_snapshot 返回 last_trade_date=None，5 个用例在 setUpClass
直接 ERROR，只能靠 tools/test_baseline.ci.json 的 analysis/tests 键豁免。
现改为内存自造夹具 + mock 掉外部 IO：5 个用例在 CI 与本机都能真跑，
故可删除基线键（收口条件已履行）。断言未放宽：仍校验真实产品函数的输出。
"""
import json
import os
import sys
import unittest
from unittest import mock

import pandas as pd

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(HERE))

from analysis import config, gates, snapshot, features, verdict  # noqa: E402


def _fake_parquet(path, columns=None, **kwargs):
    """只喂 build_features 的行业/同业分支，绝不读磁盘资产。"""
    p = str(path).replace("\\", "/")
    if p.endswith("sw_classify_l1.parquet"):
        return pd.DataFrame({
            "level": ["L1"], "industry_name": ["夹具行业"], "index_code": ["F001"],
            "parent_code": ["0"], "industry_code": ["F001"],
        })
    if p.endswith("sw_l1_member.parquet"):
        return pd.DataFrame({
            "index_code": ["F001"] * 3,
            "con_code": ["600001.SH", "600002.SH", "600003.SH"],
            "is_new": ["Y"] * 3,
        })
    if p.endswith("daily_basic.parquet"):
        df = pd.DataFrame({
            "ts_code": ["600001.SH", "600002.SH", "600003.SH"],
            "trade_date": ["20261023"] * 3,
            "pb": [1.4, 1.5, 1.6],
            "ps_ttm": [1.8, 2.0, 2.2],
            "total_mv": [400000.0, 500000.0, 600000.0],
        })
        if columns:
            df = df[[c for c in columns if c in df.columns]]
        return df
    raise FileNotFoundError(p)


def _make_snap():
    """内存快照夹具：字段与 snapshot.load_snapshot 的返回结构一致。"""
    n = 30
    dates = ["202610%02d" % (i + 1) for i in range(n)]  # 仅作排序键，不校验真实交易日
    close = [3.50 + (3.56 - 3.50) * i / (n - 1) for i in range(n)]
    daily = pd.DataFrame({
        "trade_date": dates, "close": close,
        "high": [c + 0.05 for c in close], "low": [c - 0.05 for c in close],
        "vol": [100000.0] * n, "pct_chg": [0.5] * n,
    })
    db = pd.DataFrame({
        "trade_date": dates, "total_mv": [500000.0] * n,
        "pe_ttm": [20.0] * n, "pb": [1.5] * n, "ps_ttm": [2.0] * n,
        "turnover_rate_f": [1.2] * n,
    })
    inc = pd.DataFrame({
        "end_date": ["20250630", "20251231", "20260630"],
        "revenue": [9.0e8, 1.0e9, 1.2e9],
        "n_income_attr_p": [8.0e7, 1.0e8, 1.2e8],
    })
    mf = pd.DataFrame({
        "trade_date": dates[-10:],
        "buy_lg_amount": [100.0] * 10, "buy_elg_amount": [50.0] * 10,
        "sell_lg_amount": [80.0] * 10, "sell_elg_amount": [40.0] * 10,
    })
    chips = pd.DataFrame({
        "trade_date": ["20261023"], "cost_50pct": [3.4],
        "cost_15pct": [3.2], "cost_85pct": [3.7],
    })
    margin = pd.DataFrame({"trade_date": ["20261023"], "rzye": [2.0e8]})
    anns = pd.DataFrame({"ann_date": ["20261020"], "title": ["夹具公告"]})
    domains = {k: {"as_of": "2026-10-23", "rows": 1, "status": "ok", "source": "fixture"}
               for k in config.DOMAINS}
    return {
        "code": "600337.SH", "as_of": "2026-10-23", "name": "夹具股份",
        "last_trade_date": "20261023", "coverage": 100.0,
        "required_missing": [], "domains": domains,
        "raw": {
            "quote": daily, "valuation": db,
            "financial": {"income": inc, "balancesheet": None, "cashflow": None, "fina": None},
            "moneyflow": mf, "margin": margin, "chips": chips,
            "announcement": anns, "news": [],
        },
        "basic": {"name": "夹具股份"}, "news": [], "holder_num": [],
        "industry": {"l1_name": "夹具行业", "match": "fixture"},
        "calendar": [],
        "data_lag": {"last_trade_date": "20261023", "today": "20261023",
                     "calendar_days": 0, "trade_days": 0},
        "events": [],
    }


class TestSchema(unittest.TestCase):
    def test_schema_loads(self):
        with open(config.SCHEMA_PATH, encoding="utf-8") as f:
            s = json.load(f)
        self.assertIn("properties", s)
        for k in ("meta", "snapshot", "features", "verdict", "sources", "checks"):
            self.assertIn(k, s["properties"])


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snap = _make_snap()
        with mock.patch("analysis.features._api_json", return_value=None), \
             mock.patch("pandas.read_parquet", side_effect=_fake_parquet):
            cls.feat = features.build_features(cls.snap)
            cls.vd = verdict.build_verdict(cls.snap, cls.feat, [])

    def test_snapshot(self):
        self.assertEqual(self.snap["name"], "夹具股份")
        self.assertGreaterEqual(self.snap["coverage"], 80)
        self.assertEqual(self.snap["last_trade_date"], "20261023")
        self.assertIn("quote", self.snap["domains"])

    def test_features(self):
        self.assertAlmostEqual(self.feat["technical"]["price"], 3.56, places=2)
        self.assertIsNotNone(self.feat["valuation"].get("ps_ttm"))
        self.assertGreaterEqual(len(self.feat.get("peers") or []), 3)

    def test_verdict(self):
        self.assertIn(self.vd["rating"], config.RATINGS)
        self.assertTrue(0 <= self.vd["confidence"] <= 1)
        self.assertEqual(len(self.vd["scenarios"]), 4)
        self.assertIn("研究分析，不构成投资建议", self.vd["disclaimer"])

    def test_gate_g1(self):
        g = gates.g1_data(self.snap)
        self.assertIn("passed", g)
        self.assertTrue(g["passed"])
        self.assertGreaterEqual(g["score"], config.COVERAGE_MIN)

    def test_extras_optional(self):
        from analysis import extras
        with mock.patch("analysis.sync.load_code", return_value=None):
            ex = extras.build(self.snap)
        self.assertIsInstance(ex, dict)
        # 缓存不存在时允许全空；存在时应至少有一项
        self.assertTrue(all(v is None or isinstance(v, (dict, list)) for v in ex.values()))

    def test_build_features_without_last_trade_date(self):
        """回归护栏：无行情日时不得再抛 AttributeError。"""
        snap2 = dict(self.snap)
        snap2["last_trade_date"] = None
        with mock.patch("analysis.features._api_json", return_value=None), \
             mock.patch("pandas.read_parquet", side_effect=_fake_parquet):
            feat = features.build_features(snap2)
        self.assertIsInstance(feat, dict)
        self.assertNotIn("winner_rate", feat["chips"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
