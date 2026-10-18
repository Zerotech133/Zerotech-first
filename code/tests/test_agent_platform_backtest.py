"""回测引擎端到端测试：2 标的 × 200 根合成 bar 跑通引擎并产出报告文件（无 DB 依赖）。"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from zhice_os_v2.agent_platform.backtest.engine import BacktestEngine, BacktestResult
from zhice_os_v2.agent_platform.backtest.report import compute_stats, to_markdown, write_reports
from zhice_os_v2.agent_platform.config import AgentPlatformConfig
from zhice_os_v2.agent_platform.methods import create_default_registry
from zhice_os_v2.agent_platform.tests_support import synth_bars


@pytest.fixture
def bars_by_symbol() -> dict[str, pd.DataFrame]:
    return {
        "AAA/USDT": synth_bars(200, seed=11, pattern="chan_div"),
        "BBB/USDT": synth_bars(200, seed=42, pattern="neutral"),
    }


@pytest.fixture
def engine() -> BacktestEngine:
    cfg = AgentPlatformConfig()
    cfg.warmup_bars = 60
    cfg.window_bars = 120
    return BacktestEngine(cfg, registry=create_default_registry())


class TestBacktestEngine:
    def test_run_produces_decisions(self, engine, bars_by_symbol):
        result = engine.run_on_bars(bars_by_symbol)
        assert isinstance(result, BacktestResult)
        assert result.total_bars == 2 * (200 - 60)     # 每标的 140 根重放
        assert len(result.decisions) == result.total_bars
        # 决策结构完整
        d = result.decisions[0]
        assert d["decision_id"].startswith("aw-")
        assert d["action"] in ("SELL", "HOLD", "WAIT")
        assert d["arbitration_path"] in ("WEIGHTED", "REGIME_VETO", "LLM", "DROPPED")
        assert len(d["votes_json"]) == 7      # T07 满编（3 原生 + 4 CN）

    def test_divergence_symbol_produces_sell(self, engine, bars_by_symbol):
        """divergence fixture 标的至少产出一个 SELL 决策。"""
        result = engine.run_on_bars(bars_by_symbol)
        sells = [d for d in result.decisions if d["ts_code"] == "AAA/USDT" and d["action"] == "SELL"]
        assert sells, "背驰 fixture 标的应至少触发一次 SELL"

    def test_fwd_ret_backfilled(self, engine, bars_by_symbol):
        """T+5 前向收益回填（除尾部 5 根外全部有值）。"""
        result = engine.run_on_bars(bars_by_symbol)
        with_fwd = [d for d in result.decisions if d.get("fwd_ret_5d") is not None]
        assert len(with_fwd) >= result.total_bars - 2 * 5
        # 收益率量级合理（合成数据 ±20% 内）
        assert all(abs(d["fwd_ret_5d"]) < 0.5 for d in with_fwd)

    def test_llm_mode_is_mock(self, engine, bars_by_symbol):
        result = engine.run_on_bars(bars_by_symbol)
        assert result.llm_mock is True
        assert result.llm_real_calls == 0
        for d in result.decisions:
            if d["arbitration_path"] == "LLM":
                assert d["model_verdict"]["source"] == "MOCK"

    def test_performance_budget(self, engine, bars_by_symbol):
        """单 bar 均耗远低于性能预算线（10.6 万 bar / 5 分钟 → ≈2.8ms/bar 上限）。"""
        result = engine.run_on_bars(bars_by_symbol)
        assert result.avg_ms_per_bar < 2.8, f"单bar均耗 {result.avg_ms_per_bar}ms 超预算"

    def test_regime_fn_injection(self, engine, bars_by_symbol):
        """注入恒 risk_off 的 regime_fn → 决策带 regime_risk_off=True。"""
        result = engine.run_on_bars(bars_by_symbol, regime_fn=lambda d, m: False)
        assert all(d["regime_risk_off"] for d in result.decisions)


class TestBacktestReport:
    def test_write_reports_creates_files(self, engine, bars_by_symbol, tmp_path):
        result = engine.run_on_bars(bars_by_symbol)
        md_path, json_path, stats = write_reports(result, out_dir=tmp_path, tag="test")
        assert md_path.exists() and json_path.exists()
        assert md_path.suffix == ".md" and json_path.suffix == ".json"

        md_text = md_path.read_text(encoding="utf-8")
        assert "回测报告" in md_text
        assert "仲裁路径分布" in md_text
        assert "分方法票准确率" in md_text

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["stats"]["decision_total"] == stats["decision_total"]
        assert len(payload["decisions"]) == len(result.decisions)

    def test_stats_structure(self, engine, bars_by_symbol):
        result = engine.run_on_bars(bars_by_symbol)
        stats = compute_stats(result)
        for key in ("path_distribution", "action_distribution", "conflict_rate",
                    "method_accuracy", "sell_decision_t5_accuracy", "avg_ms_per_bar"):
            assert key in stats
        assert set(stats["path_distribution"]) == {"WEIGHTED", "REGIME_VETO", "LLM", "DROPPED"}
        assert 0.0 <= stats["conflict_rate"] <= 1.0
        # 分方法准确率三方法齐备
        assert set(stats["method_accuracy"]) >= {"chan", "volume", "rsi_spec"}

    def test_empty_result_stats(self):
        """空决策也能出报告（不崩）。"""
        empty = BacktestResult(
            market="CRYPTO", freq="15m", requested_freq="15m", freq_fallback=False,
            fallback_reason="", symbols=[], start="2026-09-01T00:00:00+00:00",
            end="2026-09-30T00:00:00+00:00",
        )
        stats = compute_stats(empty)
        assert stats["decision_total"] == 0
        md = to_markdown(stats)
        assert "回测报告" in md

    def test_default_report_dir_resolves(self):
        cfg = AgentPlatformConfig()
        p = Path(cfg.report_dir)
        assert p.name == "agent_platform_reports"


class TestDataLoaderFreqMap:
    def test_freq_map_covers_three_tiers(self):
        from zhice_os_v2.agent_platform.backtest.data_loader import FREQ_ORDER, FREQ_RESAMPLE_RULE

        assert FREQ_ORDER == ["5m", "15m", "1h"]
        for f in FREQ_ORDER:
            assert f in FREQ_RESAMPLE_RULE


class TestArbiterVsEngineConsistency:
    def test_engine_uses_three_methods(self, engine, bars_by_symbol):
        result = engine.run_on_bars(bars_by_symbol)
        # T07 满编：3 原生 + 4 CN（chan_structure 别名与 chan 同语义独立 id）
        assert sorted(result.method_ids) == [
            "chan", "chan_structure", "event_window", "rsi_spec",
            "sentiment_regime", "trend_break", "volume",
        ]
        votes_methods = {v["method_id"] for d in result.decisions for v in d["votes_json"]}
        assert votes_methods == set(result.method_ids)


class TestDailyCoverage:
    """P2b 回归：按天覆盖统计（全日历口径 + 零 bar 天断档透明化）。"""

    def _result_with_days(self, bar_days: dict[str, int], symbols: int = 1) -> BacktestResult:
        days = sorted(bar_days.keys())
        decisions = []
        for day, n in bar_days.items():
            for i in range(n):
                decisions.append({
                    "decision_id": f"aw-{day}-{i}",
                    "ts_code": "X/USDT", "market": "CRYPTO",
                    "bar_time": f"{day}T00:00:00+00:00",
                    "action": "WAIT", "score_sell": 0.0, "score_hold": 0.0,
                    "votes_json": [], "regime_risk_off": False,
                    "arbitration_path": "WEIGHTED", "model_verdict": None,
                    "status": "ARCHIVED", "fwd_ret_5d": None,
                    "created_at": day, "note": "",
                })
        return BacktestResult(
            market="CRYPTO", freq="15m", requested_freq="15m",
            freq_fallback=False, fallback_reason="",
            symbols=[f"S{i}/USDT" for i in range(symbols)],
            start=f"{days[0]}T00:00:00+00:00",
            end=f"{days[-1]}T23:59:00+00:00",
            decisions=decisions,
        )

    def test_full_coverage_no_gap(self):
        from zhice_os_v2.agent_platform.backtest.report import BARS_PER_DAY, compute_stats

        theory = BARS_PER_DAY["15m"]   # 96 bar/天 × 1 标的
        result = self._result_with_days({
            "2026-10-01": theory, "2026-10-02": theory, "2026-10-03": theory,
        })
        stats = compute_stats(result)
        cov = stats["daily_coverage"]
        assert cov["overall_coverage"] == pytest.approx(1.0, abs=1e-6)
        assert cov["gap_days"] == []

    def test_zero_bar_day_marked_as_gap(self):
        """中间整日零 bar（ETL 断档）必须显式出现在表中并标注断档。"""
        from zhice_os_v2.agent_platform.backtest.report import BARS_PER_DAY, compute_stats

        theory = BARS_PER_DAY["15m"]
        result = self._result_with_days({
            "2026-10-01": theory,
            "2026-10-02": 0,        # 整日缺数据
            "2026-10-03": theory,
        })
        stats = compute_stats(result)
        cov = stats["daily_coverage"]
        assert "2026-10-02" in cov["gap_days"]
        day_rows = {r["date"]: r for r in cov["per_day"]}
        assert day_rows["2026-10-02"]["bars"] == 0
        assert day_rows["2026-10-02"]["data_gap"] is True
        # 全日历口径：2×96 实际 / 3×96 理论 = 2/3
        assert cov["overall_coverage"] == pytest.approx(2 / 3, abs=1e-4)

    def test_boundary_days_not_flagged(self):
        """首末日（warmup/窗口边界）天然部分覆盖，不计断档。"""
        from zhice_os_v2.agent_platform.backtest.report import BARS_PER_DAY, compute_stats

        theory = BARS_PER_DAY["15m"]
        result = self._result_with_days({
            "2026-10-01": theory // 2,   # 边界日：50%
            "2026-10-02": theory,
            "2026-10-03": theory // 2,   # 边界日：50%
        })
        stats = compute_stats(result)
        cov = stats["daily_coverage"]
        assert cov["gap_days"] == []
        rows = {r["date"]: r for r in cov["per_day"]}
        assert rows["2026-10-01"]["boundary_day"] is True
        assert rows["2026-10-02"]["boundary_day"] is False

    def test_partial_day_below_threshold_flagged(self):
        from zhice_os_v2.agent_platform.backtest.report import BARS_PER_DAY, compute_stats

        theory = BARS_PER_DAY["15m"]
        result = self._result_with_days({
            "2026-10-01": theory,
            "2026-10-02": int(theory * 0.5),   # 50% < 85% → 断档
            "2026-10-03": theory,
        })
        stats = compute_stats(result)
        assert stats["daily_coverage"]["gap_days"] == ["2026-10-02"]

    def test_markdown_renders_coverage(self):
        from zhice_os_v2.agent_platform.backtest.report import (
            BARS_PER_DAY,
            compute_stats,
            to_markdown,
        )

        theory = BARS_PER_DAY["15m"]
        result = self._result_with_days({
            "2026-10-01": theory, "2026-10-02": 0, "2026-10-03": theory,
        })
        md = to_markdown(compute_stats(result))
        assert "数据覆盖率" in md
        assert "按天数据覆盖" in md
        assert "**数据断档**" in md
