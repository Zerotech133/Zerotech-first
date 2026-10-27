# -*- coding: utf-8 -*-
"""K57 价格保护风控三规则单测（unittest · 合成账本 + mock 价格，零文件副作用）。

覆盖：
T1 初始止损：px ≤ entry×0.98 → ("初始止损(-2%)", rebuild=False)
T2 移动止盈：hi_px 回撤 ≥5% → ("移动止盈(回撤5%)", rebuild=False)；含边界
T3 rps骤降：RPS_mix 单日降 ≥15 → ("rps骤降", rebuild=True)；边界 15/14.5
T4 多规则同日取最严：初始止损 > 移动止盈 > rps骤降，只出一条判定
T5 hi_px / last_rps 老仓回填兼容：缺字段首遇只初始化不误杀
T6 正常持有不误杀
T7 现有规则行为不变：信号翻空 reason/rebuild 原样；环境闸门仍先于新规则；
   源快照无价时不判价格规则
T8 _run_exits 集成：一条退出记录落账、rebuild 语义、exits 文案

隔离：全部用合成持仓 dict + mock 的 px_map/rps_map/CTXS/_envs_safe，
不读写 reports/ 与 data/ 真实文件，零网络零推送。
"""

import os
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import portfolio as pf  # noqa: E402


def _pos(**kw):
    """合成持仓 dict（字段与引擎 open[] 实际结构一致）。"""
    p = {
        "market": "CN",
        "code": "TEST.SZ",
        "name": "测试标的",
        "qty": 1000,
        "entry_px": 10.0,
        "entry_date": "2026-10-05",
        "cost": 10000.0,
        "last_px": 10.0,
        "last_date": "2026-10-05",
        "note": "t",
        "hi_px": 10.0,
        "hold_days": 0,
        "out_days": 0,
    }
    p.update(kw)
    return p


CFG = {
    "gate_bad_max": 5,
    "out_max": 3,
    "hold_max": 20,
    "stop_loss_pct": 8,
    "take_profit_pct": 15,
    "be_trigger_pct": 8,
    "be_floor_pct": 0.5,
}


def _mk_led_ac(positions, last_snap="20261005"):
    ac = {
        "name": "青萍之末",
        "icon": "🌊",
        "capital": 1_000_000,
        "cash": 900_000.0,
        "shares": 1_000_000,
        "open": positions,
        "closed": [],
        "nav_curve": [],
        "skip_log": [],
        "adjust_log": [],
        "last_snap": last_snap,
    }
    return {"gate_days": {}, "accounts": {"qpzm": ac}}, ac


def _run(led, ac, pxm, rps_map=None, action_map=None, today="2026-10-06"):
    ctx = {"snap_date": "20261006", "px_map": pxm, "action_map": action_map or {}}
    with (
        mock.patch.object(pf, "CTXS", {"qpzm": lambda: ctx}),
        mock.patch.object(pf, "_envs_safe", lambda: {}),
        mock.patch.object(pf, "_rps_score_map", lambda: rps_map or {}),
    ):
        exits, _ = pf._run_exits(led, ac, "qpzm", today, CFG)
    return exits


class TestRiskExitOf(unittest.TestCase):
    """单仓判定函数 _risk_exit_of 的规则级单测。"""

    def test_t1_initial_stop(self):
        """T1 初始止损：px ≤ entry×0.98 触发，rebuild=False，含文案。"""
        hit = pf._risk_exit_of(_pos(), {"TEST.SZ": 9.79}, {})
        self.assertIsNotNone(hit)
        reason, rebuild = hit
        self.assertEqual(reason, "初始止损(-2%)")  # 规格定名
        self.assertFalse(rebuild)  # 价格保护不重建
        # 未到阈值不触发：-1.5% 安全
        self.assertIsNone(pf._risk_exit_of(_pos(), {"TEST.SZ": 9.85}, {}))

    def test_t2_trailing_stop(self):
        """T2 移动止盈：自 hi_px 回撤 ≥5% 触发；边界外不触发。"""
        p2 = _pos(entry_px=10.0, hi_px=20.0)
        hit2 = pf._risk_exit_of(p2, {"TEST.SZ": 18.99}, {})
        self.assertEqual(hit2, ("移动止盈(回撤5%)", False))
        # 回撤恰好不足 5%（19.01 > 20×0.95=19.0）→ 不触发
        self.assertIsNone(
            pf._risk_exit_of(_pos(entry_px=10.0, hi_px=20.0), {"TEST.SZ": 19.01}, {})
        )
        # 阈值边界恰好相等：px == hi×0.95 → 触发（回撤 ≥5% 口径）
        self.assertEqual(
            pf._risk_exit_of(_pos(entry_px=10.0, hi_px=20.0), {"TEST.SZ": 19.0}, {}),
            ("移动止盈(回撤5%)", False),
        )

    def test_t3_rps_drop(self):
        """T3 rps骤降：单日降 ≥15 触发（rebuild=True）；基线每日刷新。"""
        p = _pos(last_rps=90.0)
        self.assertEqual(
            pf._risk_exit_of(p, {"TEST.SZ": 10.5}, {"TEST.SZ": 75.0}), ("rps骤降", True)
        )
        # 边界：降 14.5 < 15 不触发，但基线已刷新为 75.5
        p2 = _pos(last_rps=90.0)
        self.assertIsNone(pf._risk_exit_of(p2, {"TEST.SZ": 10.5}, {"TEST.SZ": 75.5}))
        self.assertEqual(p2["last_rps"], 75.5)
        # res[] 无该标的（score 缺失）→ 不判不动基线
        p3 = _pos(last_rps=90.0)
        self.assertIsNone(pf._risk_exit_of(p3, {"TEST.SZ": 10.5}, {}))
        self.assertEqual(p3["last_rps"], 90.0)

    def test_t4_priority_strictest_wins(self):
        """T4 多规则同日取最严：止损 > 止盈 > rps骤降，单条判定。"""
        # 止损+止盈+rps 三规则同日全中 → 只返回初始止损
        p = _pos(hi_px=10.0, last_rps=90.0)
        hit = pf._risk_exit_of(p, {"TEST.SZ": 9.5}, {"TEST.SZ": 70.0})
        self.assertEqual(hit, ("初始止损(-2%)", False))
        # 止盈+rps 同日双中（价格未破止损线）→ 只返回移动止盈
        p2 = _pos(entry_px=10.0, hi_px=12.0, last_rps=90.0)
        hit2 = pf._risk_exit_of(p2, {"TEST.SZ": 11.39}, {"TEST.SZ": 70.0})
        self.assertEqual(hit2, ("移动止盈(回撤5%)", False))

    def test_t5_legacy_backfill_compat(self):
        """T5 老仓缺 hi_px/last_rps：首遇只初始化不误杀（账本健壮性）。"""
        # 无 hi_px：hi 兜底 = max(entry, 现价)=10 → 阈值 9.5，px 9.85 不触发
        p = _pos()
        del p["hi_px"]
        self.assertIsNone(pf._risk_exit_of(p, {"TEST.SZ": 9.85}, {}))
        # 无 last_rps：当日只初始化基线不触发（防上线首日误杀）
        p2 = _pos()
        self.assertIsNone(pf._risk_exit_of(p2, {"TEST.SZ": 10.5}, {"TEST.SZ": 40.0}))
        self.assertEqual(p2["last_rps"], 40.0)
        # 次日再降 15 分位 → 正常触发
        self.assertEqual(
            pf._risk_exit_of(p2, {"TEST.SZ": 10.4}, {"TEST.SZ": 25.0}),
            ("rps骤降", True),
        )

    def test_t6_normal_hold_no_kill(self):
        """T6 正常持有：价格稳、RPS 稳 → 不误杀。"""
        p = _pos(hi_px=10.6, last_rps=80.0)
        self.assertIsNone(pf._risk_exit_of(p, {"TEST.SZ": 10.5}, {"TEST.SZ": 79.0}))
        self.assertEqual(p["last_rps"], 79.0)

    def test_t7_no_px_skips_price_rules(self):
        """T7a 源快照无价（跌出 px_map）：不判价格规则，RPS 照常。"""
        p = _pos(code="GONE.SZ", hi_px=20.0, last_rps=90.0)
        self.assertIsNone(pf._risk_exit_of(p, {}, {"GONE.SZ": 80.0}))
        self.assertEqual(p["last_rps"], 80.0)


class TestRunExitsIntegration(unittest.TestCase):
    """_run_exits 集成：退出记录落账 / rebuild 语义 / 现有规则不变。"""

    def test_t8_stop_exit_one_record(self):
        """T8a 止损集成：一条退出记录，reason/rebuild 落账正确。"""
        led, ac = _mk_led_ac([_pos(), _pos(code="OK.SZ", name="平安仓")])
        exits = _run(led, ac, {"TEST.SZ": 9.5, "OK.SZ": 10.2})
        self.assertEqual(len(exits), 1)
        self.assertIn("初始止损(-2%)", exits[0])
        self.assertEqual(len(ac["closed"]), 1)
        rec = ac["closed"][0]
        self.assertEqual(rec["reason"], "初始止损(-2%)")
        self.assertFalse(rec.get("rebuild"))  # 价格保护不重建
        self.assertAlmostEqual(rec["exit_px"], 9.5)
        self.assertEqual([p["code"] for p in ac["open"]], ["OK.SZ"])

    def test_t8b_trailing_exit(self):
        """T8b 移动止盈集成：高浮盈仓回撤 5% 平仓，不重建。"""
        led, ac = _mk_led_ac([_pos(entry_px=10.0, hi_px=20.0)])
        exits = _run(led, ac, {"TEST.SZ": 18.99})
        self.assertEqual(len(ac["closed"]), 1)
        rec = ac["closed"][0]
        self.assertEqual(rec["reason"], "移动止盈(回撤5%)")
        self.assertFalse(rec.get("rebuild"))
        self.assertGreater(rec["exit_pnl_pct"], 0)

    def test_t8c_rps_exit_rebuild(self):
        """T8c rps骤降集成：环境预警类 rebuild=True（随闸门语义）。"""
        led, ac = _mk_led_ac([_pos(last_rps=90.0)])
        exits = _run(led, ac, {"TEST.SZ": 10.5}, rps_map={"TEST.SZ": 74.0})
        self.assertEqual(len(ac["closed"]), 1)
        rec = ac["closed"][0]
        self.assertEqual(rec["reason"], "rps骤降")
        self.assertTrue(rec.get("rebuild"))

    def test_t8d_multi_rule_same_day_one_record(self):
        """T8d 同日止损+止盈+rps全中：只出一条初始止损记录。"""
        led, ac = _mk_led_ac([_pos(hi_px=10.0, last_rps=90.0)])
        exits = _run(led, ac, {"TEST.SZ": 9.5}, rps_map={"TEST.SZ": 70.0})
        self.assertEqual(len(exits), 1)
        self.assertEqual(len(ac["closed"]), 1)
        self.assertEqual(ac["closed"][0]["reason"], "初始止损(-2%)")

    def test_t9_signal_flip_unchanged(self):
        """T9 现有信号翻空行为不变：reason=信号翻空、无 rebuild 标记。"""
        led, ac = _mk_led_ac([_pos()])
        exits = _run(led, ac, {"TEST.SZ": 10.5}, action_map={"TEST.SZ": "回避"})
        self.assertEqual(len(ac["closed"]), 1)
        rec = ac["closed"][0]
        self.assertEqual(rec["reason"], "信号翻空")
        self.assertNotIn("rebuild", rec)

    def test_t10_gate_beats_new_rules(self):
        """T10 环境闸门仍先于新规则（市场级紧急优先，行为不改变）。"""
        led, ac = _mk_led_ac([_pos(hi_px=10.0, last_rps=90.0)])
        ac["gate_mark"] = "2026-10-06"  # 预置计数：闸门已连续收紧 5 日
        led["gate_days"] = {"CN": 5}
        exits = _run(led, ac, {"TEST.SZ": 9.5}, rps_map={"TEST.SZ": 70.0})
        self.assertEqual(len(ac["closed"]), 1)
        rec = ac["closed"][0]
        self.assertTrue(rec["reason"].startswith("环境闸门"))
        self.assertTrue(rec.get("rebuild"))

    def test_t11_survivor_refreshed(self):
        """T11 存活仓基线推进：last_rps/hold_days 正常刷新，不误杀。"""
        led, ac = _mk_led_ac([_pos(hi_px=10.6, last_rps=80.0)])
        _run(led, ac, {"TEST.SZ": 10.5}, rps_map={"TEST.SZ": 79.0})
        self.assertEqual(len(ac["open"]), 1)
        p = ac["open"][0]
        self.assertEqual(p["last_rps"], 79.0)
        self.assertEqual(p["hold_days"], 1)  # new_day 推进
        self.assertEqual(len(ac["closed"]), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
