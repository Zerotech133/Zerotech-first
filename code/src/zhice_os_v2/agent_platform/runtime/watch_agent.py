"""WatchAgent — 单分片盯盘主循环（每周期：读快照 → 构造 ctx → 投票 → 仲裁 → 落库）。

v1 为影子运行形态：只出决策不发卡片（CardBridge 由 T04 接入）。
启动必跑 CapacityGuard.check_or_raise（超配 fail-fast）。
"""
from __future__ import annotations

import logging
import time

from zhice_os_v2.agent_platform import metrics
from zhice_os_v2.agent_platform.arbitration.llm_arbiter import MockLlmArbiter
from zhice_os_v2.agent_platform.arbitration.regime_arbiter import AgentDecision, RegimeArbiter
from zhice_os_v2.agent_platform.capacity import CapacityGuard, CapacityReport
from zhice_os_v2.agent_platform.config import AgentPlatformConfig, utc_now
from zhice_os_v2.agent_platform.db import DecisionRecorder
from zhice_os_v2.agent_platform.methods import MethodRegistry, WatchContext, default_registry
from zhice_os_v2.agent_platform.runtime.shard import ShardPolicy

logger = logging.getLogger(__name__)

# 节奏档位 → 周期秒
HZ_SECONDS = {"5s": 5.0, "10s": 10.0, "3m": 180.0}


class WatchAgent:
    """盯盘 Agent（单分片）。run_cycle() 幂等，可被任意调度器驱动。"""

    def __init__(
        self,
        shard: ShardPolicy,
        cfg: AgentPlatformConfig | None = None,
        registry: MethodRegistry | None = None,
        arbiter: RegimeArbiter | None = None,
        recorder: DecisionRecorder | None = None,
        capacity_guard: CapacityGuard | None = None,
        agent_id: str = "",
    ) -> None:
        self.agent_id = agent_id or f"agent-{shard.market.lower()}-{utc_now():%H%M%S}"
        self.shard = shard
        self.cfg = cfg or AgentPlatformConfig()
        self.registry = registry or default_registry()
        self.arbiter = arbiter or RegimeArbiter(
            llm_arbiter=MockLlmArbiter(cfg=self.cfg.llm),
            cfg=self.cfg.arbitration,
            position_value_threshold=self.cfg.llm.position_value_threshold,
        )
        self.recorder = recorder or DecisionRecorder()
        self.guard = capacity_guard or CapacityGuard(self.cfg.capacity)
        self.last_cycle_seconds = 0.0
        self.cycles = 0
        self._capacity_report: CapacityReport | None = None

    def startup_check(self) -> CapacityReport:
        """启动自检：S×M×c_eval ≤ 周期预算×η，超配 fail-fast。"""
        report = self.guard.check_or_raise(
            len(self.shard.symbols), len(self.registry), self.shard.snapshot_hz
        )
        self._capacity_report = report
        logger.info(report.summary_line())
        return report

    def run_cycle(self, ctxs: list[WatchContext] | None = None) -> list[AgentDecision]:
        """跑一轮：ctxs 注入则用注入（测试）；否则读 DB 最新快照自构造。"""
        t0 = time.perf_counter()
        if ctxs is None:
            ctxs = self.collect_contexts()
        decisions: list[AgentDecision] = []
        for ctx in ctxs:
            votes = self.fan_out_methods(ctx)
            decision = self.arbiter.arbitrate(votes, ctx)
            self.recorder.record(decision)
            decisions.append(decision)
            # T05 闭环指标：决策产出计数（按 action，不带 symbol 维度克制）
            metrics.inc_decision(decision.action)
        self.last_cycle_seconds = time.perf_counter() - t0
        self.cycles += 1
        metrics.observe_cycle(self.last_cycle_seconds)
        return decisions

    def fan_out_methods(self, ctx: WatchContext):
        return self.registry.evaluate_all(ctx)

    # ── 线上数据装配（v1：直查 v2_latest_snapshot 最新批，只读） ────────────
    def collect_contexts(self) -> list[WatchContext]:
        """每周期 1 次 ORM 读最新快照批 + 各标的滚动窗口（由 data_provider 注入）。"""
        if self.data_provider is None:
            logger.warning("WatchAgent 未挂 data_provider，跳过线上采集（影子模式）")
            return []
        out: list[WatchContext] = []
        for symbol in self.shard.symbols:
            ctx = self.data_provider(symbol)
            if ctx is not None:
                out.append(ctx)
        return out

    def attach_data_provider(self, provider) -> None:
        """注入 (symbol -> WatchContext | None) 供应商（线上=DB 快照+bar 窗口）。"""
        self.data_provider = provider

    data_provider = None
