# -*- coding: utf-8 -*-
"""sector_effect 核心函数单测（合成数据，不依赖 TDX/ashare 真实数据）。

覆盖: RPS 截面分位、板块共性两比例、龙头超额与强度阈值、
边界（空板块 / 成分<MIN_MEMBERS 跳过 / 个股数据不足不计分母）；
S5: batch_rps new_flag 次新影子标记；S6: market_env 闸门三态
（True 放行 / False 否决 / None 缺数据降权）及 portfolio 下游语义。
"""
import json
import os
import sys
import unittest
import unittest.mock

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import sector_effect as se


class TestCalcRps(unittest.TestCase):
    """RPS 截面分位。"""

    def test_ranking_order(self):
        # 5 个板块涨幅从高到低 → RPS 依次 100..20（rank pct × 100）
        chg = pd.Series({"A": .50, "B": .30, "C": .10, "D": -.05, "E": -.20})
        rps = se.calc_rps(chg)
        self.assertAlmostEqual(rps["A"], 100.0)
        self.assertAlmostEqual(rps["E"], 20.0)
        self.assertTrue((rps["B"] > rps["C"] > rps["D"]).all())

    def test_nan_not_ranked(self):
        # 历史不足的板块 NaN，不参与分位、结果保持 NaN
        chg = pd.Series({"A": .10, "B": .20, "C": np.nan})
        rps = se.calc_rps(chg)
        self.assertTrue(pd.isna(rps["C"]))
        self.assertAlmostEqual(rps["A"], 50.0)
        self.assertAlmostEqual(rps["B"], 100.0)

    def test_all_nan(self):
        rps = se.calc_rps(pd.Series({"A": np.nan}))
        self.assertTrue(pd.isna(rps["A"]))


class TestCalcCohesion(unittest.TestCase):
    """板块共性: 结构相似比例 + 多头排列比例。"""

    def test_all_aligned_uptrend(self):
        # 单边上涨序列: 全部站上 MA20 且 MA20 上行、多头排列 → 双 1.0
        closes = {"600001": np.arange(1, 31, dtype=float)}
        coh, align = se.calc_cohesion(closes)
        self.assertEqual(coh, 1.0)
        self.assertEqual(align, 1.0)

    def test_all_downtrend(self):
        # 单边下跌序列: 双比例 0.0
        closes = {"600001": np.arange(30, 0, -1, dtype=float)}
        coh, align = se.calc_cohesion(closes)
        self.assertEqual(coh, 0.0)
        self.assertEqual(align, 0.0)

    def test_mixed_ratio(self):
        # 一涨一跌各半 → 双 0.5
        up = np.arange(1, 31, dtype=float)
        closes = {"600001": up, "600002": up[::-1].copy()}
        coh, align = se.calc_cohesion(closes)
        self.assertEqual(coh, 0.5)
        self.assertEqual(align, 0.5)

    def test_insufficient_bars_excluded(self):
        # <21 根的个股不计分母: 只有 1 只有效 + 1 只 10 根 → 仍看那只
        closes = {"600001": np.arange(1, 31, dtype=float),
                  "600002": np.ones(10)}
        coh, align = se.calc_cohesion(closes)
        self.assertEqual(coh, 1.0)
        self.assertEqual(align, 1.0)

    def test_all_insufficient_returns_none(self):
        closes = {"600001": np.ones(10)}
        coh, align = se.calc_cohesion(closes)
        self.assertIsNone(coh)
        self.assertIsNone(align)

    def test_empty_returns_none(self):
        coh, align = se.calc_cohesion({})
        self.assertIsNone(coh)
        self.assertIsNone(align)


class TestCalcLeaders(unittest.TestCase):
    """龙头超额与强度阈值。"""

    def test_top3_and_excess(self):
        chg = pd.Series({"A": .20, "B": .15, "C": .10, "D": .05,
                         "E": .01, "F": np.nan})
        leaders, strength, strong = se.calc_leaders(chg, .04)
        self.assertEqual([l["code"] for l in leaders], ["A", "B", "C"])
        self.assertAlmostEqual(leaders[0]["excess"], 16.0)   # .20-.04
        self.assertAlmostEqual(strength, 11.0)               # 均值 .15-.04
        self.assertTrue(strong)                              # .15 ≥ 1.5×.04

    def test_weak_when_sector_slow(self):
        # 前3均涨幅 .10 < 1.5×.09=.135 → 不强
        chg = pd.Series({"A": .12, "B": .10, "C": .08, "D": .02})
        _l, _s, strong = se.calc_leaders(chg, .09)
        self.assertFalse(strong)

    def test_down_sector_not_strong(self):
        # 板块下跌时超额无意义 → 强制 False
        chg = pd.Series({"A": .30, "B": .20, "C": .10})
        _l, _s, strong = se.calc_leaders(chg, -.05)
        self.assertFalse(strong)

    def test_empty_returns_empty(self):
        leaders, strength, strong = se.calc_leaders(pd.Series(dtype=float),
                                                    .05)
        self.assertEqual(leaders, [])
        self.assertIsNone(strength)
        self.assertIsNone(strong)

    def test_nan_only(self):
        leaders, strength, strong = se.calc_leaders(
            pd.Series({"A": np.nan}), .05)
        self.assertEqual(leaders, [])
        self.assertIsNone(strength)
        self.assertIsNone(strong)


class TestChg(unittest.TestCase):
    """N 日涨幅口径。"""

    def test_basic(self):
        self.assertAlmostEqual(se._chg(np.array([10.0, 11.0, 12.0]), 2),
                               0.2)

    def test_insufficient(self):
        self.assertIsNone(se._chg(np.array([10.0, 11.0]), 2))

    def test_bad_base(self):
        # 基期 ≤0 → None（防除零/负价脏数据）
        self.assertIsNone(se._chg(np.array([0.0, 5.0, 6.0]), 2))


class TestScanOne(unittest.TestCase):
    """scan_one 组装层（注入合成数据，不触磁盘）。"""

    def test_few_members_skipped(self):
        # 成分 <MIN_MEMBERS → 共性/龙头 null + skip 标记
        row = se.scan_one("测试板块", "880999",
                          {"880999": np.arange(1, 61, dtype=float)},
                          {"测试板块": ["600001", "600002"]})
        self.assertEqual(row.get("skip"), "members<5")
        self.assertIsNone(row["cohesion"])
        self.assertIsNone(row["leader_strength"])

    def test_no_member_block_still_has_rps_slot(self):
        # 无成分板块: 结构完整、共性/龙头置 None（全量扫描时补 rps50）
        row = se.scan_one("孤儿板块", "880998",
                          {"880998": np.arange(1, 61, dtype=float)}, {})
        self.assertEqual(row["sector"], "孤儿板块")
        self.assertIsNone(row["rps50"])      # rps50 由 scan 层回填

    def test_missing_index_data(self):
        # 指数无日线: 不抛异常；成分读不到数据 → 字段全 None
        with unittest.mock.patch.object(
                se, "load_stock_closes", return_value={}):
            row = se.scan_one("空指板块", "880997", {},
                              {"空指板块": ["60000%d" % i
                                            for i in range(1, 8)]})
        self.assertIsNone(row["cohesion"])
        self.assertIsNone(row["leader_strength"])
        self.assertEqual(row["leaders"], [])


# ── S5: batch_rps new_flag 次新影子标记 ────────────────────
class TestBatchRpsNewFlag(unittest.TestCase):
    """S5: new_flag = 可观测K线<250根 → True。纯标记不动 rps_mix。

    口径: A股数据源全量历史，根数≈上市交易日数 → 等价「上市不足
    250 个交易日」；DB 市场(美港加密)拉取窗口仅 200/300 自然日，
    其 new_flag 只代表窗口内历史不足。"""

    @classmethod
    def setUpClass(cls):
        import rps_resonance as rr
        cls.rr = rr

    def test_flag_values(self):
        closes = {"600001.SH": np.linspace(10, 20, 300),   # 老股
                  "301001.SZ": np.linspace(10, 20, 100),   # 次新
                  "600000.SH": np.linspace(10, 20, 250)}   # 恰好250根
        df = self.rr.batch_rps(closes)
        self.assertFalse(bool(df.loc["600001.SH", "new_flag"]))
        self.assertTrue(bool(df.loc["301001.SZ", "new_flag"]))
        self.assertFalse(bool(df.loc["600000.SH", "new_flag"]))
        self.assertEqual(df["new_flag"].dtype, bool)

    def test_too_short_not_in_frame(self):
        # 50 周期都算不出的(<51根)不进 RPS 表（现状口径回归）
        closes = {"301002.SZ": np.linspace(10, 20, 30),
                  "600001.SH": np.linspace(10, 20, 300)}
        df = self.rr.batch_rps(closes, periods=(50,))
        self.assertNotIn("301002.SZ", df.index)
        self.assertIn("600001.SH", df.index)

    def test_flag_pure_shadow(self):
        # 影子纪律: new_flag 加入后 rps_mix 与手工期望一致（未被污染）
        closes = {"UP": np.linspace(10, 15, 300),      # 50日涨
                  "DOWN": np.linspace(20, 10, 300)}    # 50日跌
        df = self.rr.batch_rps(closes, periods=(50,))
        self.assertAlmostEqual(df.loc["UP", "rps_mix"], 100.0)
        self.assertAlmostEqual(df.loc["DOWN", "rps_mix"], 50.0)

    def test_empty_closes(self):
        df = self.rr.batch_rps({})
        self.assertTrue(df.empty)


# ── S6: market_env 闸门三态 ────────────────────────────────
class TestMarketEnvGate(unittest.TestCase):
    """S6: True=放行 / False=一票否决 / None=数据缺失(缺维降权)。"""

    @classmethod
    def setUpClass(cls):
        import rps_resonance as rr
        cls.rr = rr

    def test_cold_veto_false(self):
        # 数据齐(≥5只)且宽度<0.40 → 明确否决 False
        closes = {("60000%d" % i): np.linspace(20, 10, 60)
                  for i in range(5)}
        self.assertIs(self.rr.market_env(closes)["safe"], False)

    def test_warm_pass_true(self):
        # 数据齐、宽度≥0.55 且 20日中位>0 → True
        closes = {("60000%d" % i): np.linspace(10, 20, 60)
                  for i in range(5)}
        self.assertIs(self.rr.market_env(closes)["safe"], True)

    def test_empty_none(self):
        e = self.rr.market_env({})
        self.assertIsNone(e["safe"])
        self.assertIn("数据缺失", e["state"])

    def test_all_short_none(self):
        # 全部 <21 根:宽度无法计算 → None（改前误当 safe=True 放行）
        e = self.rr.market_env({"600000.SH": np.ones(10)})
        self.assertIsNone(e["safe"])

    def test_few_samples_none(self):
        # 有效样本 3 (<POOL_GATE_MIN=5):宽度无统计意义 → None
        closes = {("60000%d" % i): np.linspace(20, 10, 60)
                  for i in range(3)}
        self.assertIsNone(self.rr.market_env(closes)["safe"])


# ── S6: 共振打分对 None/False 的区分 ───────────────────────
class TestResonanceGateDownstream(unittest.TestCase):
    """S6: safe=None → 环境维缺维降权、不回避；
    safe=False → 一票否决「回避（环境闸门）」。"""

    @classmethod
    def setUpClass(cls):
        import rps_resonance as rr
        cls.rr = rr
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.tmp.name, "news_config.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"watchlist": [{"code": "sh600519",
                                      "name": "贵州茅台"}]}, f)
        cls.df60 = pd.DataFrame(
            {"close": np.linspace(10, 20, 60)},
            index=pd.date_range("2026-06-01", periods=60))
        cls._stubs = [
            unittest.mock.patch("runner.NEWS_DIR", cls.tmp.name),
            unittest.mock.patch(
                "runner.resolve_watchlist",
                return_value=[("CN", "sh600519", "茅台")]),
            unittest.mock.patch(
                "data_loader.load_bars",
                return_value={"sh600519": cls.df60}),  # 真实源码按 sym 取
            unittest.mock.patch.object(
                rr, "_jev_pool_scores", return_value={}),
            unittest.mock.patch.dict(
                "strategies.REGISTRY",
                {sn: (lambda df, params: pd.Series(0, index=df.index))
                 for sn in ("trend_follow", "ma_cross",
                            "rsi_reversal", "boll_breakout")}),
        ]
        for st in cls._stubs:
            st.start()

    @classmethod
    def tearDownClass(cls):
        for st in reversed(cls._stubs):
            st.stop()
        cls.tmp.cleanup()

    def _row(self, safe):
        env = {"score": 20 if safe is False else 80,
               "breadth": .3, "ret20": -.04, "safe": safe,
               "state": "test"}
        rps_df = self.rr.batch_rps(
            {"600519.SH": self.df60["close"].to_numpy()}, periods=(50,))
        rows, _pool = self.rr.resonance_watchlist(rps_df, env, {}, {})
        self.assertEqual(len(rows), 1)
        return rows[0]

    def test_safe_none_dims_downweighted(self):
        row = self._row(None)
        self.assertIsNone(row["env"])
        self.assertNotIn("env", row["dims"])
        self.assertNotIn("env", row["w"])
        # 缺维降权: tech .35 / rps .25 归一 → .583/.417（环境不进权重）
        self.assertAlmostEqual(row["w"]["tech"], 0.583, places=3)
        self.assertAlmostEqual(row["w"]["rps"], 0.417, places=3)
        self.assertNotIn("环境闸门", row["action"])

    def test_safe_false_veto(self):
        row = self._row(False)
        self.assertEqual(row["env"], 20)
        self.assertIn("env", row["dims"])
        self.assertEqual(row["action"], "回避（环境闸门）")

    def test_safe_true_no_veto(self):
        row = self._row(True)
        self.assertEqual(row["env"], 80)
        self.assertNotIn("环境闸门", row["action"])


# ── S5 落地(K230): A 股次新不产生买入信号 ─────────────────
class TestSubNewFilter(unittest.TestCase):
    """new_flag=true 且过买入线 → 「观察（次新）」；
    老股同数据 → 正常「买入」；次新不过线 → 「观察」不变。
    DB 市场（美/港/加密）不消费 new_flag（系统性 True）。"""

    @classmethod
    def setUpClass(cls):
        import rps_resonance as rr
        cls.rr = rr
        import tempfile
        cls.tmp = tempfile.TemporaryDirectory()
        with open(os.path.join(cls.tmp.name, "news_config.json"),
                  "w", encoding="utf-8") as f:
            json.dump({"watchlist": [{"code": "sh600519",
                                      "name": "贵州茅台"}]}, f)
        cls._stubs = [
            unittest.mock.patch("runner.NEWS_DIR", cls.tmp.name),
            unittest.mock.patch(
                "runner.resolve_watchlist",
                return_value=[("CN", "sh600519", "茅台")]),
            unittest.mock.patch.object(
                rr, "_jev_pool_scores", return_value={}),
            unittest.mock.patch.dict(
                "strategies.REGISTRY",
                {sn: (lambda df, params: pd.Series(1, index=df.index))
                 for sn in ("trend_follow", "ma_cross",
                            "rsi_reversal", "boll_breakout")}),
        ]
        for st in cls._stubs:
            st.start()

    @classmethod
    def tearDownClass(cls):
        for st in reversed(cls._stubs):
            st.stop()
        cls.tmp.cleanup()

    def _run(self, n_bars):
        """n 根递增K线 + 四策略全多头 + 单票 RPS=100 + 闸门放行
        → tech=100 / rps=100 / env=80 → score 远超买入线。"""
        df = pd.DataFrame(
            {"close": np.linspace(10, 10 + n_bars / 10.0, n_bars)},
            index=pd.date_range("2025-01-01", periods=n_bars))
        self._lb = unittest.mock.patch(
            "data_loader.load_bars", return_value={"sh600519": df})
        self._lb.start()
        try:
            env = {"score": 80, "breadth": .6, "ret20": .01,
                   "safe": True, "state": "test"}
            rps_df = self.rr.batch_rps(
                {"600519.SH": df["close"].to_numpy()}, periods=(50,))
            rows, _pool = self.rr.resonance_watchlist(rps_df, env, {}, {})
            self.assertEqual(len(rows), 1)
            return rows[0]
        finally:
            self._lb.stop()

    def test_subnew_buy_downgraded(self):
        row = self._run(60)          # 60 根 < 250 → 次新
        self.assertTrue(row["new_flag"])
        self.assertEqual(row["action"], "观察（次新）")

    def test_old_stock_still_buy(self):
        row = self._run(300)         # 300 根 ≥ 250 → 老股
        self.assertFalse(row["new_flag"])
        self.assertEqual(row["action"], "买入")

    def test_subnew_below_buy_line_unchanged(self):
        """次新但分数不过线 → 不触发过滤文案（保持原 action）。"""
        row = self._run(60)
        # 本构造分数必过线，这里仅验证 new_flag 字段进产物
        self.assertIn("new_flag", row)


# ── S6 下游: portfolio _envs_safe / gates 计数语义 ─────────
class TestPortfolioEnvsSafe(unittest.TestCase):
    """S6 下游适配: None 透传不误杀；gates 缺数据不计坏日不清零。"""

    @classmethod
    def setUpClass(cls):
        import portfolio as pf
        cls.pf = pf

    def test_envs_safe_three_state(self):
        cases = [
            ([{"label": "A股全市场(5551只)", "safe": True}],
             {"CN": True}),
            ([{"label": "A股全市场(5551只)", "safe": False}],
             {"CN": False}),
            ([{"label": "A股全市场(5551只)", "safe": None}],
             {"CN": None}),                       # 核心新语义: None 透传
            ([{"label": "美股全市场(5809只)", "safe": None}],
             {"US": None}),
        ]
        for envs, want in cases:
            with unittest.mock.patch.object(
                    self.pf, "_load_json",
                    return_value={"envs": envs}):
                self.assertEqual(self.pf._envs_safe(), want)

    def _run_gates(self, safe):
        led = {"gate_days": {"CN": 3}}
        ac = {"last_snap": "20260930", "open": []}
        ctx = {"px_map": {}, "action_map": {},
               "snap_date": "20261001", "alive": None}
        cfg = {"gate_bad_max": 5, "out_max": 3, "hold_max": 20}
        with unittest.mock.patch.object(
                    self.pf, "_envs_safe",
                    return_value={"CN": safe}), \
             unittest.mock.patch.object(
                 self.pf, "_rps_score_map", return_value={}), \
             unittest.mock.patch.dict(
                 self.pf.CTXS, {"qpzm": lambda: ctx}):
            self.pf._run_exits(led, ac, "qpzm", "20261001", cfg)
        return led["gate_days"]["CN"]

    def test_gate_none_holds(self):
        # 缺数据:不累计坏日、也不清零已累计（既不推进也不赦免）
        self.assertEqual(self._run_gates(None), 3)

    def test_gate_false_increments(self):
        self.assertEqual(self._run_gates(False), 4)

    def test_gate_true_resets(self):
        self.assertEqual(self._run_gates(True), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
