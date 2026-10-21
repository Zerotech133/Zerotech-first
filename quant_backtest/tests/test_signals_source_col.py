# -*- coding: utf-8 -*-
"""[20261021_wo-qt-072] /bt-signals 来源列 + 双池口径副标题 单元测试。

四态覆盖（todays_picks.json join）：
· join 命中   → 来源列显示策略名缩写列表（· 分隔）+ tier 徽标
· join 未命中 → 来源列灰色「观察名单」
· 文件缺失   → 整列降级「（票池数据不可用）」，页面照常出
· 坏 JSON    → 同上，不炸

另断言：表头含「来源」列、副标题含双池口径说明（观察名单/选票池）。

纪律：不触网、不读真实产物——PICKS_JSON/SIGNALS 全部替换为临时
文件；runner 依赖（load_bars 等）不涉及（直接测 report_page 渲染层，
rows 用内存假数据，零真实行情）。

运行：仓根 pytest quant_backtest/tests/test_signals_source_col.py -v
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))        # quant_backtest/tests
QBT = os.path.dirname(HERE)                              # quant_backtest
sys.path.insert(0, HERE)
sys.path.insert(0, QBT)

import report_page          # noqa: E402


def _tmpfile(suffix=".html"):
    fd, p = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    return p


def _write_picks(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        if isinstance(obj, str):
            f.write(obj)                      # 坏 JSON 用
        else:
            json.dump(obj, f, ensure_ascii=False)


class TestSignalsSourceCol(unittest.TestCase):
    """/bt-signals 来源列四态 + 表头 + 双池口径副标题。"""

    def setUp(self):
        self._orig_signals = report_page.SIGNALS
        self._orig_picks = report_page.PICKS_JSON
        self.sig_path = _tmpfile()
        self.picks_path = _tmpfile(suffix=".json")
        report_page.SIGNALS = self.sig_path
        report_page.PICKS_JSON = self.picks_path

    def tearDown(self):
        report_page.SIGNALS = self._orig_signals
        report_page.PICKS_JSON = self._orig_picks
        for p in (self.sig_path, self.picks_path):
            if os.path.exists(p):
                os.remove(p)

    def _render(self):
        out = report_page.write_signals_html(self._rows(), "2026-10-21 10:00")
        with open(out, encoding="utf-8") as f:
            return f.read()

    @staticmethod
    def _rows():
        return [
            {"name": "贵州茅台", "market": "CN", "px": 1452.0,
             "date": "2026-10-20", "symbol": "600519.SH",
             "sigs": {s: True for s in report_page.SIG_STRATS},
             "since": {s: "10-01" for s in report_page.SIG_STRATS},
             "since_full": {s: "2026-10-01" for s in report_page.SIG_STRATS},
             "res": 4},
            {"name": "义翘神州", "market": "CN", "px": 100.0,
             "date": "2026-10-20", "symbol": "301047.SZ",
             "sigs": {s: False for s in report_page.SIG_STRATS},
             "since": {s: "" for s in report_page.SIG_STRATS},
             "since_full": {s: "" for s in report_page.SIG_STRATS},
             "res": 0},
        ]

    # ── ① join 命中：策略缩写 + tier 徽标 ──────────────
    def test_join_hit_shows_sources_and_tier(self):
        _write_picks(self.picks_path, {"stocks": [
            {"code": "600519.SH", "name": "贵州茅台",
             "sources": [{"s": "chanlun"}, {"s": "chanlun"},  # 重复去重
                         {"s": "ninedim"}],
             "tier": "T1", "n_hit": 2}]})
        h = self._render()
        self.assertIn("chanlun·ninedim", h)       # 缩写列表 · 分隔
        self.assertIn("T1", h)                    # tier 徽标
        self.assertIn('class="tier t1"', h)
        self.assertIn("观察名单", h)              # 301047.SZ 未命中 → 灰
        # 多策略共享一行单元格（来源列内，非跨行拼凑）
        self.assertIn('class="src1"', h)

    def test_join_hit_dedup_keeps_order(self):
        """sources 重复策略去重且保持首次出现顺序。"""
        _write_picks(self.picks_path, {"stocks": [
            {"code": "600519.SH", "tier": "T2",
             "sources": [{"s": "b"}, {"s": "a"}, {"s": "b"}]}]})
        h = self._render()
        self.assertIn("b·a", h)
        self.assertNotIn("b·a·b", h)
        self.assertIn('class="tier t2"', h)

    # ── ② join 未命中 ─────────────────────────────────
    def test_join_miss_shows_watchlist(self):
        _write_picks(self.picks_path, {"stocks": [
            {"code": "000001.SZ", "sources": [{"s": "chanlun"}],
             "tier": "T2"}]})
        h = self._render()
        self.assertIn("观察名单", h)
        self.assertNotIn('class="src1"', h)       # 两票均未命中
        self.assertIn("chanlun", h)               # 票池数据本身仍被读入

    # ── ③ 文件缺失：整列降级，不炸 ─────────────────────
    def test_missing_file_degrades_whole_column(self):
        if os.path.exists(self.picks_path):
            os.remove(self.picks_path)
        h = self._render()                        # 不抛异常即通过一半
        self.assertIn("（票池数据不可用）", h)
        self.assertEqual(h.count("（票池数据不可用）"), 2)   # 整列每行占位
        self.assertNotIn('class="src1"', h)

    # ── ④ 坏 JSON：整列降级，不炸 ──────────────────────
    def test_bad_json_degrades_whole_column(self):
        _write_picks(self.picks_path, "{oops not json")
        h = self._render()
        self.assertIn("（票池数据不可用）", h)
        self.assertNotIn('class="src1"', h)

    def test_missing_fields_degrade_gracefully(self):
        """stocks 存在但缺 sources/tier 字段 → 观察名单灰显，不炸。"""
        _write_picks(self.picks_path, {"stocks": [
            {"code": "600519.SH"},                # 无 sources/tier
            {"code": "301047.SZ", "sources": [], "tier": ""}]})
        h = self._render()
        self.assertIn("观察名单", h)
        self.assertNotIn('class="src1"', h)

    # ── 表头 + 双池口径副标题 ──────────────────────────
    def test_header_has_source_col(self):
        h = self._render()
        self.assertIn("<th>来源</th>", h)
        self.assertLess(h.index("<th>标的</th>"), h.index("<th>来源</th>"))
        self.assertLess(h.index("<th>来源</th>"), h.index("<th>市场</th>"))

    def test_subtitle_dual_pool(self):
        _write_picks(self.picks_path, {"stocks": [
            {"code": "600519.SH", "sources": [{"s": "chanlun"}],
             "tier": "T1"}]})
        h = self._render()
        self.assertIn("本页=观察名单(2)信号检查", h)     # 观察名单口径+只数
        self.assertIn("/picks.html", h)                  # 选票池入口
        self.assertIn("只带来源评分", h)
        self.assertIn("1 只带来源评分", h)               # 票池只数动态

    def test_subtitle_when_picks_unavailable(self):
        if os.path.exists(self.picks_path):
            os.remove(self.picks_path)
        h = self._render()
        self.assertIn("本页=观察名单(2)信号检查", h)
        self.assertIn("— 只带来源评分", h)               # 不可用→只数占位

    # ── 纯函数单测 ────────────────────────────────────
    def test_load_picks_index_direct(self):
        _write_picks(self.picks_path, {"stocks": [
            {"code": "600519.SH", "sources": [{"s": "chanlun"},
                                              {"s": "9dim"}],
             "tier": "T1"},
            {"code": None},                               # 脏行跳过
            "not-a-dict"]}                                # 脏行跳过
        )
        idx, ok = report_page._load_picks_index()
        self.assertTrue(ok)
        self.assertEqual(idx, {"600519.SH": ("chanlun·9dim", "T1")})


if __name__ == "__main__":
    unittest.main(verbosity=2)
