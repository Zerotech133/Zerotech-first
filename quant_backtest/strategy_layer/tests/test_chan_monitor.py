# -*- coding: utf-8 -*-
"""chan_monitor 适配器测试：mock 信号池 JSONL 文件测转换逻辑。"""
import json

import pytest

from strategy_layer.adapters.chan_monitor import ChanMonitorAdapter


def _write_pool(tmp_path, name, rows):
    """写一个假信号池 JSONL（可混入坏行）。"""
    p = tmp_path / name
    with open(p, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return str(p)


def _row(code="000338.SZ", sig_type="2买", dt="2026-10-15", **over):
    row = {
        "market": "CN", "code": code, "name": "潍柴动力", "period": "1d",
        "type": sig_type, "dt": dt, "price": 15.8,
        "detect_date": "2026-10-16", "lag": 1, "entry_px": 15.9,
        "mbei": None, "bei": None, "bei2": None, "vv": None,
        "reg": "up", "tbe": None, "trend": "up",
    }
    row.update(over)
    return row


def _adapter(pool_paths):
    return ChanMonitorAdapter(pool_globs=pool_paths)


class TestTypeMapping:
    @pytest.mark.parametrize("sig_type,score,action", [
        ("1买", 95.0, "buy"), ("2买", 85.0, "buy"), ("3买", 75.0, "buy"),
        ("3卖", 25.0, "sell"), ("2卖", 15.0, "sell"), ("1卖", 5.0, "sell"),
    ])
    def test_base_score_and_action(self, tmp_path, sig_type, score, action):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row(sig_type=sig_type)])
        sigs = _adapter([pool]).score("20261016")
        assert len(sigs) == 1
        assert sigs[0].score == score
        assert sigs[0].action == action
        assert sigs[0].strategy == "chan_monitor"
        assert sigs[0].trade_date == "20261016"
        assert sigs[0].symbol == "000338.SZ"

    def test_score_capped_at_100(self, tmp_path):
        # 95 + 4×2 = 103 → 封顶 100
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="1买", mbei=True, bei=True,
                                 bei2=True, vv=True)])
        assert _adapter([pool]).score("20261016")[0].score == 100.0


class TestResonance:
    def test_resonance_adds_score_and_confidence(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=True, bei2=True, vv=True,
                                 mbei=None)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 75.0 + 6.0
        assert s.confidence == 0.9  # 0.6 + 3×0.1

    def test_no_resonance_base_confidence(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 85.0
        assert s.confidence == 0.6


class TestDatePicking:
    def test_picks_latest_detect_batch(self, tmp_path):
        """Δ1：选日按 detect_date（最新扫描批次），与 picks 链口径一致。"""
        pool = _write_pool(tmp_path, "pool.jsonl", [
            _row(code="A.SH", dt="2026-10-10", detect_date="2026-10-14",
                 sig_type="3买"),
            _row(code="B.SH", dt="2026-10-15", detect_date="2026-10-16",
                 sig_type="1买"),
            _row(code="C.SH", dt="2026-10-15", detect_date="2026-10-16",
                 sig_type="2卖"),
        ])
        sigs = {s.symbol: s for s in _adapter([pool]).score("20261016")}
        assert set(sigs) == {"B.SH", "C.SH"}  # 只取最新扫描批次当日

    def test_picks_by_detect_date_not_dt(self, tmp_path):
        """Δ1 核心回归：旧信号日 + 新扫描批次要入选（dt 口径会漏）。"""
        pool = _write_pool(tmp_path, "pool.jsonl", [
            _row(code="A.SH", dt="2026-10-08", detect_date="2026-10-16",
                 sig_type="3买"),
            _row(code="B.SH", dt="2026-10-09", detect_date="2026-10-15",
                 sig_type="1买"),
        ])
        sigs = {s.symbol: s for s in _adapter([pool]).score("20261016")}
        assert set(sigs) == {"A.SH"}  # 取 10-16 扫描批次，非 10-09 信号日

    def test_exact_detect_date_match(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [
            _row(code="A.SH", dt="2026-10-15", detect_date="2026-10-16"),
            _row(code="B.SH", dt="2026-10-15", detect_date="2026-10-15"),
        ])
        sigs = {s.symbol: s for s in _adapter([pool]).score("20261016")}
        assert set(sigs) == {"A.SH"}  # detect_date 恰等于交易日上限

    def test_no_signal_before_trade_date(self, tmp_path, capsys):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(dt="2026-10-15", detect_date="2026-10-20")])
        assert _adapter([pool]).score("20261016") == []
        assert "降级" in capsys.readouterr().out

    def test_multi_market_pools_merged(self, tmp_path):
        cn = _write_pool(tmp_path, "pool.jsonl",
                         [_row(code="000338.SZ", dt="2026-10-15",
                               sig_type="2买")])
        us = _write_pool(tmp_path, "pool_us.jsonl",
                         [{"market": "US", "code": "NVDA", "name": "英伟达",
                           "type": "1买", "dt": "2026-10-15", "price": 180.5,
                           "detect_date": "2026-10-16",
                           "mbei": True, "bei": None, "bei2": None, "vv": True,
                           "trend": "up"}])
        sigs = {s.symbol: s for s in _adapter([cn, us]).score("20261016")}
        assert set(sigs) == {"000338.SZ", "NVDA"}
        assert sigs["NVDA"].score == 95.0 + 4.0
        assert sigs["NVDA"].confidence == 0.8  # 0.6 + 2×0.1

    def test_per_market_latest_detect_us_newer_than_cn(self, tmp_path):
        """QA-20261017 回归（detect_date 口径）：美股池 detect_date 较新时
        不得遮蔽 A 股信号（分市场选日）。"""
        cn = _write_pool(tmp_path, "pool.jsonl",
                         [_row(code="000338.SZ", dt="2026-09-25",
                               detect_date="2026-09-28", sig_type="1买"),
                          _row(code="000338.SZ", dt="2026-09-30",
                               detect_date="2026-09-30", sig_type="2买")])
        us = _write_pool(tmp_path, "pool_us.jsonl",
                         [{"market": "US", "code": "NXPI", "name": "NXP",
                           "type": "3卖", "dt": "2026-10-02", "price": 248.86,
                           "detect_date": "2026-10-02",
                           "mbei": None, "bei": None, "bei2": None,
                           "vv": None, "trend": "up"}])
        sigs = {s.symbol: s for s in _adapter([cn, us]).score("20261017")}
        # 两市场各取组内最新 detect_date：CN 组取 09-30 批次（而非被 10-02 遮蔽）
        assert set(sigs) == {"000338.SZ", "NXPI"}
        assert sigs["000338.SZ"].score == 85.0  # 2买 基准分
        assert sigs["NXPI"].score == 25.0       # 3卖 基准分

    def test_market_without_recent_rows_returns_empty(self, tmp_path):
        """某市场无 detect_date≤trade_date 行时该组为空，不影响其他组。"""
        cn = _write_pool(tmp_path, "pool.jsonl",
                         [_row(code="000338.SZ", dt="2026-10-15",
                               sig_type="2买")])
        us = _write_pool(tmp_path, "pool_us.jsonl",
                         [{"market": "US", "code": "NXPI", "name": "NXP",
                           "type": "3卖", "dt": "2026-11-01", "price": 248.86,
                           "detect_date": "2026-11-01",
                           "mbei": None, "bei": None, "bei2": None,
                           "vv": None, "trend": "up"}])
        sigs = {s.symbol: s for s in _adapter([cn, us]).score("20261016")}
        assert set(sigs) == {"000338.SZ"}


class TestToxicDowngrade:
    """Δ4 毒层降档（确认门① A 方案，口径同 picks 链 _chanlun_toxic）：
    3买∧bei=False / 2买∧trend=range → score 封顶 60、action 不变、
    rationale 标记；TOXIC_DOWNGRADE=False 一键回退。"""

    def test_toxic_3buy_bei_false_capped(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 60.0
        assert s.action == "buy"  # 仍在池中，低分沉底（语义同 picks 链 T2）

    def test_toxic_3buy_bei_false_with_resonance_capped(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=False, mbei=True, vv=True)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 60.0  # 75+4=79 → 仍封顶 60

    def test_toxic_2buy_range_capped(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="2买", trend="range")])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 60.0
        assert s.action == "buy"

    def test_toxic_rationale_marked(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert "毒层降档" in s.rationale
        assert "3买∧bei=False" in s.rationale

    def test_nontoxic_3buy_bei_true_unaffected(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=True)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 77.0  # 75+2，不封顶
        assert "毒层降档" not in s.rationale

    def test_nontoxic_3buy_bei_none_unaffected(self, tmp_path):
        """bei=None 不可判 → 保守不判毒（对齐 picks 链 P4 口径）。"""
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=None)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 75.0

    def test_nontoxic_2buy_trend_up_unaffected(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="2买", trend="up")])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 85.0

    def test_toxic_sell_unaffected(self, tmp_path):
        """卖点不判毒：3卖 bei=False 照常 25 分 sell。"""
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3卖", bei=False)])
        s = _adapter([pool]).score("20261016")[0]
        assert s.score == 25.0
        assert s.action == "sell"

    def test_toxic_switch_off_restores(self, tmp_path):
        """TOXIC_DOWNGRADE=False 一键回退：毒层买点恢复同权分。"""
        from strategy_layer.adapters import chan_monitor as cm
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(sig_type="3买", bei=False)])
        old = cm.TOXIC_DOWNGRADE
        try:
            cm.TOXIC_DOWNGRADE = False
            s = _adapter([pool]).score("20261016")[0]
            assert s.score == 75.0
            assert "毒层降档" not in s.rationale
        finally:
            cm.TOXIC_DOWNGRADE = old


class TestRationale:
    def test_rationale_human_readable(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [_row()])
        s = _adapter([pool]).score("20261016")[0]
        assert "缠论2买" in s.rationale
        assert "2026-10-15" in s.rationale
        assert "15.8" in s.rationale
        assert s.rationale.strip() != ""

    def test_rationale_includes_trend(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl",
                           [_row(trend="down", reg="down")])
        s = _adapter([pool]).score("20261016")[0]
        assert "down" in s.rationale


class TestDegradation:
    def test_missing_pool_returns_empty(self, tmp_path, capsys):
        adapter = _adapter([str(tmp_path / "not_exist.jsonl")])
        assert adapter.score("20261016") == []
        assert "降级" in capsys.readouterr().out

    def test_bad_lines_skipped(self, tmp_path):
        p = tmp_path / "pool.jsonl"
        good = json.dumps(_row(), ensure_ascii=False)
        p.write_text("{broken\n\n" + good + "\n", encoding="utf-8")
        sigs = _adapter([str(p)]).score("20261016")
        assert len(sigs) == 1

    def test_unknown_type_and_missing_code_skipped(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [
            _row(code="A.SH", sig_type="4买"),
            _row(code="", sig_type="1买"),
            _row(code="B.SH", sig_type="1卖"),
        ])
        sigs = _adapter([pool]).score("20261016")
        assert [s.symbol for s in sigs] == ["B.SH"]

    def test_never_raises_to_caller(self, tmp_path, capsys):
        # 目录当池文件读 → OSError → 降级
        d = tmp_path / "adir"
        d.mkdir()
        assert _adapter([str(d)]).score("20261016") == []
        capsys.readouterr()

    def test_sorted_by_score_desc(self, tmp_path):
        pool = _write_pool(tmp_path, "pool.jsonl", [
            _row(code="A.SH", sig_type="2卖", dt="2026-10-15"),
            _row(code="B.SH", sig_type="1买", dt="2026-10-15"),
        ])
        sigs = _adapter([pool]).score("20261016")
        assert [s.score for s in sigs] == sorted(
            [s.score for s in sigs], reverse=True)
