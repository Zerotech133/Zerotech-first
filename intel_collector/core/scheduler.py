# -*- coding: utf-8 -*-
"""采集即服务平台 · 多源调度编排 (core/scheduler.py)

把 Orchestrator 的"一次采集"升级为"按源节奏的可持续调度"：
- 虚拟时钟驱动（now_provider 可注入），不依赖真实 cron、不真实触网，可单测；
- 每源按 sla.freshness_minutes 判定是否到期（due）；
- 连续失败按注册表 health.consecutive_failures 做指数退避（min(freshness*2^fails, 600s)）；
- 熔断态 circuit_state 绑定注册表 enabled + consecutive_failures + max_failures：
  closed(健康) / half_open(有过失败但未到阈值) / open(已禁用或达阈值)；
- run_due 落账 ledger（原型态，不落盘），供面板/审计读取。

注意：本模块只编排"何时采、是否熔断"，不碰 RightsGate/源登记/真实网络；
RightsGate 在 Orchestrator.run_once 内已校验。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

from registry.source_registry import SourceRegistry  # noqa: F401  (类型/持久化同进程)


def _default_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Scheduler:
    registry: SourceRegistry
    now_provider: Callable[[], datetime] = _default_now
    # 每源下次到期时间（内存态；虚拟时钟驱动）
    _next_due: dict[str, datetime] = field(default_factory=dict)
    # 运行留痕（原型态，不落盘）
    ledger: list[dict] = field(default_factory=list)

    # ---- 配置解析 ----
    def _freshness(self, spec) -> timedelta:
        return timedelta(minutes=spec.sla.get("freshness_minutes", 60))

    def _max_failures(self, spec) -> int:
        return spec.sla.get("max_failures", 5)

    def _init_due(self, spec) -> None:
        if spec.source_id not in self._next_due:
            # 首轮立即到期，便于上线即采
            self._next_due[spec.source_id] = self._now()

    # ---- 到期判定 ----
    def due_sources(self) -> list:
        out = []
        for spec in self.registry.list():
            if not spec.enabled:
                continue
            self._init_due(spec)
            if self._now() >= self._next_due[spec.source_id]:
                out.append(spec)
        return out

    # ---- 反馈环/统一 cron 副作用入口 ----
    def force_due(self, source_id: str) -> None:
        """反馈环用：把某源下次到期强制设为现在，使其下一 tick 立即重新采集（加速补采/补救）。"""
        spec = self.registry.get(source_id)
        if spec is None:
            return
        self._init_due(spec)
        self._next_due[source_id] = self._now()

    def mark_collected(self, source_id: str, ok: bool) -> None:
        """反馈环/统一 cron 用：采集完成后推进该源下次到期（成功=freshness，失败=退避）。"""
        spec = self.registry.get(source_id)
        if spec is None:
            return
        self._init_due(spec)
        if ok:
            self._next_due[source_id] = self._now() + self._freshness(spec)
        else:
            self._next_due[source_id] = self._now() + self._backoff(spec)

    # ---- 退避 ----
    def _backoff(self, spec) -> timedelta:
        fails = spec.health.get("consecutive_failures", 0)
        base_secs = self._freshness(spec).total_seconds()
        # 指数退避：基线 * 2^失败数；封顶取 max(600s, 基线*8)，保证能指数增长不被压死
        cap = max(600, base_secs * 8)
        secs = min(base_secs * (2 ** fails), cap)
        return timedelta(seconds=secs)

    # ---- 熔断态 ----
    def circuit_state(self, source_id: str) -> str:
        spec = self.registry.get(source_id)
        if spec is None:
            return "unknown"
        if not spec.enabled:
            return "open"  # 已禁用 = 熔断开启
        fails = spec.health.get("consecutive_failures", 0)
        threshold = self._max_failures(spec)
        if fails >= threshold:
            return "open"
        if fails > 0:
            return "half_open"
        return "closed"

    # ---- 单轮调度 ----
    def run_due(self, orchestrator, jobs: dict, max_per_tick: int = 200) -> dict:
        """jobs: source_id -> {"adapter": BaseAdapter, "targets": list, "bind": tuple}

        对到期源执行 Orchestrator.run_once，按成败更新下次到期（成功=freshness，
        失败=退避），并记录 ledger。返回每源本轮结果。"""
        results: dict[str, dict] = {}
        for spec in self.due_sources():
            if len(results) >= max_per_tick:
                break
            job = jobs.get(spec.source_id)
            if not job:
                # 注册表有源但编排未挂适配器：跳过并顺延一个 freshness
                self._next_due[spec.source_id] = self._now() + self._freshness(spec)
                self.ledger.append({
                    "source_id": spec.source_id, "at": self._now().isoformat(),
                    "outcome": "skipped_no_adapter",
                })
                results[spec.source_id] = {"outcome": "skipped_no_adapter",
                                          "circuit": self.circuit_state(spec.source_id)}
                continue
            res = orchestrator.run_once(
                job["adapter"], job["targets"],
                bind_use_cases=job.get("bind", ()),
            )
            if res.get("ok"):
                self._next_due[spec.source_id] = self._now() + self._freshness(spec)
            else:
                self._next_due[spec.source_id] = self._now() + self._backoff(spec)
            rec = {
                "outcome": "ok" if res.get("ok") else "failed",
                "n": res.get("n", 0),
                "reason": res.get("reason", ""),
                "consecutive_failures": spec.health.get("consecutive_failures", 0),
                "circuit": self.circuit_state(spec.source_id),
                "next_due": self._next_due[spec.source_id].isoformat(),
            }
            self.ledger.append({"source_id": spec.source_id,
                                "at": self._now().isoformat(), **rec})
            results[spec.source_id] = rec
        return results

    # 兼容旧编排器调用习惯
    def _now(self) -> datetime:
        return self.now_provider()
