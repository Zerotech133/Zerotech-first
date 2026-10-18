"""容量推导器（架构稿 §3 公式代码化）+ 启动自检 CapacityGuard。

确定性层公式：S × M × c_eval ≤ 周期预算 × η
  - S 标的数 × M 方法数 × c_eval(ms/次) ≤ period_ms × 0.6
  - 超配 fail-fast，并打印建议分片数（market 一级 + symbol 二级分片）。
LLM 层预算（660 判定/h）在 LlmArbiter 令牌桶内执行，不在此重复。
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from zhice_os_v2.agent_platform.config import CapacityConfig


@dataclass
class CapacityReport:
    """单次容量自检结果。"""

    ok: bool
    symbols: int
    methods: int
    period_key: str
    budget_ms: float               # period_ms × η
    required_ms: float             # S×M×c_eval
    utilization: float             # required / budget
    max_symbols_per_agent: int     # floor(budget / (M×c_eval))
    suggested_shards: int          # ceil(S / max_symbols_per_agent)
    detail: dict = field(default_factory=dict)

    def summary_line(self) -> str:
        state = "PASS" if self.ok else "FAIL(超配，拒绝启动)"
        return (
            f"[CapacityGuard] {state} S={self.symbols} M={self.methods} "
            f"档位={self.period_key} 需求={self.required_ms:.0f}ms/周期 "
            f"预算={self.budget_ms:.0f}ms/周期(η水位) 利用率={self.utilization:.1%} "
            f"单Agent容量={self.max_symbols_per_agent}标的 建议分片={self.suggested_shards}"
        )


class CapacityGuard:
    """Agent 启动必跑 check()；超配 fail-fast（raise CapacityExceededError）。"""

    def __init__(self, cfg: CapacityConfig | None = None) -> None:
        self.cfg = cfg or CapacityConfig()
        self._measured_c_eval_ms: float | None = None

    # ── 公式 ──────────────────────────────────────────────────────────────
    def check(self, symbols: int, methods: int, hz: str | float) -> CapacityReport:
        """S×M×c_eval ≤ 周期预算×η 的边界判定。

        hz 接受档位键（'5s'/'10s'/'3m'）或周期秒数（float）。
        c_eval 优先用 benchmark() 实测值，未实测用配置保守值。
        """
        period_key, period_ms = self._resolve_period(hz)
        c_eval = self._measured_c_eval_ms or self.cfg.c_eval_ms
        budget_ms = period_ms * self.cfg.eta
        required_ms = float(symbols) * float(methods) * c_eval
        max_symbols = int(budget_ms // (methods * c_eval)) if methods > 0 else 0
        shards = math.ceil(symbols / max_symbols) if max_symbols > 0 else math.inf
        return CapacityReport(
            ok=required_ms <= budget_ms,
            symbols=symbols,
            methods=methods,
            period_key=period_key,
            budget_ms=budget_ms,
            required_ms=required_ms,
            utilization=(required_ms / budget_ms) if budget_ms > 0 else math.inf,
            max_symbols_per_agent=max_symbols,
            suggested_shards=int(shards) if shards != math.inf else -1,
            detail={"c_eval_ms": c_eval, "eta": self.cfg.eta},
        )

    def check_or_raise(self, symbols: int, methods: int, hz: str | float) -> CapacityReport:
        report = self.check(symbols, methods, hz)
        if not report.ok:
            raise CapacityExceededError(report)
        return report

    # ── 实测回填（架构稿 §6.5-1：c_eval=10ms 是保守假设） ─────────────────
    def benchmark(self, n_runs: int = 50, n_bars: int = 200) -> float:
        """用合成 K 线 fixture 实测单次方法评估耗时，回填真实 c_eval。

        返回实测毫秒数并缓存（后续 check() 用实测值）。
        """
        from zhice_os_v2.agent_platform.methods import default_registry
        from zhice_os_v2.agent_platform.methods.base import WatchContext
        from zhice_os_v2.agent_platform.tests_support import synth_bars

        df = synth_bars(n_bars, seed=7)
        ctx = WatchContext(
            ts_code="BENCH/USDT",
            market="CRYPTO",
            snapshot_tail=df.tail(5),
            bars_df=df,
            regime_risk_off=False,
            memory_hints=[],
        )
        registry = default_registry()
        methods = registry.all()
        # 预热（JIT 无关，排除首次 import/pandas 首算开销）
        for m in methods:
            m.evaluate(ctx)
        t0 = time.perf_counter()
        for _ in range(n_runs):
            for m in methods:
                m.evaluate(ctx)
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        self._measured_c_eval_ms = elapsed_ms / (n_runs * max(1, len(methods)))
        return self._measured_c_eval_ms

    # ── 内部 ─────────────────────────────────────────────────────────────
    def _resolve_period(self, hz: str | float) -> tuple[str, float]:
        if isinstance(hz, str):
            key = hz.strip().lower()
            if key in self.cfg.period_ms:
                return key, self.cfg.period_ms[key]
            # 形如 '30s' / '2m' 的未登记档位：秒数解析
            try:
                return key, float(key.rstrip("s")) * 1000.0
            except ValueError:
                raise ValueError(f"未知节奏档位: {hz!r}，可选: {list(self.cfg.period_ms)}") from None
        return f"{hz:g}s", float(hz) * 1000.0


class CapacityExceededError(RuntimeError):
    """超配 fail-fast：携带 CapacityReport 供上层打印建议分片数。"""

    def __init__(self, report: CapacityReport) -> None:
        super().__init__(report.summary_line())
        self.report = report
