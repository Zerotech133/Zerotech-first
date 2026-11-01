# -*- coding: utf-8 -*-
"""采集即服务平台 · 统一 cron 实体 (core/cron_runner.py)

把分散在 Orchestrator.as_scheduler / examples/run_scheduler_demo 的调度逻辑收敛为一个
可部署实体：CronRunner。它用 Scheduler（虚拟时钟）驱动，因此同一份代码既能单元测试
（手动 tick + 注入 now），也能生产部署（run_forever 真实轮询）。

职责（每个 tick）：
  - 取出到期源 → 通过 CollectDispatcher 协议派发采集 → 推进下次到期；
  - （可选）跑分析（SLA/PII）并应用反馈环，完成"分析→采集"闭环；
  - dispatch / analyzer / feedback_engine 全部依赖注入，默认 None（纯调度，零副作用）。

安全：本实体不自行触网；触网只在注入的 dispatcher 内发生，且需在 RightsGate 授权前提下。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from core.scheduler import Scheduler
from core.feedback import FeedbackEngine, FeedbackAction
from core.quality_sla import SlaReport


class CollectDispatcher(Protocol):
    def collect_source(self, source_id: str, now: datetime) -> dict:
        """执行一次该源的采集，返回 {ok: bool, n: int, reason: str}。"""
        ...


@dataclass
class TickReport:
    tick_at: str
    dispatched: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    feedback_actions: list[FeedbackAction] = field(default_factory=list)
    outcomes: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tick_at": self.tick_at,
            "dispatched": self.dispatched,
            "skipped": self.skipped,
            "feedback_action_count": len(self.feedback_actions),
            "feedback_actions": [a.to_dict() for a in self.feedback_actions],
            "outcomes": self.outcomes,
        }


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


class CronRunner:
    def __init__(self, scheduler: Scheduler, dispatcher: CollectDispatcher,
                 analyzer: Optional[Callable[[datetime], tuple[dict[str, SlaReport], dict[str, int]]]] = None,
                 feedback_engine: Optional[FeedbackEngine] = None,
                 now_provider: Callable[[], datetime] = _default_now):
        self.scheduler = scheduler
        self.dispatcher = dispatcher
        self.analyzer = analyzer                  # now -> (sla_reports, pii_counts)
        self.feedback_engine = feedback_engine
        self.now_provider = now_provider

    def tick(self, now: Optional[datetime] = None) -> TickReport:
        now = now or self.now_provider()
        report = TickReport(tick_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"))

        # 1) 派发到期源
        for spec in self.scheduler.due_sources():
            try:
                res = self.dispatcher.collect_source(spec.source_id, now)
            except Exception as e:  # 失败可见，不静默吞
                res = {"ok": False, "n": 0, "reason": f"dispatch error: {e}"}
            ok = bool(res.get("ok"))
            self.scheduler.mark_collected(spec.source_id, ok)
            report.dispatched.append(spec.source_id)
            report.outcomes[spec.source_id] = res

        # 2) 分析 → 反馈环（可选；注入 analyzer + feedback_engine 才生效）
        if self.analyzer is not None and self.feedback_engine is not None:
            sla_reports, pii_counts = self.analyzer(now)
            report.feedback_actions = self.feedback_engine.run(sla_reports, pii_counts)

        return report

    def run_forever(self, poll_interval: float = 60.0, max_ticks: Optional[int] = None,
                   sleep_fn: Callable[[float], None] = time.sleep) -> list[TickReport]:
        """生产部署实体：每个 poll_interval 秒 tick 一次。

        max_ticks：用于测试/受限运行；sleep_fn 可注入（测试用 no-op 避免阻塞）。
        注意：虚拟时钟下 now_provider 不变则到期源不会重复派发；生产用真实时钟。
        """
        history: list[TickReport] = []
        ticks = 0
        while True:
            history.append(self.tick())
            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                break
            sleep_fn(poll_interval)
        return history
