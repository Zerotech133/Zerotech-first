#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""jev 毕业判据 v2 单元测试（W5c，2026-10-14）。

只测纯函数 judge_domain / overall_light，全部用合成数据，不读真实数据源。
运行：  "C:/Program Files/Python311/python.exe" test_jev_graduation_v2.py
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))
from jev_graduation_status import judge_domain, overall_light  # noqa: E402


class TestJudgeDomain(unittest.TestCase):
    def test_us_double_pass(self):
        """US 双过：n_eff 与均值同时达标 → 该域达标。"""
        r = judge_domain(35, 0.02, 0.005, "US")
        self.assertTrue(r["pass"])
        self.assertEqual(r["state"], "达标")

    def test_us_n_only_pass(self):
        """US 单过（n 够、均值不够）→ 未毕业，state=均值未过线。"""
        r = judge_domain(44, -0.0086, 0.0155, "US")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "均值未过线")

    def test_mean_only_pass(self):
        """均值够、n 不够 → 未毕业，state=样本不足。"""
        r = judge_domain(20, 0.03, 0.01, "US")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "样本不足")

    def test_hk_n_insufficient(self):
        """HK n 不足（n_eff=14 < 30）→ 未毕业（现实口径：均值同负，双未达标）。"""
        r = judge_domain(14, -0.0086, 0.0240, "HK")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "n与均值均未达标")
        # 若 HK 均值已转正而 n 仍不足 → 样本不足
        r = judge_domain(14, 0.02, 0.0240, "HK")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "样本不足")

    def test_both_fail(self):
        """n 与均值均不达标。"""
        r = judge_domain(10, -0.02, -0.01, "HK")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "n与均值均未达标")

    def test_crypto_transitional_excluded(self):
        """CRYPTO 过渡期剔除：无论指标多好都 pass=False。"""
        for n, m, g in [(6, -0.0244, -0.0170), (100, 0.05, 0.02)]:
            r = judge_domain(n, m, g, "CRYPTO")
            self.assertFalse(r["pass"])
            self.assertEqual(r["state"], "过渡期剔除")

    def test_cn_entry_missing(self):
        """CN 影子组（2026-10-16 P1-1）：FOCUS_CN 积累期状态与转正纪律。
        n_eff=0 → 影子积累中；达标 → 影子达标（待转正）；pass 恒 False。"""
        r = judge_domain(0, None, None, "CN")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "影子积累中")
        r2 = judge_domain(35, 0.02, 0.01, "CN")
        self.assertFalse(r2["pass"])  # 影子组不自动毕业，转正需人工
        self.assertEqual(r2["state"], "影子达标（待转正）")
        r3 = judge_domain(35, -0.005, 0.0, "CN")
        self.assertFalse(r3["pass"])
        self.assertEqual(r3["state"], "影子未达标")

    def test_boundary_n_eff_30(self):
        """边界：n_eff=30 恰好达标（≥30）。"""
        r = judge_domain(30, 0.02, 0.0, "US")
        self.assertTrue(r["pass"])
        r = judge_domain(29, 0.02, 0.0, "US")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "样本不足")

    def test_boundary_mean_exact_1pp(self):
        """边界：均值恰为 +1.0pp 不达标（须严格 >+1pp）。"""
        r = judge_domain(30, 0.01, 0.005, "US")
        self.assertFalse(r["pass"])
        self.assertEqual(r["state"], "均值未过线")
        r = judge_domain(30, 0.0101, 0.005, "US")
        self.assertTrue(r["pass"])

    def test_gap_is_aux_only(self):
        """辅助指标 FOCUS−PASS>0 无硬门槛：负 gap 不改变判定。"""
        r = judge_domain(35, 0.02, -0.01, "US")
        self.assertTrue(r["pass"])
        self.assertIn("✗", r["note"])


class TestOverallLight(unittest.TestCase):
    DOM = {"n_eff": 10, "cluster_mean_pp": -0.5, "focus_pass_gap_pp": None, "pass": False}

    def _dom(self, **kw):
        d = dict(self.DOM)
        d.update(kw)
        return d

    def test_green_when_any_live_domain_pass(self):
        doms = {"US": self._dom(**{"pass": True}), "HK": self._dom(focus_pass_gap_pp=2.4)}
        self.assertEqual(overall_light(doms), "green")

    def test_yellow_when_no_pass_but_positive_gap(self):
        doms = {"US": self._dom(focus_pass_gap_pp=1.55),
                "HK": self._dom(focus_pass_gap_pp=-1.0)}
        self.assertEqual(overall_light(doms), "yellow")

    def test_yellow_counts_excluded_domain_gap(self):
        """CRYPTO 过渡剔除但其 gap>0 也计入黄灯信号（监控值）。"""
        doms = {"CRYPTO": self._dom(focus_pass_gap_pp=0.5)}
        self.assertEqual(overall_light(doms), "yellow")

    def test_red_when_all_negative(self):
        doms = {"US": self._dom(focus_pass_gap_pp=-1.5),
                "HK": self._dom(focus_pass_gap_pp=None),
                "CN": self._dom(focus_pass_gap_pp=None)}
        self.assertEqual(overall_light(doms), "red")

    def test_green_beats_yellow(self):
        doms = {"US": self._dom(**{"pass": True}, focus_pass_gap_pp=-1.0),
                "HK": self._dom(focus_pass_gap_pp=2.0)}
        self.assertEqual(overall_light(doms), "green")


if __name__ == "__main__":
    unittest.main(verbosity=2)
