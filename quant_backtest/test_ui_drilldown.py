# -*- coding: utf-8 -*-
"""UI 下钻改造单元测试（unittest，零依赖）。

覆盖 2026-09-27 两轮 UI 改造的可测单元：
· report_page._stock_link / _strat_link 统一下钻链接（四市场代码归一）
· write_positions_html / write_signals_html 渲染（标的+策略可点、导航、图例）
· cross_poll.norm_code 代码归一化（watchlist 前缀 → tushare 基准）
· cross_poll.build_cross 聚合（方向语义三分/冲突检测/两两矩阵/缠论
  90 天窗口/注册表驱动/缓存 TTL）
· portal_api._matrix_cards 个股方法论矩阵（注册表策略卡/方向色/
  未命中 mute/HTML 转义/B 段本地卡锚点/新策略零改自动扩展）
· portal_api.pill_nav 导航分组（金融区今日/策略/跟踪三组、
  FIN_NAV_GROUPS 单一数据源、当前页高亮、其他板块平铺不回归）
· portal_api._fin_page 金融驾驶舱三段式（A 今日全景：闸门 g-on/off、
  机会 Top5 截断、盘前五维拼图（维度专属色块/正绿负红/板块正负最强/
  外围流动性摘要/离岸汇率方向/空数据降级）；B 策略动态：注册表
  sstat 卡 + 共振聚合卡；C 折叠收纳 3 区块。_read_json/_sa_hist_read/
  cross_poll 全 mock，零真实产物零网络）
· portal_api._cross_page 共振看板（四大区块/标的+徽标下钻/两两矩阵
  pair 下钻/市场筛选 tab/聚合器异常降级；cross_poll mock）
· portal_api._chanlun_pool_page 缠论预警池（代码→个股全景、类型徽标
  →样本面板、强预警横幅、侧面标签配色、模拟持仓、战绩表、空池占位；
  QBT_DIR 指向临时目录、run mock，零真实产物零子进程）
· portal_api._bt_stock_page 个股全景整页（①-⑥ 区块骨架、矩阵锚点
  #sig4/#jev/#ai 与区块 id 闭环、行情红绿/缺源占位、共振命中分解/
  不在自选池降级、4 策略表与矩阵同源集成、引擎无数据降级、JEV 卡片、
  未识别回表单；resolve/fetch_quote/runner/cross_poll/jev 全 mock）

纪律：不触网、不读真实产物（REPORTS/DATA/POSITIONS/SIGNALS 全部替换为
临时目录），跑完自动还原。

运行：cd quant_backtest && python -m unittest test_ui_drilldown -v
"""
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import report_page          # noqa: E402
import cross_poll           # noqa: E402


def _tmpfile(suffix=".html"):
    fd, p = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return p


class TestStockLink(unittest.TestCase):
    """_stock_link：四市场代码 → /bt/stock 个股全景下钻。"""

    def test_cn_strips_suffix(self):
        a = report_page._stock_link("贵州茅台", "600519.SH")
        self.assertIn('href="/kg/stock/600519"', a)
        self.assertIn("贵州茅台", a)

    def test_sz_strips_suffix(self):
        self.assertIn("/kg/stock/000001",
                      report_page._stock_link("平安银行", "000001.SZ"))

    def test_us_ticker(self):
        self.assertIn("/kg/stock/PLTR", report_page._stock_link("Palantir", "PLTR"))

    def test_hk(self):
        self.assertIn("/kg/stock/00700", report_page._stock_link("腾讯控股", "00700"))

    def test_crypto_urlencoded(self):
        self.assertIn("/kg/stock/BTC%2FUSDT",
                      report_page._stock_link("比特币", "BTC/USDT"))

    def test_empty_symbol_plain_text(self):
        self.assertEqual(report_page._stock_link("某股", ""), "某股")
        self.assertEqual(report_page._stock_link("某股", None), "某股")

    def test_name_html_escaped(self):
        self.assertIn("A&amp;B", report_page._stock_link("A&B", "PLTR"))

    def test_has_drilldown_hint(self):
        self.assertIn("个股全景", report_page._stock_link("x", "PLTR"))


class TestStratLink(unittest.TestCase):
    """_strat_link：策略 → /bt/strategy/<id> 纵切页下钻。"""

    def test_known_strategy(self):
        a = report_page._strat_link("ma_cross")
        self.assertIn('href="/bt/strategy/ma_cross"', a)
        self.assertIn("均线交叉", a)

    def test_unknown_falls_back_to_id(self):
        a = report_page._strat_link("no_such")
        self.assertIn('href="/bt/strategy/no_such"', a)
        self.assertIn("no_such", a)


class TestPositionsPage(unittest.TestCase):
    """持仓页：三张表标的+策略可点、导航与图例、空账本占位。"""

    def setUp(self):
        self._orig = report_page.POSITIONS
        self.path = _tmpfile()
        report_page.POSITIONS = self.path
        self.led = {
            "pending": [{"name": "贵州茅台", "market": "CN",
                         "strategy": "ma_cross", "sig_date": "2026-09-25",
                         "last_px": 1452.0, "symbol": "600519.SH"}],
            "open": [{"name": "Palantir", "market": "US",
                      "strategy": "trend_follow",
                      "entry_date": "2026-09-20", "entry_px": 60.0,
                      "last_px": 66.0, "pnl": 0.1, "days": 5,
                      "symbol": "PLTR",
                      "marks": [{"d": "2026-09-26", "px": 66.0,
                                 "pnl": 0.1}]}],
            "closed": [{"name": "腾讯控股", "market": "HK",
                        "strategy": "rsi_reversal",
                        "entry_date": "2026-09-01",
                        "exit_date": "2026-09-15", "entry_px": 400.0,
                        "exit_px": 420.0, "pnl": 0.05, "days": 14,
                        "symbol": "00700"}],
        }

    def tearDown(self):
        report_page.POSITIONS = self._orig
        if os.path.exists(self.path):
            os.remove(self.path)

    def test_renders_and_links(self):
        out = report_page.write_positions_html(self.led, "2026-09-27 12:00:00")
        self.assertTrue(os.path.exists(out))
        with open(out, encoding="utf-8") as f:
            h = f.read()
        for frag in ("/kg/stock/600519", "/kg/stock/PLTR",
                     "/kg/stock/00700",                 # 三表标的可点
                     "/bt/strategy/ma_cross",
                     "/bt/strategy/trend_follow",
                     "/bt/strategy/rsi_reversal",          # 策略可点
                     "/bt-cross",                          # 导航含共振入口
                     "个股全景"):                          # 图例说明下钻
            self.assertIn(frag, h)

    def test_empty_ledger_placeholder(self):
        out = report_page.write_positions_html(
            {"pending": [], "open": [], "closed": []}, "t")
        with open(out, encoding="utf-8") as f:
            self.assertIn("—", f.read())


class TestSignalsPage(unittest.TestCase):
    """信号页：标的可点、4 策略列头可点、旧格式行（无 symbol）不崩。"""

    def setUp(self):
        self._orig = report_page.SIGNALS
        self.path = _tmpfile()
        report_page.SIGNALS = self.path

    def tearDown(self):
        report_page.SIGNALS = self._orig
        if os.path.exists(self.path):
            os.remove(self.path)

    def _row(self, **kw):
        base = {"name": "贵州茅台", "market": "CN", "px": 1452.0,
                "date": "2026-09-26", "symbol": "600519.SH",
                "sigs": {s: True for s in report_page.SIG_STRATS},
                "since": {s: "2026-09-01" for s in report_page.SIG_STRATS},
                "res": 4}
        base.update(kw)
        return base

    def test_links_and_headers(self):
        rows = [self._row(),
                self._row(name="Palantir", market="US", symbol="PLTR",
                          px=66.0, res=1)]
        report_page.write_signals_html(rows, "2026-09-27 12:00:00")
        with open(self.path, encoding="utf-8") as f:
            h = f.read()
        self.assertIn("/kg/stock/600519", h)
        self.assertIn("/kg/stock/PLTR", h)
        for s in report_page.SIG_STRATS:      # 列头全部可点
            self.assertIn("/bt/strategy/%s" % s, h)
        self.assertIn("/bt-cross", h)

    def test_legacy_row_without_symbol_no_crash(self):
        r = self._row(symbol="")
        r.pop("symbol")
        report_page.write_signals_html([r], "t")
        with open(self.path, encoding="utf-8") as f:
            self.assertIn("贵州茅台", f.read())


class TestNormCode(unittest.TestCase):
    """cross_poll.norm_code：watchlist 前缀 → 统一格式。"""

    def test_prefixes(self):
        f = cross_poll.norm_code
        self.assertEqual(f("sh600519"), "600519.SH")
        self.assertEqual(f("sz000001"), "000001.SZ")
        self.assertEqual(f("bj430047"), "430047.BJ")   # tushare 北交所后缀
        self.assertEqual(f("rt_hk00700"), "00700")
        self.assertEqual(f("gb_pltr"), "PLTR")
        self.assertEqual(f("BTC/USDT"), "BTC/USDT")
        self.assertEqual(f(""), "")
        self.assertEqual(f(None), "")


class TestBuildCross(unittest.TestCase):
    """build_cross 聚合：方向语义/冲突/矩阵/缠论窗口/注册表/缓存。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.reports = os.path.join(self.tmp, "reports")
        os.makedirs(self.reports)
        self._orig = (cross_poll.REPORTS, cross_poll.DATA,
                      cross_poll._CACHE, cross_poll._src_chanlun_load)
        cross_poll.REPORTS = self.reports
        cross_poll.DATA = self.tmp
        cross_poll._CACHE = {"t": 0, "d": None}
        # 缠论源 mock：绕开真实 SQLite 池，走 JSONL 路径（零真实产物纪律）
        cross_poll._src_chanlun_load = (
            lambda: cross_poll._load_jsonl(
                os.path.join(self.tmp, "chanlun_pool.jsonl")))
        with open(os.path.join(self.reports, "rps_latest.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"res": [
                {"code": "sh600519", "name": "贵州茅台", "market": "CN",
                 "score": 43, "action": "回避", "longs": 1},
                {"code": "gb_pltr", "name": "Palantir", "market": "US",
                 "score": 80, "action": "买入", "longs": 3},
                {"code": "BTC/USDT", "name": "比特币", "market": "CRYPTO",
                 "score": 68, "action": "观察", "longs": 2}]}, f)
        fresh = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        old = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        with open(os.path.join(self.tmp, "chanlun_pool.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write(json.dumps({"code": "sh600519", "name": "贵州茅台",
                                "market": "CN", "type": "1买",
                                "dt": fresh, "detect_date": fresh},
                               ensure_ascii=False) + "\n")
            f.write(json.dumps({"code": "sh600519", "name": "贵州茅台",
                                "market": "CN", "type": "3卖",
                                "dt": old, "detect_date": old},
                               ensure_ascii=False) + "\n")
        with open(os.path.join(self.tmp, "nine_dim_scores.json"), "w",
                  encoding="utf-8") as f:
            json.dump({"rows": [
                {"code": "sh600519", "name": "贵州茅台", "score": 85,
                 "cov": 0.9},
                {"code": "sz000001", "name": "平安银行", "score": 70,
                 "cov": 0.8}]}, f)

    def tearDown(self):
        (cross_poll.REPORTS, cross_poll.DATA, cross_poll._CACHE,
         cross_poll._src_chanlun_load) = self._orig

    def test_aggregation_and_states(self):
        d = cross_poll.build_cross(force=True)
        st = {s["code"]: s for s in d["stocks"]}
        self.assertEqual(len(st), 3)          # 000001（九维70<80）不入榜
        # 茅台：RPS 回避(short) + 缠论1买(long) + 九维85(long) → 冲突；
        # sig4 longs=1<3 记 neutral 也计一次 hit（n_hit 含中性命中）
        self.assertEqual(st["600519.SH"]["n_hit"], 4)
        self.assertEqual(st["600519.SH"]["state"], "mixed")
        self.assertEqual(st["600519.SH"]["n_long"], 2)
        self.assertEqual(st["600519.SH"]["n_short"], 1)
        # PLTR：RPS 买入 + 4策略3/4 → 双多共振
        self.assertEqual(st["PLTR"]["state"], "long")
        self.assertEqual(st["PLTR"]["n_hit"], 2)
        # BTC：RPS 观察 + 4策略2/4（均 neutral）→ 单一中性
        self.assertEqual(st["BTC/USDT"]["state"], "neutral")
        self.assertEqual(st["BTC/USDT"]["n_hit"], 2)

    def test_chanlun_90d_window_cutoff(self):
        d = cross_poll.build_cross(force=True)
        cl = [h for s in d["stocks"] for h in s["hits"]
              if h["sid"] == "chanlun"]
        self.assertEqual(len(cl), 1)          # 200 天前信号被窗口剔除
        self.assertIn("1买", cl[0]["label"])

    def test_matrix_counts(self):
        d = cross_poll.build_cross(force=True)
        m = d["matrix"]
        self.assertEqual(m["qpzm"]["chanlun"], 1)   # 仅茅台有缠论信号
        self.assertEqual(m["qpzm"]["sig4"], 3)      # 3 票均同时被两源覆盖
        self.assertEqual(m["qpzm"]["qpzm"], 3)      # 对角=覆盖标的数

    def test_registry_driven(self):
        d = cross_poll.build_cross(force=True)
        self.assertEqual({s["id"] for s in d["strategies"]},
                         {"qpzm", "sig4", "chanlun", "9dim"})

    def test_cache_ttl_reuse(self):
        a = cross_poll.build_cross(force=True)
        self.assertIs(cross_poll.build_cross(), a)  # 60s 内命中缓存


class TestMethodologyMatrix(unittest.TestCase):
    """个股全景页方法论矩阵 portal_api._matrix_cards（纯函数）。

    A 段 = cross_poll.STRATEGIES 逐策略出卡（命中=label+方向色，
    未命中=mute）；B 段 = 本地附加卡（实时4策略→④ / JEV→⑤ / AI→⑥）。
    零 IO、零网络，全部内存假数据；import portal_api 仅加载定义
    （服务启动在 __main__ guard 内，不触端口）。
    """

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    @staticmethod
    def _strats():
        return [{k: v for k, v in s.items() if k != "loader"}
                for s in cross_poll.STRATEGIES]

    # ── A 段：注册表策略卡 ──────────────────────────────
    def test_registry_icons_and_urls(self):
        for s in cross_poll.STRATEGIES:
            self.assertTrue(s.get("icon"), "注册表 %s 缺 icon" % s["id"])
            self.assertTrue(s.get("url"), "注册表 %s 缺 url" % s["id"])

    def test_every_registered_strategy_gets_card(self):
        cards = self.pa._matrix_cards(
            {"strategies": self._strats()}, None, {}, "", [])
        self.assertEqual(len(cards), len(cross_poll.STRATEGIES) + 3)

    def test_hit_card_label_and_dir_color(self):
        for d, cls in (("long", "buy"), ("short", "avoid"),
                       ("neutral", "watch")):
            hit = {"sid": "qpzm", "dir": d, "label": "共振80·买入",
                   "url": "/bt-qpzm", "badge": "共振", "color": "#3fb950"}
            h = "".join(self.pa._matrix_cards(
                {"strategies": self._strats()},
                {"code": "600519.SH", "hits": [hit]}, {}, "", []))
            self.assertIn("class='mv %s'>共振80·买入" % cls, h, d)

    def test_hit_dt_rendered_before_link(self):
        hit = {"sid": "chanlun", "dir": "long", "label": "缠论1买",
               "url": "/bt-chanlun-pool", "dt": "2026-06-26"}
        h = "".join(self.pa._matrix_cards(
            {"strategies": self._strats()},
            {"code": "600519.SH", "hits": [hit]}, {}, "", []))
        self.assertIn("2026-06-26 · → 工作台", h)
        hit2 = dict(hit, dt="")
        h2 = "".join(self.pa._matrix_cards(
            {"strategies": self._strats()},
            {"code": "600519.SH", "hits": [hit2]}, {}, "", []))
        self.assertNotIn("2026-06-26", h2)
        self.assertIn("缠论1买</div>", h2)

    def test_miss_card_muted_with_workbench_url(self):
        h = "".join(self.pa._matrix_cards(
            {"strategies": self._strats()}, None, {}, "", []))
        self.assertIn("未命中", h)
        self.assertIn("class='mcard mute' href='/bt-9dim'", h)
        self.assertIn("class='mcard mute' href='/bt-chanlun-pool'", h)

    def test_new_strategy_auto_appears_zero_change(self):
        """扩展性核心：注册表加一条 → 矩阵自动多一张卡，渲染零改。"""
        strats = self._strats() + [
            {"id": "newx", "name": "新策略<-X>", "icon": "🆕",
             "url": "/bt-newx"}]
        cards = self.pa._matrix_cards({"strategies": strats}, None,
                                      {}, "", [])
        h = "".join(cards)
        self.assertEqual(len(cards), len(strats) + 3)
        self.assertIn("🆕 新策略&lt;-X&gt;", h)     # 名称转义不破版
        self.assertIn("href='/bt-newx'", h)

    def test_strategy_name_escaped(self):
        h = "".join(self.pa._matrix_cards(
            {"strategies": [{"id": "x", "name": "A&B<c>", "icon": "x",
                             "url": "/x"}]}, None, {}, "", []))
        self.assertIn("A&amp;B&lt;c&gt;", h)
        self.assertNotIn("A&B<c>", h)

    # ── B 段：本地附加卡 ────────────────────────────────
    def test_sig4_counts_and_anchor(self):
        sig = {"trend_follow": "多头", "ma_cross": "多头",
               "rsi_reversal": "空头", "boll_breakout": "空仓"}
        h = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, sig, "", []))
        self.assertIn("2/4 多头", h)
        self.assertIn("href='#sig4'", h)

    def test_sig4_all_long_gets_buy_color(self):
        sig = {s: "多头" for s in ("trend_follow", "ma_cross",
                                   "rsi_reversal", "boll_breakout")}
        h = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, sig, "", []))
        self.assertIn("class='mv buy'>4/4 多头", h)

    def test_sig4_error_mute_fallback(self):
        h = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, {}, "技术引擎暂不可用（x）", []))
        self.assertIn("class='mcard mute' href='#sig4'", h)
        self.assertIn("技术引擎暂不可用（x）", h)

    def test_jev_dominant_bias_and_tie(self):
        h = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, {}, "",
            [{"bias_cn": "利多"}, {"bias_cn": "利多"},
             {"bias_cn": "利空"}]))
        self.assertIn("利多 · 3 条", h)
        self.assertIn("href='#jev'", h)
        h2 = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, {}, "",
            [{"bias_cn": "利多"}, {"bias_cn": "利空"}]))
        self.assertIn("分歧 · 2 条", h2)

    def test_jev_empty_links_hub(self):
        h = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, {}, "", []))
        self.assertIn("近7天无判断", h)
        self.assertIn("href='/bt-jev'", h)

    def test_ai_card_always_present(self):
        h = "".join(self.pa._matrix_cards(
            {"strategies": []}, None, {}, "", []))
        self.assertIn("href='#ai'", h)
        self.assertIn("AI 深度分析", h)


class TestPillNav(unittest.TestCase):
    """pill_nav 导航：面包屑 + ☰ 域内下拉分组 + DOMAIN_NAV_GROUPS 单一数据源。

    守护点：预警池等入口不丢失、当前页高亮不再成链接、
    其他域（intel）保持分组不受 quant 域影响。
    """

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    def test_fin_group_labels(self):
        """quant 域下拉必须渲染全部五个分组头。"""
        h = self.pa.pill_nav("fin")
        for lb in ("盘前与驾驶", "指挥", "策略资产", "实盘跟踪",
                   "研究与数据"):
            self.assertIn(lb, h)

    def test_fin_group_contains_all_links(self):
        """每个分组项在「别的页」上必须是链接（当前页自身为高亮 span）。"""
        all_keys = [k for _, its in self.pa.DOMAIN_NAV_GROUPS["quant"]
                    for _, _, k in its]
        for _, items in self.pa.DOMAIN_NAV_GROUPS["quant"]:
            for lb, href, key in items:
                probe = next(k for k in all_keys if k != key)
                h = self.pa.pill_nav(probe)
                self.assertIn("href='%s'" % href, h, key)

    def test_chanlun_pool_still_in_nav(self):
        """回归守护：预警池入口不得从导航中丢失。"""
        h = self.pa.pill_nav("fin")
        self.assertIn("href='/bt-chanlun-pool'", h)

    def test_current_page_highlighted_not_link(self):
        h = self.pa.pill_nav("qpzm")
        self.assertIn(">🌊 青萍之末 🚧</span>", h)    # 高亮 span
        self.assertNotIn("href='/bt-qpzm'", h)     # 自己不再是链接

    def test_other_sections_flat(self):
        h = self.pa.pill_nav("intel")
        self.assertNotIn(">今日</span>", h)
        self.assertNotIn(">跟踪</span>", h)
        self.assertIn("href='/bt-jev'", h)

    def test_section_pages_derived_from_groups(self):
        flat = [it for _, its in self.pa.DOMAIN_NAV_GROUPS["quant"]
                for it in its]
        self.assertEqual(self.pa.SECTION_PAGES["quant"], flat)

    def test_page_section_keys_covered(self):
        """PAGE_SECTION 里 quant 区的每个 key 都必须挂在导航上。"""
        keys = {k for _, its in self.pa.DOMAIN_NAV_GROUPS["quant"]
                for _, _, k in its}
        for k, sec in self.pa.PAGE_SECTION.items():
            if sec == "quant":
                self.assertIn(k, keys, "PAGE_SECTION 有 %s 但导航无" % k)


class TestFinCockpit(unittest.TestCase):
    """/fin 三段式驾驶舱：A 今日全景 / B 策略动态 / C 折叠收纳。

    纪律：_read_json/_sa_hist_read/cross_poll 全 mock，零真实产物。
    Handler 用 object.__new__ 绕过 socket 初始化，_html 捕获输出。
    """

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    def _render(self, premarket=None, rps=None, hist=None, cross=None):
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        fake_mod = types.SimpleNamespace(
            build_cross=lambda force=False: (cross if cross else {}))

        def fake_read(p):
            if "premarket" in p:
                return premarket or {}
            if "rps" in p:
                return rps or {}
            return {}
        with mock.patch.object(self.pa.api_backtest, "_read_json",
                               side_effect=fake_read), \
             mock.patch.object(self.pa.api_backtest, "_sa_hist_read",
                               return_value=hist or []), \
             mock.patch.dict(sys.modules, {"cross_poll": fake_mod}):
            h._fin_page()
        self.assertEqual(len(captured), 1)
        return captured[0]

    # ── 三段式骨架 ─────────────────────────────────────
    def test_three_zones_rendered(self):
        # WO-QT-20261020-080: K-STOCK-403（60479cb6）把闸门外层标题
        # "四市场闸门"重构为 gatebar 横幅内嵌"市场闸门"，且空数据时
        # 横幅降级为"暂无快照"。骨架断言随设计更新，并注入最小快照
        # 使闸门区块真实渲染，覆盖不回退。
        rps = {"latest": "2026-09-27 17:35", "envs": [
            {"label": "A股环境", "safe": True, "score": 72, "state": "多头"}]}
        h = self._render(rps=rps)
        for frag in ("分析一只股票", "市场闸门", "策略动态",
                     "盘前五维明细", "工具与横切", "最近分析"):
            self.assertIn(frag, h)
        self.assertEqual(h.count("<details class='sec'>"), 3)  # C 区折叠

    # ── A 区：今日全景 ─────────────────────────────────
    def test_gates_on_off(self):
        rps = {"latest": "2026-09-27 17:35", "envs": [
            {"label": "A股环境", "safe": True, "score": 72, "state": "多头"},
            {"label": "美股环境", "safe": False, "score": 41,
             "state": "空头"}]}
        h = self._render(rps=rps)
        self.assertIn("g-on", h)
        self.assertIn("g-off", h)
        self.assertIn("✅ 放行", h)
        self.assertIn("⛔ 否决", h)

    def test_opportunity_top5_capped(self):
        res = [{"code": "600%02d.SH" % i, "name": "票%d" % i,
                "market": "CN", "px": 10 + i, "score": 90 - i,
                "dims": "4/4", "action": "买入"} for i in range(8)]
        h = self._render(rps={"latest": "t", "res": res})
        self.assertEqual(h.count("<tr><td><a href='/kg/stock/"),
                         5)                          # Top5 截断
        self.assertIn("全池 8 只", h)                # 计数报全池
        self.assertIn("买入 5", h)                   # 展示数为截断后

    def test_no_buys_placeholder(self):
        h = self._render(rps={"latest": "t", "res": [
            {"code": "600519.SH", "action": "回避", "score": 40}]})
        self.assertIn("今日无买入信号", h)

    def test_premarket_one_liner(self):
        h = self._render(premarket={"verdict": "中性偏多", "prob": 62,
                                    "vals": {"情绪": 3}})
        self.assertIn("中性偏多", h)
        self.assertIn("62%", h)
        self.assertIn("情绪", h)

    # ── A2 五维拼图（结论置顶 + 维度色块 + 板块结构 + 摘要行）──
    _PRE_FULL = {
        "verdict": "偏空（开盘折价概率较大）", "prob": 1,
        "vals": {"外围": 6, "流动性": -4, "政策": -33, "板块": 6,
                 "情绪": 30},
        "l1": {"score": 6, "fx": {"val": 7.0208, "pct": -0.03}},
        "l3": {"score": -4},
        "l4": [{"sector": "科技巨头", "score": 18},
               {"sector": "互联网中概", "score": -34}]}

    def test_premarket_five_dim_chips(self):
        """五维各出一张色块：维度名专属色 + 分值正绿负红。"""
        h = self._render(premarket=self._PRE_FULL)
        for frag in ("color:#58a6ff'>外围",     # 蓝
                     "color:#d2a8ff'>流动性",   # 紫
                     "color:#f778ba'>政策",     # 粉
                     "color:#ffa657'>板块",     # 橙
                     "color:#3fb950'>情绪"):    # 绿
            self.assertIn(frag, h)
        self.assertIn("+6", h)                  # 正分渲染
        self.assertIn("-33", h)                 # 负分渲染
        self.assertIn("偏空", h)                 # 结论置顶
        self.assertIn("溢价 1%", h)

    def test_premarket_sector_structure(self):
        """板块结构：l4 正负最强各一，涨红跌绿（A 股口径）。"""
        h = self._render(premarket=self._PRE_FULL)
        self.assertIn("科技巨头 +18", h)
        self.assertIn("互联网中概 -34", h)
        self.assertIn("color:#f85149'>科技巨头", h)
        self.assertIn("color:#3fb950'>互联网中概", h)

    def test_premarket_l1l3_fx_summary(self):
        """摘要行：外围/流动性分值 + 离岸人民币贬值方向。"""
        h = self._render(premarket=self._PRE_FULL)
        self.assertIn("外围 +6", h)
        self.assertIn("流动性 -4", h)
        self.assertIn("离岸人民币 7.0208", h)
        self.assertIn("贬", h)                  # pct<0 → 贬

    def test_premarket_minimal_degradation(self):
        """只有 verdict 时五维/板块/摘要全部缺席不崩，详情链接仍在。"""
        h = self._render(premarket={"verdict": "中性", "prob": None})
        self.assertIn("中性", h)
        self.assertIn("溢价 —", h)
        self.assertIn("盘前详情 →", h)
        self.assertNotIn("color:#ffa657'>板块", h)   # 无 l4 不出板块行

    # ── B 区：策略动态（注册表驱动）────────────────────
    def test_strategy_dyn_cards_from_registry(self):
        cross = {"strategies": [
            {"id": "qpzm", "name": "RPS共振", "icon": "🌊",
             "url": "/bt-qpzm"},
            {"id": "sig4", "name": "四策略信号", "icon": "📊",
             "url": "/bt-signals"}],
            "n_per": {"qpzm": 12, "sig4": 30},
            "stocks": [{"n_hit": 2, "state": "long"},
                       {"n_hit": 2, "state": "mixed"},
                       {"n_hit": 1, "state": "long"}]}
        h = self._render(cross=cross)
        self.assertEqual(h.count("class='sstat'"), 3)  # 2 策略 + 1 聚合
        self.assertIn("12 只命中", h)
        self.assertIn("30 只命中", h)
        self.assertIn("href='/bt-qpzm'", h)
        self.assertIn("href='/bt-cross'", h)
        self.assertIn("2</span> 多策略", h)   # n_hit>=2 计数
        self.assertIn("1</span> 冲突", h)     # mixed 计数

    def test_zero_hits_strategy_card(self):
        cross = {"strategies": [{"id": "x", "name": "空策略",
                                 "icon": "🧪", "url": "/x"}],
                 "n_per": {}, "stocks": []}
        h = self._render(cross=cross)
        self.assertIn("0 只命中", h)               # 0 值也出卡，不缺

    # ── 空数据降级 ────────────────────────────────────
    def test_empty_data_fallbacks(self):
        h = self._render()
        self.assertEqual(h.count("暂无快照"), 2)   # 闸门 + 机会
        self.assertIn("href='/bt-cross'", h)       # B 区聚合卡常驻
        self.assertIn("0</span> 多策略", h)

    # ── C 区 ─────────────────────────────────────────
    def test_recent_history(self):
        hist = [{"q": "600519", "name": "贵州茅台",
                 "px": "1452.00（+1.2%）", "bias": "利多",
                 "t": "09-27 10:00", "ok": True}]
        h = self._render(hist=hist)
        self.assertIn("/sa?q=600519", h)
        self.assertIn("贵州茅台", h)
        self.assertIn("color:#f85149'>利多<", h)   # 利多红（A 股口径）

    def test_tools_collapsed_zone(self):
        h = self._render()
        for href in ("/bt-signals", "/bt-positions", "/bt-sectors",
                     "/bt-opt", "/bt-jev", "/bt-brief"):
            self.assertIn("href='%s'" % href, h)


def _fake_cross():
    """build_cross() 假产物：2 策略 × 3 标的（冲突/共振多/US 独立）。"""
    hit_a = {"sid": "a", "dir": "long", "label": "A看多", "url": "/bt-a",
             "badge": "A", "color": "#3fb950", "dt": ""}
    hit_b_short = {"sid": "b", "dir": "short", "label": "B看空",
                   "url": "/bt-b", "badge": "B", "color": "#58a6ff",
                   "dt": ""}
    hit_b_long = {"sid": "b", "dir": "long", "label": "B看多",
                  "url": "/bt-b", "badge": "B", "color": "#58a6ff",
                  "dt": ""}
    return {
        "generated_at": "2026-09-27 10:00:00",
        "strategies": [
            {"id": "a", "name": "策略A", "badge": "A", "color": "#3fb950",
             "url": "/bt-a", "markets": "CN"},
            {"id": "b", "name": "策略B", "badge": "B", "color": "#58a6ff",
             "url": "/bt-b", "markets": "CN/US"}],
        "n_per": {"a": 3, "b": 3},
        "stocks": [
            {"code": "600519", "name": "贵州茅台", "market": "CN",
             "n_hit": 2, "n_long": 1, "n_short": 1, "state": "mixed",
             "hits": [hit_a, hit_b_short]},
            {"code": "000001", "name": "平安银行", "market": "CN",
             "n_hit": 2, "n_long": 2, "n_short": 0, "state": "long",
             "hits": [hit_a, hit_b_long]},
            {"code": "PLTR", "name": "Palantir", "market": "US",
             "n_hit": 2, "n_long": 2, "n_short": 0, "state": "long",
             "hits": [dict(hit_a), dict(hit_b_long)]}],
        "matrix": {"a": {"a": 3, "b": 3}, "b": {"a": 3, "b": 3}},
    }


class TestCrossBoard(unittest.TestCase):
    """/bt-cross 共振看板页（_cross_page）：区块 + 全下钻 + 筛选。

    cross_poll 用 SimpleNamespace 假模块注入 sys.modules，
    Handler 用 object.__new__ 绕过 socket，_html 捕获输出。
    """

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    def _render(self, path="/bt-cross", cross=None, boom=False):
        def _build(force=False):
            if boom:
                raise RuntimeError("boom")
            return cross if cross is not None else _fake_cross()
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        h.path = path
        with mock.patch.dict(sys.modules, {"cross_poll": types.SimpleNamespace(
                build_cross=_build)}):
            h._cross_page()
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_four_blocks_rendered(self):
        h = self._render()
        for frag in ("方向冲突", "共振做多", "共振预警", "策略覆盖矩阵",
                     "上架策略"):
            self.assertIn(frag, h)

    def test_stock_and_badge_drilldowns(self):
        h = self._render()
        self.assertIn("href='/kg/stock/600519'", h)   # 标的→个股全景
        self.assertIn("href='/bt-a'", h)                # 徽标→策略工作台
        self.assertIn("下钻到 A看多", h)                 # 徽标 title
        self.assertIn("贵州茅台", h)

    def test_conflict_row_and_resonance_counts(self):
        h = self._render()
        self.assertIn("多空分歧（1）", h)                # mixed 1 只
        self.assertIn("2 策略看多", h)                  # 000001 共振
        self.assertIn("当前无 ≥2 策略共振看空的标的", h)  # 无 short≥2

    def test_matrix_pair_links(self):
        h = self._render()
        self.assertIn("href='/bt-cross?pair=a,b'", h)   # 交集下钻

    def test_pair_drilldown_view(self):
        h = self._render(path="/bt-cross?pair=a,b")
        self.assertIn("交集明细：策略A × 策略B", h)
        self.assertIn("href='/kg/stock/600519'", h)   # 共同命中

    def test_market_filter_tab(self):
        h = self._render(path="/bt-cross?m=US")
        self.assertIn("PLTR", h)
        self.assertNotIn("600519", h)                   # CN 被筛掉
        h2 = self._render()
        self.assertIn("href='/bt-cross?m=CN'", h2)
        self.assertIn("href='/bt-cross?m=US'", h2)

    def test_aggregator_error_fallback(self):
        h = self._render(boom=True)
        self.assertIn("聚合器异常", h)
        self.assertIn("boom", h)


class TestChanlunPoolPage(unittest.TestCase):
    """🛟 缠论预警池页（_chanlun_pool_page）：下钻 + 强预警 + 战绩。

    QBT_DIR 指向临时 data 目录（三份假产物），run() mock 掉
    PowerShell 计划任务查询——零真实产物零子进程。
    """

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    def setUp(self):
        self._orig_qbt = self.pa.QBT_DIR
        self._orig_run = self.pa.run
        self._orig_mixin_qbt = self.pa.api_chanlun_pages.QBT_DIR
        self._orig_mixin_run = self.pa.api_chanlun_pages.run
        self.tmp = tempfile.mkdtemp(prefix="clpool_")
        self.d = os.path.join(self.tmp, "data")
        os.makedirs(self.d)
        self.pa.QBT_DIR = self.tmp
        self.pa.api_chanlun_pages.QBT_DIR = self.tmp
        _run = lambda *a, **kw: (
            "last=2026-09-27 07:10  result=0  next=2026-09-28 07:10")
        self.pa.run = _run
        self.pa.api_chanlun_pages.run = _run

    def tearDown(self):
        self.pa.QBT_DIR = self._orig_qbt
        self.pa.run = self._orig_run
        self.pa.api_chanlun_pages.QBT_DIR = self._orig_mixin_qbt
        self.pa.api_chanlun_pages.run = self._orig_mixin_run
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, pool_lines, paper=None, ev=None):
        with open(os.path.join(self.d, "chanlun_pool.jsonl"), "w",
                  encoding="utf-8") as f:
            f.write("".join(pool_lines))
        for fn, obj in (("chanlun_paper.json", paper or {}),
                        ("chanlun_pool_eval_cache.json", ev or {})):
            with open(os.path.join(self.d, fn), "w",
                      encoding="utf-8") as f:
                json.dump(obj, f, ensure_ascii=False)

    def _render(self):
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        h._chanlun_pool_page()
        self.assertEqual(len(captured), 1)
        return captured[0]

    def test_drilldown_links(self):
        self._write([
            '{"code":"600519","name":"贵州茅台","type":"1卖",'
            '"dt":"2026-09-26 15:00","detect_date":"2026-09-26",'
            '"entry_px":1452.0,"tbe":true,"mbei":true,"bei":false}\n'])
        h = self._render()
        self.assertIn("href='/kg/stock/600519'", h)   # 代码→个股全景
        self.assertIn("href='/bt-chanlun'", h)          # 徽标→样本面板
        self.assertIn("影子", h)                        # 影子口径不变形

    def test_strong_alert_banner(self):
        self._write([
            '{"code":"600519","name":"贵州茅台","type":"1卖",'
            '"detect_date":"2026-09-26","tbe":true}\n',
            '{"code":"000001","name":"平安银行","type":"2买",'
            '"detect_date":"2026-09-25"}\n'])
        h = self._render()
        self.assertIn("今日强预警", h)
        self.assertIn("600519 贵州茅台", h)             # 强预警点名

    def test_no_strong_alert_quiet(self):
        self._write([
            '{"code":"600519","name":"贵州茅台","type":"2买",'
            '"detect_date":"2026-09-26","tbe":true}\n'])
        h = self._render()
        self.assertIn("无 1卖·趋✓ 强预警信号", h)

    def test_side_tags_colors(self):
        self._write([
            '{"code":"600519","type":"1卖","detect_date":"2026-09-26",'
            '"mbei":true,"bei":false,"reg":"up"}\n'])
        h = self._render()
        self.assertIn("color:#bc8cff", h)               # M✓ 紫
        self.assertIn("✗", h)                           # False 灰叉
        self.assertIn("势↑", h)

    def test_paper_positions(self):
        self._write(['{"code":"000001","type":"2买",'
                     '"detect_date":"2026-09-25"}\n'],
                    paper={"open": [{"code": "000001",
                                     "name": "平安银行", "type": "2买",
                                     "entry_date": "2026-09-25",
                                     "days_held": 2, "last_px": 10.8,
                                     "pnl": 0.028, "tags": {}}],
                           "closed": []})
        h = self._render()
        self.assertIn("href='/kg/stock/000001'", h)
        self.assertIn("+2.80%", h)                      # pnl 红绿着色
        self.assertIn("color:#3fb950", h)

    def test_eval_table(self):
        self._write(['{"code":"600519","type":"1卖",'
                     '"detect_date":"2026-09-26"}\n'],
                    ev={"generated_at": "2026-09-26 20:00",
                        "n_pool": 100, "n_open": 1, "n_closed": 5,
                        "types": {"1卖": {
                            "all": {"n": 79, "win": 60.8, "avg": 4.94},
                            "closed_n": 79,
                            "sides": {"mbei": {"n": 20, "win": 70.0,
                                               "avg": 6.0}}}}})
        h = self._render()
        self.assertIn("n=79", h)                        # 全集战绩
        self.assertIn("+4.94%", h)
        self.assertIn("n=20", h)                        # 侧面战绩
        self.assertNotIn("style='color:#484f58'><span>n=79", h)  # 达标不灰

    def test_empty_pool_placeholder(self):
        self._write([])
        h = self._render()
        self.assertIn("尚无入池信号", h)
        self.assertIn("当前无持仓", h)
        self.assertIn("last=2026-09-27", h)     # run() mock 生效，零子进程


class TestStockOverview(unittest.TestCase):
    """/bt/stock 个股全景整页（_bt_stock_page）：六区块 + 锚点闭环。

    全 mock 零网络：_bt_resolve/_bt_jev_news patch 模块属性，
    stock_analyze/runner/data_loader/strategies/cross_poll 注入
    sys.modules 假模块，STATION 指向临时目录放假 rps 快照。
    假 df 用带 __len__ 的 list 子类，策略值用 values=[±1] 命名空间。
    """

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    def setUp(self):
        self._orig_station = self.pa.STATION
        self._orig_ab_station = self.pa.api_backtest.STATION
        self._orig_ab_reports = self.pa.api_backtest.REPORTS_DIR
        self._orig_ab_qbt = self.pa.api_backtest.QBT_DIR
        self.tmp = tempfile.mkdtemp(prefix="stkov_")
        os.makedirs(os.path.join(self.tmp, "quant_backtest", "reports"))
        self.pa.STATION = self.tmp
        self.pa.api_backtest.STATION = self.tmp
        self.pa.api_backtest.REPORTS_DIR = os.path.join(
            self.tmp, "quant_backtest", "reports")
        self.pa.api_backtest.QBT_DIR = self.tmp

    def tearDown(self):
        self.pa.STATION = self._orig_station
        self.pa.api_backtest.STATION = self._orig_ab_station
        self.pa.api_backtest.REPORTS_DIR = self._orig_ab_reports
        self.pa.api_backtest.QBT_DIR = self._orig_ab_qbt
        shutil.rmtree(self.tmp, ignore_errors=True)

    class _DF(list):
        """假 K 线：len(df)>=55 即可通过引擎门槛。

        K235 公告后验/量能事件块用 df.columns 探测字段（portal_api
        12602/12636 两处在 try 块外），假 df 需声明 columns 才能走到
        「字段缺失 → 整块安全跳过」分支（生产语义：无该列即跳过）。
        """
        @property
        def columns(self):
            return []   # 假 df 无 close/volume 列 → K235 块跳过

    def _render(self, qs="c=600519.SH", quote=None, rps=None,
                sig4="ok", items=None, cross=None, resolve="hit"):
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        entry = None if resolve == "miss" else {
            "name": "贵州茅台", "code": "600519.SH", "aliases": []}
        fake_sa = types.SimpleNamespace(fetch_quote=lambda c: quote)
        fake_runner = types.SimpleNamespace(
            resolve_watchlist=lambda rows: [("cn", "600519", "贵州茅台")],
            CFG={"strategies": {}})
        if sig4 == "ok":
            fake_dl = types.SimpleNamespace(load_bars=lambda mk, sy, st, en:
                                            {sy[0]: self._DF([0] * 60)})
            fake_strats = types.SimpleNamespace(REGISTRY={
                sn: (lambda df, cfg: types.SimpleNamespace(values=[1]))
                for sn in ("trend_follow", "ma_cross",
                           "rsi_reversal", "boll_breakout")})
        else:   # nodata：load_bars 空 → 引擎降级分支
            fake_dl = types.SimpleNamespace(load_bars=lambda *a: {})
            fake_strats = types.SimpleNamespace(REGISTRY={})
        fake_cp = types.SimpleNamespace(
            build_cross=lambda force=False: (cross if cross is not None
                                             else _fake_cross()),
            norm_code=lambda c: str(c).split(".")[0])
        if rps is not None:
            with open(os.path.join(self.tmp, "quant_backtest", "reports",
                                   "rps_latest.json"), "w",
                      encoding="utf-8") as f:
                json.dump(rps, f, ensure_ascii=False)
        mods = {"stock_analyze": fake_sa, "runner": fake_runner,
                "data_loader": fake_dl, "strategies": fake_strats,
                "cross_poll": fake_cp,
                # 20261014_g 补漏：_bt_stock_page 的 KG 块会 import
                # kg_portal/event_graph 直连真实 Neo4j（违反本类「全
                # mock 零网络」契约；无 neo4j 驱动的解释器里 KgClient
                # 抛 SystemExit 绕过产线 except Exception 致批量报错）。
                # stub 成 None/空 → 走产线既有降级分支，零网络确定性。
                "kg_portal": types.SimpleNamespace(stock=lambda c, days=90: None),
                "event_graph": types.SimpleNamespace(
                    events_for_stock=lambda c, days=30:
                        {"code": c, "n": 0,
                         "bias_stat": {"bull": 0, "bear": 0, "neutral": 0,
                                       "unknown": 0, "net": 0.0, "n": 0},
                         "events": []})}
        with mock.patch.object(self.pa.api_backtest, "_bt_resolve",
                               return_value=entry), \
             mock.patch.object(self.pa.api_backtest, "_bt_jev_news",
                               return_value=items if items is not None
                               else []), \
             mock.patch.dict(sys.modules, mods):
            h._bt_stock_page(qs)
        self.assertEqual(len(captured), 1)
        return captured[0]

    _RPS = {"latest": "2026-09-26", "res": [
        {"code": "600519.SH", "name": "贵州茅台", "action": "买入",
         "score": 87, "dims": "4/4", "tech": 90, "jev": 60, "jev_n": 2,
         "rps": 85, "rps_label": "RPS85", "env": 70}]}

    _YDAY = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    _ITEMS = [{"bias_cn": "利多", "conf": 86, "title": "回购计划",
               "reason": "正面", "ts": _YDAY}]

    def test_six_sections_and_anchor_loop(self):
        """①-⑥ 区块齐全；矩阵 B 段卡锚点与区块 id 一一对应。

        注意：JEV 卡仅在「有判断」时跳本页 #jev 锚点，无判断时
        跳 /bt-jev 主页（见 test_resonance_miss_fallback）。
        """
        h = self._render(quote={"price": "1452", "pct": 1.0},
                         rps=self._RPS, items=self._ITEMS)
        for frag in ("① 行情", "② 方法论矩阵", "③ 共振分解", "④ 策略信号",
                     "⑤ 全事件时间轴", "⑥ AI 深度分析"):
            self.assertIn(frag, h)
        for aid in ("id='sig4'", "id='jev'", "id='ai'"):
            self.assertIn(aid, h)
        for href in ("href='#sig4'", "href='#jev'", "href='#ai'"):
            self.assertIn(href, h)          # 矩阵卡 → 本页锚点闭环

    def test_quote_render_and_colors(self):
        # high/low 用数值：portal _fv 只格式化 int/float，字符串渲染为
        # 「—」（K235 前该断言被 df.columns 崩溃掩盖）
        h = self._render(quote={"price": "1452.00", "pct": 1.5,
                                "high": 1460, "low": 1440,
                                "ts": "09-27"})
        self.assertIn("1452.00", h)
        self.assertIn("class='buy'", h)     # A股口径涨红跌绿：+ → .buy(红)
        self.assertIn("1460", h)

    def test_quote_missing_placeholder(self):
        h = self._render(quote=None)
        self.assertIn("暂无实时", h)

    def test_resonance_hit_breakdown(self):
        h = self._render(quote=None, rps=self._RPS)
        self.assertIn("综合", h)
        self.assertIn("87", h)
        self.assertIn("买入", h)
        self.assertIn("→ 青萍之末工作台", h)

    def test_resonance_miss_fallback(self):
        h = self._render(quote=None)        # 无 rps 文件 → 降级
        self.assertIn("不在自选池", h)
        self.assertIn("href='/bt-jev'", h)  # JEV 矩阵卡空数据 → 主页

    def test_sig4_full_long_and_matrix_integration(self):
        """矩阵 B1 卡与 ④ 表同源：4 多头 → buy 色 + 4 行纵切链接。"""
        h = self._render(quote=None, sig4="ok")
        self.assertIn("4/4 多头", h)                  # 矩阵卡
        self.assertIn("class='mv buy'", h)
        for sn in ("trend_follow", "ma_cross", "rsi_reversal",
                   "boll_breakout"):
            self.assertIn("href='/bt/strategy/%s'" % sn, h)
        self.assertEqual(h.count("<span class='b-long'>多头</span>"), 4)

    def test_sig4_engine_nodata_fallback(self):
        h = self._render(quote=None, sig4="nodata")
        self.assertIn("无足够历史数据", h)             # 表占位
        self.assertIn("class='mcard mute' href='#sig4'",
                      h)                              # 矩阵卡 mute 降级

    def test_jev_items_and_matrix_card(self):
        items = [{"bias_cn": "利多", "conf": 86, "title": "回购计划",
                  "reason": "正面", "ts": self._YDAY},
                 {"bias_cn": "利多", "conf": 70, "title": "业绩超预期",
                  "reason": "正面", "ts": self._YDAY}]
        h = self._render(quote=None, items=items)
        self.assertIn("bdg g", h)               # ⑤ 时间轴利多徽章（绿底红字）
        self.assertIn("利多 86%", h)
        self.assertIn("回购计划", h)
        self.assertIn("href='/bt-jev'", h)
        self.assertIn("利多 · 2 条", h)               # 矩阵 B2 卡同源

    def test_unknown_code_falls_to_form(self):
        h = self._render(resolve="miss")
        self.assertIn("未识别", h)
        self.assertIn("个股全景", h)

    def test_empty_query_form(self):
        h = self._render(qs="")
        self.assertIn("个股全景", h)
        self.assertIn("input", h)


class TestDataHealth(unittest.TestCase):
    """data_health.py 扫描器 + /bt-data-health 页面渲染。"""

    @classmethod
    def setUpClass(cls):
        pdir = os.path.join(os.path.dirname(HERE), "portal")
        sys.path.insert(0, pdir)
        import portal_api as _pa
        cls.pa = _pa

    def test_scan_returns_structure(self):
        """scan_data_health 返回 dict，含预期顶层 key。"""
        import data_health as _dh
        with mock.patch("data_health._scan_kline", return_value={
            "count": 10, "exists": True, "path": "a"}):
            d = _dh.scan_data_health()
        for k in ("generated_at", "kline", "news", "strategy_artifacts",
                  "gaps"):
            self.assertIn(k, d)

    def test_kline_count_and_market_split(self):
        """A股按 _SH/_SZ/_BJ 后缀分类计数。"""
        import data_health as _dh
        def _fp(name):
            return types.SimpleNamespace(
                name=name,
                stat=lambda: types.SimpleNamespace(
                    st_size=1, st_mtime=1727000000.0))
        with mock.patch.object(_dh.Path, "exists", return_value=True), \
             mock.patch.object(_dh.Path, "rglob",
                               return_value=[_fp("1_SH.parquet"),
                                             _fp("2_SZ.parquet"),
                                             _fp("3_BJ.parquet"),
                                             _fp("4_SH.parquet")]):
            r = _dh._scan_kline("cn")
        self.assertEqual(r["count"], 4)
        self.assertEqual(r["sh"], 2)
        self.assertEqual(r["sz"], 1)
        self.assertEqual(r["bj"], 1)
        self.assertIn("latest_file", r)

    def test_news_db_stats(self):
        """news.db 统计：total/sources/date_range/jev_judged。"""
        import data_health as _dh
        fake_conn = mock.MagicMock()
        fake_conn.execute.side_effect = [
            mock.MagicMock(fetchone=lambda: (2040,)),
            mock.MagicMock(fetchone=lambda:
                           ("2026-09-25", "2026-09-27")),
            mock.MagicMock(fetchall=lambda:
                           [("sina", 1303), ("eastmoney", 430)]),
            mock.MagicMock(fetchone=lambda: (44,)),
        ]
        with mock.patch("data_health.sqlite3.connect", return_value=fake_conn):
            r = _dh._scan_news()
        self.assertEqual(r["total"], 2040)
        self.assertEqual(r["sources"]["sina"], 1303)
        self.assertIn("2026-09-25", r["date_range"])
        self.assertEqual(r["jev_pending"], 1996)

    def test_gaps_list_populated(self):
        """已知缺口至少包含美股/港股/加密 K线缺失。"""
        import data_health as _dh
        kline = {"us": {"count": 0, "exists": False},
                 "hk": {"count": 0, "exists": False},
                 "crypto": {"count": 0, "exists": False}}
        news = {"exists": True, "has_announcement": False,
                "has_research": False, "has_report": False,
                "jev_pending": 0}
        gaps = _dh._build_gaps(kline, news)
        self.assertTrue(any("美股" in g for g in gaps))
        self.assertTrue(any("港股" in g for g in gaps))
        self.assertTrue(any("加密" in g for g in gaps))

    def test_news_sources_derive_has_flags(self):
        """has_announcement/research/report 从 sources 计数动态推导。"""
        import data_health as _dh
        fake_conn = mock.MagicMock()
        fake_conn.execute.side_effect = [
            mock.MagicMock(fetchone=lambda: (2400,)),
            mock.MagicMock(fetchone=lambda:
                           ("2026-09-25", "2026-09-27")),
            mock.MagicMock(fetchall=lambda:
                           [("sina", 1300), ("announcement", 300),
                            ("survey", 50), ("report", 60)]),
            mock.MagicMock(fetchone=lambda: (100,)),
        ]
        with mock.patch("data_health.sqlite3.connect", return_value=fake_conn):
            r = _dh._scan_news()
        self.assertTrue(r["has_announcement"])
        self.assertTrue(r["has_research"])
        self.assertTrue(r["has_report"])
        self.assertEqual(r["jev_pending"], 2300)

    def test_gaps_news_cleared_when_sources_present(self):
        """公告/调研/研报源齐备且 JEV 无积压 → 新闻类缺口全消失。"""
        import data_health as _dh
        kline = {"us": {"count": 5920, "exists": True},
                 "hk": {"count": 3355, "exists": True},
                 "crypto": {"count": 37, "exists": True}}
        news = {"exists": True, "has_announcement": True,
                "has_research": True, "has_report": True,
                "jev_pending": 50}
        self.assertEqual(_dh._build_gaps(kline, news), [])

    def test_gap_jev_backlog(self):
        """JEV 积压 > 100 报缺口。"""
        import data_health as _dh
        kline = {"us": {"count": 1, "exists": True},
                 "hk": {"count": 1, "exists": True},
                 "crypto": {"count": 1, "exists": True}}
        news = {"exists": True, "has_announcement": True,
                "has_research": True, "has_report": True,
                "jev_pending": 500}
        gaps = _dh._build_gaps(kline, news)
        self.assertEqual(len(gaps), 1)
        self.assertIn("JEV 积压 500", gaps[0])

    def test_warehouse_scan_structure(self):
        """scan_warehouse 返回 kpi + 6 组资产，rows 字段统一。"""
        import data_health as _dh
        _dh._WCACHE["t"], _dh._WCACHE["d"] = 0, None   # 清缓存
        fake_tdx = {
            "tables": [{"asset": "ts_daily", "rows": 4136843, "parts": 1,
                        "done": True, "size_mb": 180.0,
                        "latest": "2026-09-27 11:19", "age_hours": 0.5}],
            "info": [{"asset": "公告", "table": "stock_notice", "rows": 92414,
                      "latest": "2026-09-25", "path": "x"}],
            "sync_done": 1, "sync_total": 2, "db_size_mb": 155.1}
        with mock.patch("data_health._scan_kline", return_value={
                "count": 10, "exists": True, "path": "a",
                "size_mb": 1.0, "sh": 5}), \
             mock.patch("data_health._scan_news", return_value={
                 "exists": True, "total": 2000, "date_range": "a ~ b",
                 "sources": {"sina": 2000}, "jev_judged": 100,
                 "jev_pending": 1900}), \
             mock.patch("data_health._scan_tdx", return_value=fake_tdx), \
             mock.patch("data_health._scan_tushare", return_value={
                 "tables": [{"asset": "daily", "rows": 4100000, "parts": 2,
                             "done": True, "size_mb": 300.0,
                             "latest": "2026-09-27 08:00",
                             "age_hours": 1.0}],
                 "sync_done": 2, "sync_total": 2}), \
             mock.patch("data_health._scan_freshness", return_value={
                 "tables": [{"table": "daily", "live": "20260927",
                             "snap": "20260926", "lag_days": 0,
                             "ready": True, "status": "OK"}]}), \
             mock.patch("data_health._scan_artifacts", return_value=[]), \
             mock.patch("data_health._scan_integrity", return_value={
                 "metrics": [], "fail_count": 0, "warn_count": 0,
                 "expected_latest": "—"}):
            d = _dh.scan_warehouse()
        _dh._WCACHE["t"], _dh._WCACHE["d"] = 0, None   # 还原缓存
        for k in ("generated_at", "kpi", "groups", "gaps"):
            self.assertIn(k, d)
        kinds = [g["kind"] for g in d["groups"]]
        required = {"tdx", "tushare", "freshness", "integrity",
                    "kline", "news", "artifact"}
        self.assertTrue(required.issubset(set(kinds)))
        self.assertGreaterEqual(len(kinds), len(required))
        if "jev_reconciliation" in kinds:
            self.assertEqual(kinds[kinds.index("jev_reconciliation")],
                             "jev_reconciliation")
        self.assertEqual(d["kpi"]["tdx_sync"], "1/2")
        tdx_g = d["groups"][0]
        self.assertEqual(len(tdx_g["rows"]), 2)        # parquet 表 + info 表
        for r in tdx_g["rows"]:                        # 字段统一
            for k in ("asset", "rows", "latest", "size_mb", "age_hours"):
                self.assertIn(k, r)
        self.assertTrue(any(word in tdx_g["note"] for word in ("同步", "manifest")))

    def test_page_renders_warehouse(self):
        """资产总览页：KPI + 四组清单 + 新鲜度色标 + 产物下钻 + 缺口。"""
        fake = {
            "generated_at": "2026-09-27 10:00:00",
            "kpi": {"n_tables": 26, "total_mb": 520.3,
                    "tdx_sync": "11/15", "freshest": "0.1h 前"},
            "groups": [
                {"name": "TDX 远端仓", "kind": "tdx",
                 "note": "同步 11/15 表 · tdx_info.db 155.1 MB",
                 "rows": [
                     {"asset": "ts_daily", "rows": "413.7 万 (×1片)",
                      "latest": "2026-09-27 11:19", "size_mb": 180.0,
                      "age_hours": 0.5, "extra": "同步完成"},
                     {"asset": "ts_margin_detail", "rows": "267.8 万 (×2片)",
                      "latest": "2026-09-26 18:00", "size_mb": 90.0,
                      "age_hours": 100.0, "extra": "同步完成"},
                     {"asset": "公告（info.db）", "rows": "9.2 万",
                      "latest": "2026-09-25", "size_mb": 155.1,
                      "age_hours": None, "extra": None}]},
                {"name": "本地 K 线仓", "kind": "kline", "note": None,
                 "rows": [{"asset": "A股（5592 只）", "rows": "日线 parquet",
                           "latest": "2026-09-26", "size_mb": 245.0,
                           "age_hours": None,
                           "extra": "SH 2332 · SZ 2912 · BJ 348"}]},
                {"name": "新闻资讯", "kind": "news", "note": None,
                 "rows": [{"asset": "快讯（sina、eastmoney）", "rows": "2157",
                           "latest": "2026-09-27 11:08", "size_mb": None,
                           "age_hours": None,
                           "extra": "JEV 已判 679 · 积压 1478"}]},
                {"name": "策略产物", "kind": "artifact", "note": None,
                 "rows": [{"asset": "缠论预警池", "rows": "3.1 MB",
                           "latest": "2026-09-26 22:26", "size_mb": 3.1,
                           "age_hours": 12.0, "extra": None,
                           "url": "/bt-chanlun-pool"}]}],
            "gaps": ["美股 K线缺失", "调研源缺失"]}
        captured = []
        h = object.__new__(self.pa.Handler)
        h._html = captured.append
        state = {"data": fake, "error": None, "refreshing": False}
        with mock.patch.object(self.pa.api_data, "data_health_bg",
                               return_value=state), \
             mock.patch.object(self.pa.api_data, "data_freshness_bg",
                               return_value={"expect": "20260930", "rows": []}), \
             mock.patch.object(self.pa.api_data, "data_health_kg_bg",
                               return_value={"data": None, "refreshing": False,
                                             "error": None}):
            h._data_health_page()
        self.assertEqual(len(captured), 1)
        html = captured[0]
        # KPI 总览
        self.assertIn("数据仓 · 全部资产", html)
        self.assertIn("26", html)
        self.assertIn("520</b>MB", html)
        self.assertIn("11/15", html)
        # 四组资产清单
        for g in ("TDX 远端仓", "本地 K 线仓", "新闻资讯", "策略产物"):
            self.assertIn(g, html)
        self.assertIn("413.7 万", html)             # 规模列
        self.assertIn("同步 11/15", html)           # 组注释
        # 新鲜度三色：0.5h 绿 / 100h 红 / None 灰
        self.assertIn("var(--color-ok)", html)
        self.assertIn("var(--color-err)", html)
        self.assertIn("var(--text-dim)", html)
        # extra 列 + 产物下钻 + 缺口
        self.assertIn("JEV 已判 679", html)
        self.assertIn("href='/bt-chanlun-pool'", html)
        self.assertIn("缺口", html)
        self.assertIn("美股 K线缺失", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
