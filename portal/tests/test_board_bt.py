# -*- coding: utf-8 -*-
"""板块分析与回测（board_bt / api_board_bt）单测 —— 2026-11-09。

覆盖：
1. 选股纯函数 `select_picks`：涨幅前 K / 涨停股 / K 越界钳制 / 全空输入；
2. 净值与统计 `metrics` / `_nav_series`：空样本、按卖出日分组、复利与回撤；
3. 接口参数校验：路径、board、day、k、hold、mode、cost 七类非法输入必须 400
   （回测是只读但昂贵，参数错了不能默默跑一遍 311MB）；
4. 页面契约：独立页必须挂 tokens.css / os_theme.css / nav.js（UI 宪法 §10 页面
   清单要求），且真的调 `/api/board/backtest`——不能是个只有图的空壳。

全程不读真实数据：`select_picks` / `metrics` / 参数校验都是纯逻辑，
重数据路径（`_concept_frame` / `_market_panel`）由端到端实跑覆盖，不在单测里重复。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

PORTAL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PORTAL))

import api_board_bt as api  # noqa: E402
import board_bt as B  # noqa: E402


# ── 夹具 ──────────────────────────────────────────────────
def _row(code, pct, limit_up=False):
    return {"code": code, "pct": pct, "limit_up": limit_up}


class _FakeHandler:
    def __init__(self):
        self.body = None
        self.status = None

    def _json(self, obj, status=200):
        self.body, self.status = obj, status


def _call(path, qs=""):
    h = _FakeHandler()
    api.handle_get(h, path, qs)
    return h


# ── 0. 涨停档位向量化版 vs jev_graph 逐条对账 ──────────────
class TestLimitRatio:
    """两套实现（向量化 / 逐行）必须给出同一档位。

    判例 #25 的坑就是"同一个口径写了两份、各走各的"。这里用机检代替记性：
    主板 10% / 创业板+科创 20% / 北交所 30% / 主板 ST 5% / 撤 ST 未改名按涨幅反推 10%。
    """
    CASES = [
        ("600000", "浦发银行", 1.0), ("300001", "特锐德", 2.0),
        ("301001", "凯淳股份", 1.0), ("688001", "华兴源创", 3.0),
        ("689009", "九号公司", 1.5), ("830001", "北交某", 4.0),
        ("920001", "北交另一", 5.0), ("600001", "ST某某", 5.0),
        ("600002", "*ST亿晶", 10.22), ("300002", "ST创业", 19.9),
    ]

    def test_vectorized_matches_rowwise(self):
        import jev_graph as G
        c6 = pd.Series([c for c, _, _ in self.CASES])
        nm = pd.Series([n for _, n, _ in self.CASES])
        pct = pd.Series([p for _, _, p in self.CASES])
        got = B.limit_ratio_series(c6, nm, pct).tolist()
        want = [G.limit_ratios(c, n, p)[0] for c, n, p in self.CASES]
        assert got == want


# ── 1. select_picks ───────────────────────────────────────class TestSelectPicks:
    def test_top_k_by_pct(self):
        rows = [_row("A", 3.0), _row("B", 9.9), _row("C", 5.5), _row("D", -1.0)]
        assert B.select_picks(rows, mode="top", k=2) == [("B", 9.9), ("C", 5.5)]

    def test_top_ignores_null_pct(self):
        rows = [_row("A", None), _row("B", 1.0)]
        assert B.select_picks(rows, mode="top", k=3) == [("B", 1.0)]

    def test_empty_rows(self):
        assert B.select_picks([], mode="top", k=3) == []
        assert B.select_picks([], mode="limitup", k=3) == []

    def test_limitup_only_limit_up(self):
        rows = [_row("A", 10.0, True), _row("B", 9.8, False), _row("C", 5.0, True)]
        got = B.select_picks(rows, mode="limitup", k=1)
        assert [c for c, _ in got] == ["A", "C"]      # k*3 上限，涨停不足就全给

    def test_limitup_cap_is_three_times_k(self):
        rows = [_row(str(i), 10.0, True) for i in range(10)]
        assert len(B.select_picks(rows, mode="limitup", k=2)) == 6

    def test_k_zero_still_returns_one(self):
        rows = [_row("A", 3.0), _row("B", 2.0)]
        assert B.select_picks(rows, mode="top", k=0) == [("A", 3.0)]


# ── 2. 净值与统计 ─────────────────────────────────────────
class TestMetrics:
    def test_empty(self):
        m = B.metrics([])
        assert m["n"] == 0 and m["total_return"] == 0.0 and m["equity"] == []

    def test_groups_by_exit_day(self):
        pairs = [("20261102", 0.10), ("20261102", -0.10), ("20261103", 0.05)]
        eq, nav, mdd = B._nav_series(pairs)
        assert len(eq) == 2
        assert eq[0]["n"] == 2 and abs(eq[0]["day_ret"]) < 1e-9   # 同日等权平均
        assert abs(eq[1]["nav"] - 1.05) < 1e-9                    # 1.0 × 1.05
        assert mdd <= 0

    def test_metrics_basic_fields(self):
        trades = [
            {"exit_day": "20261102", "ret": 0.02, "hold_days": 3},
            {"exit_day": "20261103", "ret": -0.01, "hold_days": 4},
        ]
        m = B.metrics(trades, cost_bps=20.0)
        assert m["n"] == 2
        assert m["win_rate"] == 0.5
        assert m["best"] == 0.02 and m["worst"] == -0.01
        assert abs(m["total_return"] - (1.02 * 0.99 - 1)) < 1e-6
        assert m["avg_hold"] == 3.5
        assert m["cost_bps"] == 20.0

    def test_all_losses_mdd_is_nav_drop(self):
        trades = [{"exit_day": "2026110%d" % i, "ret": -0.10, "hold_days": 3}
                  for i in range(1, 4)]
        m = B.metrics(trades)
        assert m["win_rate"] == 0.0
        assert m["total_return"] < -0.27          # 0.9^3 ≈ 0.729
        assert m["mdd"] < -0.27

    def test_annual_scales_with_trading_days_not_equity_points(self):
        """年化按交易日折算：3 个净值点 ≠ 3 个交易日（首次上线把 −15.8% 吹成 −93%）。"""
        trades = [{"exit_day": "2026110%d" % i, "ret": -0.05, "hold_days": 3}
                  for i in range(1, 4)]
        short = B.metrics(trades, days_span=10)
        long_ = B.metrics(trades, days_span=100)
        assert short["annual"] < long_["annual"] < 0


# ── 3. 接口参数校验（全部离线）────────────────────────────
class TestApiValidation:
    def test_unknown_path(self):
        h = _call("/api/board/nope")
        assert h.status == 400 and h.body["ok"] is False

    def test_missing_board(self):
        h = _call("/api/board/analyze", "day=20261106")
        assert h.status == 400
        assert "board" in h.body["message"]

    def test_bad_day_format(self):
        h = _call("/api/board/list", "day=2026-11-06")
        assert h.status == 400 and "YYYYMMDD" in h.body["message"]

    def test_k_out_of_range(self):
        h = _call("/api/board/backtest", "board=x&k=99")
        assert h.status == 400 and "k" in h.body["message"]

    def test_cost_out_of_range(self):
        h = _call("/api/board/backtest", "board=x&cost_bps=999")
        assert h.status == 400 and "cost_bps" in h.body["message"]

    def test_mode_invalid(self):
        h = _call("/api/board/backtest", "board=x&mode=chase")
        assert h.status == 400 and "mode" in h.body["message"]

    def test_top_out_of_range(self):
        h = _call("/api/board/list", "top=0")
        assert h.status == 400 and "top" in h.body["message"]

    def test_board_too_long(self):
        h = _call("/api/board/analyze", "board=" + "电" * 40)
        assert h.status == 400


# ── 4. 页面契约 ───────────────────────────────────────────
class TestPageContract:
    PAGE = PORTAL / "board_bt.html"

    def test_theme_and_nav_deps(self):
        s = self.PAGE.read_text(encoding="utf-8")
        for need in ("/static/tokens.css", "/static/os_theme.css", "/static/nav.js"):
            assert need in s, "页面清单要求：%s 必须挂载" % need

    def test_calls_backtest_and_analyze(self):
        s = self.PAGE.read_text(encoding="utf-8")
        assert "/api/board/backtest" not in s      # 前端用 API 前缀 + 子路径拼接
        assert 'API = "/api/board"' in s
        assert 'jget("/backtest"' in s and 'jget("/analyze"' in s

    def test_shows_skipped_and_limits(self):
        s = self.PAGE.read_text(encoding="utf-8")
        assert "一字板" in s and "剔除" in s           # 不可成交必须如实展示
        assert "不构成投资建议" in s
