# -*- coding: utf-8 -*-
"""[20261021_wo-qt-087] 量化交易板块入口 /quant-entry 渲染层单元测试。

覆盖（老田 10-21 收敛口径 + WO-078 范式）：
· 四市场总表六列（市场/今日多空/策略货架/股票池/Agent 席/进入）
· 多空灯双源合成（拍板默认②）：regime × 青萍 env_gate
    - 双源一致 → 亮灯（偏空=down 绿 / 偏多=up 红，A股惯例涨红跌绿）
    - 双源相反 → warn「多空分歧」；单源 → warn「单源待判」；无源 → off「待判」
· stale 判定（快照 trade_date 超 3 个自然日 → 标注）
· 缺源显式标「待接」不造数（全空 ctx 不出假数字）
· 席位门户：真实在役路由链接（/pool-watch /strategies /bt-positions）；
  未实跑/待建席无链接（虚线样式）
· 影子期红线常驻（不入 combo + WO-070/078/083 + D14=11-04）
· 纯渲染确定性（同 ctx + 固定 now 两次渲染逐字节一致）
· 集成冒烟：读真实盘出页（文件缺失才 skip，不吞异常假绿）

纪律：主体用例全部注入合成 ctx，不触网、零真实行情；
集成冒烟只读盘上产物断言稳定结构（市场名/路由/红线文案），
不断言易变数值——数值承重由合成 ctx 用例承担。

运行：仓根 pytest quant_backtest/tests/test_quant_entry_page.py -v
"""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))        # quant_backtest/tests
QBT = os.path.dirname(HERE)                              # quant_backtest
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import quant_entry_page as qep                          # noqa: E402

CST = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 21, 14, 30, 0, tzinfo=CST)

_PICKS = {
    "scan_run_date": "2026-10-19",
    "counts": {"total": 390, "T1_resonance": 0, "T2_high_value": 14,
               "T3_single": 376},
    "strategies": {"qpzm": {"label": "青萍之末", "n": 25,
                            "env_gate": {"safe": False, "state": "偏冷",
                                         "score": 20, "breadth": 0.172}}},
}

_STRATS = ([{"id": "s%d" % i, "status": "live"} for i in range(6)]
           + [{"id": "s%d" % i, "status": "shadow"} for i in range(3)]
           + [{"id": "s%d" % i, "status": "tool"} for i in range(4)])

_REGIME = {"regime": "risk_off", "trade_date": "20261016",
           "breadth": {"available": True, "down_count": 2128},
           "index": {}, "gates": {}}

_XUNYOU = {"pool": [{"symbol": "603057.SH", "tier": "T1", "action": "buy",
                     "score": 100.0}] * 20
                   + [{"symbol": "x%d" % i, "tier": "T2"} for i in range(7)]
                   + [{"symbol": "y%d" % i} for i in range(49)],
           "cutover_verdict": "BLOCK_CUTOVER", "generated_at": "2026-10-20"}

_US = {"rows": 183, "codes": 150, "mtime": datetime(2026, 10, 21, 9, 13)}
_CRYPTO = {"n": 37, "btc_mtime": datetime(2026, 10, 21, 9, 0)}


def _ctx(**over):
    base = {"picks": _PICKS, "strategies": _STRATS, "regime": _REGIME,
            "xunyou": _XUNYOU, "us_pool": _US, "crypto": _CRYPTO}
    base.update(over)
    return base


class TestStanceLamp(unittest.TestCase):
    """多空灯双源合成（纯函数单元，业务规则分支）。"""

    def test_both_sources_bear_lights_green(self):
        cls, label, note = qep.stance_lamp(_REGIME, _PICKS)
        self.assertEqual(cls, "down")          # A股惯例：偏空=绿
        self.assertEqual(label, "偏空")
        self.assertIn("双源一致", note)

    def test_both_sources_bull_lights_red(self):
        regime = {"regime": "risk_on", "trade_date": "20261021"}
        picks = {"strategies": {"qpzm": {"env_gate": {"score": 80}}}}
        cls, label, note = qep.stance_lamp(regime, picks)
        self.assertEqual(cls, "up")            # A股惯例：偏多=红
        self.assertEqual(label, "偏多")
        self.assertIn("双源一致", note)

    def test_divergence_lights_amber(self):
        regime = {"regime": "risk_off", "trade_date": "20261021"}
        picks = {"strategies": {"qpzm": {"env_gate": {"score": 80}}}}
        cls, label, note = qep.stance_lamp(regime, picks)
        self.assertEqual(cls, "warn")
        self.assertEqual(label, "多空分歧")
        self.assertIn("双源相反", note)

    def test_neutral_source_lights_amber(self):
        regime = {"regime": "risk_off", "trade_date": "20261021"}
        picks = {"strategies": {"qpzm": {"env_gate": {"score": 45}}}}
        cls, label, note = qep.stance_lamp(regime, picks)
        self.assertEqual(cls, "warn")
        self.assertIn("中性", note)

    def test_single_source_lights_amber(self):
        cls, label, note = qep.stance_lamp(_REGIME, {})
        self.assertEqual(cls, "warn")
        self.assertEqual(label, "单源待判")
        self.assertIn("另一源无数据", note)

    def test_no_source_stays_off(self):
        cls, label, note = qep.stance_lamp(None, {})
        self.assertEqual(cls, "off")
        self.assertEqual(label, "待判")
        self.assertIn("灯不亮不造数", note)

    def test_qpzm_env_missing_gate_is_no_source(self):
        # qpzm 在但 env_gate 缺失 → 青萍无源（不猜）
        self.assertIsNone(qep._qpzm_env({"strategies": {"qpzm": {"n": 25}}}))


class TestRegimeStale(unittest.TestCase):

    def test_old_snapshot_is_stale(self):
        self.assertTrue(qep.regime_stale(_REGIME, NOW))

    def test_fresh_snapshot_not_stale(self):
        snap = {"regime": "risk_off", "trade_date": "20261021"}
        self.assertFalse(qep.regime_stale(snap, NOW))

    def test_missing_snapshot_is_stale(self):
        self.assertTrue(qep.regime_stale(None, NOW))


class TestRenderTable(unittest.TestCase):

    def setUp(self):
        self.html = qep.render_html(_ctx(), now=NOW)

    def test_four_markets_present(self):
        for mk in ("A股", "美股", "港股", "虚拟币"):
            self.assertIn(mk, self.html)

    def test_headers_six_columns(self):
        for h in ("市场", "今日多空", "策略货架", "股票池", "Agent 席"):
            self.assertIn(h, self.html)

    def test_stance_lamp_rendered_with_stale_note(self):
        self.assertIn("偏空", self.html)
        self.assertIn("双源一致", self.html)
        self.assertIn("stale", self.html)

    def test_pool_cell_counts(self):
        self.assertIn("390", self.html)
        self.assertIn("T2 高价值 14", self.html)
        self.assertIn("76", self.html)          # 荀攸研究池
        self.assertIn("BLOCK_CUTOVER", self.html)

    def test_strategy_distribution(self):
        self.assertIn("13 条上架", self.html)
        self.assertIn("6 live", self.html)
        self.assertIn("3 shadow", self.html)

    def test_us_pool_stats(self):
        self.assertIn("183 行", self.html)
        self.assertIn("150 标的", self.html)

    def test_crypto_stats(self):
        self.assertIn("37 标的", self.html)

    def test_appendix_commodity_fx_pending(self):
        self.assertIn("大宗交易", self.html)
        self.assertIn("外汇", self.html)
        self.assertIn("0 条目", self.html)


class TestMissingSourcesMarkedPending(unittest.TestCase):
    """缺源显式标「待接」不造数（承重用例）。"""

    def setUp(self):
        self.html = qep.render_html(_ctx(picks={}, strategies=[],
                                         regime=None, xunyou=None,
                                         us_pool=None, crypto=None),
                                    now=NOW)

    def test_pool_and_shelf_marked_pending(self):
        self.assertIn("待接", self.html)
        self.assertIn("todays_picks.json 不可用", self.html)
        self.assertIn("strategies.json 不可用", self.html)

    def test_no_fabricated_numbers(self):
        # 全空 ctx：不得出现池/货架/美股池/币市的实盘数字
        for fake in ("390", "13 条上架", "183 行", "37 标的"):
            self.assertNotIn(fake, self.html)

    def test_stance_off_not_fabricated(self):
        self.assertIn("待判", self.html)
        self.assertIn("双源均无数据", self.html)


class TestSeatGateways(unittest.TestCase):

    def setUp(self):
        self.html = qep.render_html(_ctx(), now=NOW)

    def test_live_route_links(self):
        for href in ('/pool-watch', '/strategies', '/bt-positions'):
            self.assertIn('href="%s"' % href, self.html)

    def test_unbuilt_seats_have_no_links(self):
        # 订票/投审/复盘：无链接（如实标注未实跑/待建）
        self.assertIn("待建（阶段二）", self.html)
        self.assertIn("v2 账本未落盘", self.html)

    def test_exit_routes_present(self):
        for r in ('/portal', '/intel', '/org', '/ops-board'):
            self.assertIn('href="%s"' % r, self.html)


class TestShadowRedline(unittest.TestCase):

    def test_redline_banner_constant(self):
        html = qep.render_html(_ctx(), now=NOW)
        self.assertIn("影子期红线（常驻）", html)
        self.assertIn("WO-070/078/083", html)
        self.assertIn("D14", html)
        self.assertIn("2026-11-04", html)

    def test_d7_d14_anchors_on_page(self):
        html = qep.render_html(_ctx(), now=NOW)
        self.assertIn("10-28", html)
        self.assertIn("11-04", html)


class TestPureRender(unittest.TestCase):

    def test_deterministic_same_input(self):
        a = qep.render_html(_ctx(), now=NOW)
        b = qep.render_html(_ctx(), now=NOW)
        self.assertEqual(a, b)

    def test_module_has_no_write_in_render_layer(self):
        # 纯渲染层零写：render_html 源码内不得出现 open( 写模式
        import inspect
        src = inspect.getsource(qep.render_html)
        self.assertNotIn('open(', src)


class TestRealDiskSmoke(unittest.TestCase):
    """集成冒烟：读真实盘出页（只断言稳定结构，数值由合成用例承重）。"""

    @unittest.skipUnless(os.path.exists(qep.PICKS_PATH),
                         "todays_picks.json 不在盘（非 CI 常态）→ 显式 skip")
    def test_render_default_real_disk(self):
        html = qep.render_default(now=NOW)
        for mk in ("A股", "美股", "港股", "虚拟币"):
            self.assertIn(mk, html)
        for href in ('/pool-watch', '/strategies', '/bt-positions'):
            self.assertIn('href="%s"' % href, html)
        self.assertIn("影子期红线（常驻）", html)
        self.assertGreater(len(html), 3000)


class TestCryptoFreshnessRendering(unittest.TestCase):
    """WO-094：币市停更必须看得见——stale 标注与数据时点口径。

    背景：CryptoSync 直连已停用，本地 parquet 改由供给层导出维护；
    若不再导出则停更，此时若仍只显示文件 mtime，旧行情会被当实时读。
    """

    def _html(self, crypto):
        return qep.render_html(
            _ctx(picks=_PICKS, strategies=[], regime=None, xunyou=_XUNYOU,
                 us_pool=_US, crypto=crypto), now=NOW)

    def test_stale_marked(self):
        h = self._html({"n": 37, "btc_date": "20261001", "stale": True})
        self.assertIn("停更", h)

    def test_fresh_not_marked(self):
        h = self._html({"n": 37, "btc_date": "20261021", "stale": False})
        self.assertNotIn("停更", h)

    def test_shows_data_date_not_mtime(self):
        """展示口径必须是数据时点（btc_date），不是文件 mtime。"""
        h = self._html({"n": 37, "btc_date": "20261021", "stale": False,
                        "btc_mtime": None})
        self.assertIn("BTC_USDT 20261021", h)

    def test_missing_btc_date_shows_dash(self):
        h = self._html({"n": 37, "btc_date": None, "stale": True})
        self.assertIn("BTC_USDT -", h)
        self.assertIn("停更", h)


class TestCryptoStatsFreshness(unittest.TestCase):
    """crypto_stats 新鲜度判定（注入临时目录，不读真实 data/crypto）。"""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old = qep.CRYPTO_DIR
        qep.CRYPTO_DIR = self.tmp.name
        self.addCleanup(self._restore)

    def _restore(self):
        qep.CRYPTO_DIR = self.old

    def _mk(self, td):
        import pandas as pd
        pd.DataFrame({"trade_date": [td], "close": [1.0]}).to_parquet(
            os.path.join(self.tmp.name, "BTC_USDT.parquet"), index=False)

    def test_fresh_within_three_days(self):
        self._mk("20261021")
        s = qep.crypto_stats(now=NOW)
        self.assertFalse(s["stale"])
        self.assertEqual(s["btc_date"], "20261021")
        self.assertEqual(s["n"], 1)

    def test_stale_after_three_days(self):
        self._mk("20261001")
        self.assertTrue(qep.crypto_stats(now=NOW)["stale"])

    def test_boundary_exactly_three_days_is_fresh(self):
        self._mk("20261018")                    # NOW=10-21 → 差 3 天，不算 stale
        self.assertFalse(qep.crypto_stats(now=NOW)["stale"])

    def test_empty_dir_returns_none(self):
        self.assertIsNone(qep.crypto_stats(now=NOW))

    def test_missing_btc_file_marks_stale(self):
        """有别的 parquet 但缺 BTC → 数据时点未知 → 保守判 stale。"""
        import pandas as pd
        pd.DataFrame({"trade_date": ["20261021"], "close": [1.0]}).to_parquet(
            os.path.join(self.tmp.name, "ETH_USDT.parquet"), index=False)
        s = qep.crypto_stats(now=NOW)
        self.assertIsNotNone(s)
        self.assertTrue(s["stale"])
        self.assertIsNone(s["btc_date"])


class TestStatusTextMatchesExecutedState(unittest.TestCase):
    """文案必须与已执行动作一致——动作变了文案不改，就是新的假信息。

    · 港股扫描任务已于 WO-095（10-21 16:10）停用 → 不得再写「已现跑」
    · 币市源已由 WO-094 收编供给层、直连停用 → 不得再写「是否入供给层待裁」
    """

    def setUp(self):
        self.html = qep.render_html(
            _ctx(picks=_PICKS, strategies=[], regime=None, xunyou=_XUNYOU,
                 us_pool=_US, crypto=_CRYPTO), now=NOW)

    def test_hk_says_scan_stopped(self):
        self.assertIn("扫描已停", self.html)
        self.assertIn("WO-095", self.html)

    def test_hk_no_longer_claims_running(self):
        self.assertNotIn("已现跑零产出", self.html)

    def test_crypto_says_source_adopted(self):
        self.assertIn("已收编供给层", self.html)

    def test_crypto_no_stale_verdict_text(self):
        self.assertNotIn("币市源是否入情报供给层待裁", self.html)


if __name__ == "__main__":
    unittest.main()
