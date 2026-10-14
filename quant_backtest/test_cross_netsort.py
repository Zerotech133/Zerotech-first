# -*- coding: utf-8 -*-
"""W5 cross 净向分排序单测（20261014 cross-netdev）。

覆盖三条路径（全部合成数据，不读真实源、不碰网络/数据库/推送）：
1. 净向分计算：net_score = Σ(dir×w×sign)/Σ(可用源 w)——含 9dim 反指
   （方向取反×权重 0.5 → 命中贡献 -0.5）、单源缺失时分子分母同步剔除
   （归一化）、排序相对 raw 翻转；
2. 开关回退：config cross.sort_key="raw" → 完全旧行为（长票计数排序、
   无 net_score 字段）；非法值按缺省 net 处理不炸；
3. 与韧性改造共存：degraded_k3 模式下净向分正常出（缺失源归一化）；
   blocked 模式 topn 产空且 k 不降档；k 门槛与分级阈值不受排序影响。

判例与公式对齐 cross_grid_backtest（W3-4 网格）与
docs/changes/20261014_cross_netsort_changes.md。

纪律：临时目录合成产物；mock 点与 test_cross_resilience 同口径
（REPORTS/DATA/_CACHE/_src_chanlun_load/CONFIG_FP）。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import cross_poll           # noqa: E402


def _rps_row(code, name, longs=None, sigs=None, action="买入"):
    """合成 rps_latest.json res[] 行（longs/sigs 字段级可控）。"""
    r = {"code": code, "name": name, "market": "CN", "action": action,
         "score": 80}
    if longs is not None:
        r["longs"] = longs
    if sigs is not None:
        r["sigs"] = sigs
    return r


def _write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)


def _st(d, code):
    return next(s for s in d["stocks"] if s["code"] == code)


# 合成预期净向分（与 cross_poll.net_score_of 同公式，独立复算作交叉验证）
W_Q, W_S, W_C, W_9 = 1.0, 1.0, 1.0, 0.5


class _Base(unittest.TestCase):
    """合成产物沙箱：REPORTS/DATA/CONFIG_FP 全部指向临时目录。

    基线合成（四源齐整）：
    · 600519.SH  qpzm 买入 + sig4 4/4 + chanlun 3买 + 9dim 85 分
                 → net=(1+1+1-0.5)/3.5=0.7143（9dim 反指拖累）
    · 000001.SZ  qpzm 买入 + sig4 4/4 + chanlun 3买，9dim 无票
                 → net=3/3.5=0.8571
    · 300750.SZ  qpzm 买入 + sig4 1/4(neutral) + chanlun 一卖(short)
                 → net=(1+0-1)/3.5=0.0
    raw 排序（-n_hit,-n_long）：600519(4,4) > 000001(3,3) > 300750(3,1)；
    net 排序：000001(0.8571) > 600519(0.7143) > 300750(0.0) → 头名翻转。
    """

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="cross_net_")
        self.reports = os.path.join(self.tmp, "reports")
        self.data = os.path.join(self.tmp, "data")
        os.makedirs(self.reports, exist_ok=True)
        os.makedirs(self.data, exist_ok=True)
        self._orig = (cross_poll.REPORTS, cross_poll.DATA,
                      cross_poll._CACHE, cross_poll._src_chanlun_load,
                      cross_poll.CONFIG_FP)
        cross_poll.REPORTS = self.reports
        cross_poll.DATA = self.data
        cross_poll._CACHE = {"t": 0, "d": None}
        cross_poll.CONFIG_FP = os.path.join(self.tmp, "config.json")
        self._write_config({"cross": {"source_health_alert": False,
                                      "sort_key": "net"}})
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        self._write_fixtures(ts)
        # chanlun 默认满池（四源齐整基线）；单测按需覆盖为空池/异常
        cross_poll._src_chanlun_load = lambda: [
            {"code": "600519.SH", "type": "3买", "detect_date":
             datetime.now().strftime("%Y-%m-%d"), "name": "贵州茅台",
             "market": "CN", "dt": "2026-10-13"},
            {"code": "000001.SZ", "type": "3买", "detect_date":
             datetime.now().strftime("%Y-%m-%d"), "name": "平安银行",
             "market": "CN", "dt": "2026-10-13"},
            {"code": "300750.SZ", "type": "一卖", "detect_date":
             datetime.now().strftime("%Y-%m-%d"), "name": "宁德时代",
             "market": "CN", "dt": "2026-10-13"}]

    def _write_config(self, cfg):
        _write_json(cross_poll.CONFIG_FP, cfg)

    def _write_fixtures(self, ts):
        _write_json(os.path.join(self.reports, "rps_latest.json"), {
            "ts": ts, "res": [
                _rps_row("600519.SH", "贵州茅台", longs=4),
                _rps_row("000001.SZ", "平安银行", longs=4),
                _rps_row("300750.SZ", "宁德时代", longs=1)]})
        _write_json(os.path.join(self.data, "nine_dim_scores.json"), {
            "generated_at": ts,
            "trade_date": datetime.now().strftime("%Y%m%d"),
            "rows": [{"code": "600519.SH", "name": "贵州茅台",
                      "score": 85.0, "cov": 0.9}]})

    def tearDown(self):
        (cross_poll.REPORTS, cross_poll.DATA, cross_poll._CACHE,
         cross_poll._src_chanlun_load, cross_poll.CONFIG_FP) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestNetScoreCalc(_Base):
    """路径 1：净向分计算（9dim 反向 / 归一化 / 排序翻转）。"""

    def test_net_score_values_and_9dim_reverse(self):
        """四源齐整：9dim 命中按 -0.5 计入（方向取反×权重 0.5）。"""
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["sort_key"], "net")
        a, b, c = (_st(d, "600519.SH"), _st(d, "000001.SZ"),
                   _st(d, "300750.SZ"))
        # 9dim 反指：a 四源全命中（含 9dim long）反被拖到 2.5/3.5
        self.assertAlmostEqual(a["net_score"], round(
            (W_Q + W_S + W_C - 0.5) / (W_Q + W_S + W_C + W_9), 4))
        # b 三源多头无 9dim → 满分口径 3/3.5
        self.assertAlmostEqual(b["net_score"], round(
            (W_Q + W_S + W_C) / (W_Q + W_S + W_C + W_9), 4))
        # c 冲突票：+1(qpzm) + 0(sig4 neutral) + -1(chanlun 卖) = 0
        self.assertAlmostEqual(c["net_score"], 0.0)

    def test_net_ordering_flips_top1_vs_raw(self):
        """排序翻转：raw 头名 600519（4 源命中）在 net 下让位 000001
        （9dim 反指拖累）——排序键切换真实生效而非恒等变换。"""
        d = cross_poll.build_cross(force=True)
        codes_net = [s["code"] for s in d["topn"]]
        self.assertEqual(codes_net[0], "000001.SZ")
        self.assertEqual(codes_net[1], "600519.SH")
        # 同一聚合下 raw 排序应为旧口径（-n_hit,-n_long,code）
        raw = sorted(d["stocks"], key=lambda x: (-x["n_hit"],
                                                 -x["n_long"], x["code"]))
        self.assertEqual([s["code"] for s in raw][:2],
                         ["600519.SH", "000001.SZ"])
        self.assertNotEqual([s["code"] for s in d["topn"]][:2],
                            [s["code"] for s in raw][:2])

    def test_missing_source_normalized(self):
        """单源缺失（chanlun 池空 → missing）：分子分母同步剔除并归一化，
        a=(1+1-0.5)/2.5=0.6；且 degraded_k3 共存、k 门槛不动。"""
        cross_poll._src_chanlun_load = lambda: []      # chanlun 缺席
        d = cross_poll.build_cross(force=True)
        a = _st(d, "600519.SH")
        den = W_Q + W_S + W_9                          # 2.5
        self.assertAlmostEqual(a["net_score"],
                               round((W_Q + W_S - 0.5) / den, 4))
        self.assertTrue(d["degraded"])
        self.assertEqual(d["k_gate"]["mode"], "degraded_k3")
        self.assertEqual(d["k_gate"]["k"], 3)          # 门槛不因排序而变
        self.assertTrue(all(s["n_hit"] >= 3 for s in d["topn"]))

    def test_extreme_all_sources_missing_zero(self):
        """极端：可用源权重和为 0（全缺失）→ net_score=0.0 不除零。"""
        os.remove(os.path.join(self.reports, "rps_latest.json"))
        os.remove(os.path.join(self.data, "nine_dim_scores.json"))
        cross_poll._src_chanlun_load = lambda: []
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["k_gate"]["mode"], "blocked")
        self.assertEqual(d["topn"], [])                # blocked 产空
        for s in d["stocks"]:
            self.assertEqual(s["net_score"], 0.0)


class TestRawFallback(_Base):
    """路径 2：开关 raw 一键回退旧行为。"""

    def test_raw_config_restores_legacy_order(self):
        """sort_key=raw → 旧长票计数排序、无 net_score 字段（零残留）。"""
        self._write_config({"cross": {"source_health_alert": False,
                                      "sort_key": "raw"}})
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["sort_key"], "raw")
        raw = sorted(d["stocks"], key=lambda x: (-x["n_hit"],
                                                 -x["n_long"], x["code"]))
        self.assertEqual([s["code"] for s in d["stocks"]],
                         [s["code"] for s in raw])
        self.assertEqual([s["code"] for s in d["topn"]],
                         [s["code"] for s in raw][:cross_poll.TOPN_DEFAULT])
        self.assertFalse(any("net_score" in s for s in d["stocks"]))

    def test_missing_sort_key_defaults_net(self):
        """config 无 sort_key → 缺省 net（W5 直上口径）。"""
        self._write_config({"cross": {"source_health_alert": False}})
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["sort_key"], "net")
        self.assertTrue(any("net_score" in s for s in d["stocks"]))

    def test_invalid_sort_key_falls_back_net_no_crash(self):
        """非法 sort_key 值 → 按缺省 net 处理，不炸。"""
        self._write_config({"cross": {"sort_key": "whatever"}})
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["sort_key"], "net")


class TestResilienceCoexist(_Base):
    """路径 3：与韧性改造（src_state/degraded/source_health）共存。"""

    def test_source_error_degrades_with_net_sort(self):
        """单源 loader 抛异常（error）→ 净向分按缺失源归一化继续出。"""
        cross_poll._src_chanlun_load = lambda: (_ for _ in ()).throw(
            RuntimeError("chanlun 库炸了"))
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["src_state"]["chanlun"]["status"], "error")
        self.assertTrue(d["degraded"])
        a = _st(d, "600519.SH")
        self.assertAlmostEqual(a["net_score"],
                               round((W_Q + W_S - 0.5) / (W_Q + W_S + W_9),
                                     4))
        # k=3 门槛不动：000001/300750 只剩 2 源命中被挡，a（3 源）居首
        self.assertEqual([s["code"] for s in d["topn"]][0], "600519.SH")
        self.assertNotIn("000001.SZ", [s["code"] for s in d["topn"]])

    def test_blocked_empty_topn_with_net(self):
        """两源齐灭（blocked）→ topn 产空，sort_key 标注仍在。"""
        os.remove(os.path.join(self.reports, "rps_latest.json"))
        d = cross_poll.build_cross(force=True)
        self.assertEqual(d["k_gate"]["mode"], "blocked")
        self.assertEqual(d["topn"], [])
        self.assertEqual(d["sort_key"], "net")

    def test_tier_inputs_untouched(self):
        """分级阈值口径不动：k 门槛（n_hit≥3）与 n_long 计数不受净向分
        影响（9dim 反向只进排序键，不改变 n_hit/n_long 原始计数）。"""
        d = cross_poll.build_cross(force=True)
        a = _st(d, "600519.SH")
        self.assertEqual(a["n_hit"], 4)      # 9dim 仍计一次 hit
        self.assertEqual(a["n_long"], 4)     # 9dim 仍计一次 long（原始计数）
        self.assertTrue(all(s["n_hit"] >= cross_poll.K_RES
                            for s in d["topn"]))

    def test_health_summary_still_works(self):
        """source_health / health_summary_line 与排序改造互不干扰。"""
        h = cross_poll.source_health()
        self.assertEqual(h["9dim"]["status"], "ok")
        line = cross_poll.health_summary_line(h)
        self.assertIn("9dim ok", line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
