"""容量公式边界测试：超配拒绝 / 水位内通过 / 档位解析 / 分片建议。"""
from __future__ import annotations

import pytest

from zhice_os_v2.agent_platform.capacity import (
    CapacityExceededError,
    CapacityGuard,
    CapacityReport,
)
from zhice_os_v2.agent_platform.config import CapacityConfig


class TestCapacityBoundary:
    def test_within_watermark_passes(self):
        """§3.2 表：5s 档 M=3 → 100 标的恰好在水位内（3000ms 预算，需求 3000ms ≤）。"""
        guard = CapacityGuard(CapacityConfig(c_eval_ms=10.0, eta=0.6))
        report = guard.check(symbols=100, methods=3, hz="5s")
        assert report.ok is True
        assert report.max_symbols_per_agent == 100
        assert report.suggested_shards == 1

    def test_over_watermark_fails(self):
        """101 标的 × 3 方法 → 3030ms > 3000ms 预算 → 超配拒绝。"""
        guard = CapacityGuard(CapacityConfig(c_eval_ms=10.0, eta=0.6))
        report = guard.check(symbols=101, methods=3, hz="5s")
        assert report.ok is False
        assert report.utilization > 1.0

    def test_check_or_raise_failfast(self):
        guard = CapacityGuard(CapacityConfig(c_eval_ms=10.0, eta=0.6))
        with pytest.raises(CapacityExceededError) as exc_info:
            guard.check_or_raise(symbols=500, methods=3, hz="5s")
        report = exc_info.value.report
        assert report.suggested_shards >= 5   # 500/100 → 至少 5 分片

    def test_10s_market_cn_capacity(self):
        """CN 10s 档 M=3 → 200 标的在水位内。"""
        guard = CapacityGuard(CapacityConfig(c_eval_ms=10.0, eta=0.6))
        report = guard.check(symbols=200, methods=3, hz="10s")
        assert report.ok is True

    def test_hz_seconds_direct(self):
        """hz 支持直接给秒数。"""
        guard = CapacityGuard(CapacityConfig(c_eval_ms=10.0, eta=0.6))
        report = guard.check(symbols=100, methods=3, hz=5.0)
        assert report.ok is True
        assert report.period_key == "5s"

    def test_unknown_hz_raises(self):
        guard = CapacityGuard()
        with pytest.raises(ValueError):
            guard.check(symbols=10, methods=3, hz="bogus")

    def test_report_summary_line(self):
        guard = CapacityGuard(CapacityConfig(c_eval_ms=10.0, eta=0.6))
        report = guard.check(symbols=37, methods=3, hz="5s")
        assert "PASS" in report.summary_line()
        assert "37" in report.summary_line()

    def test_real_c_eval_measured_smaller_allows_more(self):
        """实测 c_eval=2ms 时 5s 档容量扩到 500 标的。"""
        guard = CapacityGuard(CapacityConfig(c_eval_ms=2.0, eta=0.6))
        report = guard.check(symbols=500, methods=3, hz="5s")
        assert report.ok is True
        assert report.max_symbols_per_agent == 500

    def test_benchmark_backfills_c_eval(self):
        """benchmark() 实测回填（合成 fixture，无 DB）。"""
        guard = CapacityGuard()
        ms = guard.benchmark(n_runs=5)
        assert 0.01 < ms < 500.0        # 合理量级（远快于线上含 IO 的路径也算通过型断言）
        report = guard.check(symbols=37, methods=3, hz="5s")
        assert report.detail["c_eval_ms"] == ms

    def test_capacity_report_dataclass_defaults(self):
        rep = CapacityReport(ok=True, symbols=1, methods=1, period_key="5s",
                             budget_ms=1.0, required_ms=1.0, utilization=1.0,
                             max_symbols_per_agent=1, suggested_shards=1)
        assert rep.detail == {}
