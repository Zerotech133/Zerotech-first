# -*- coding: utf-8 -*-
"""采集即服务平台 · 最小编排器 (core/orchestrator.py)

统一调度 + 健康 + 封禁/掉线检测 + 采集时权利绑定。
注意：本原型不含真实 cron；调度骨架仅演示退避与连续失败封禁逻辑。
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from .base_adapter import BaseAdapter
from .contracts import UseCase
from .rights_gate import RightsGate
from registry.source_registry import SourceRegistry
from store.obs_store import ObservationStore


class Orchestrator:
    def __init__(self, registry: SourceRegistry, store: ObservationStore,
                 gate: Optional[RightsGate] = None):
        self.registry = registry
        self.store = store
        self.gate = gate or RightsGate()

    def run_once(self, adapter: BaseAdapter, targets: Iterable[str], *,
                 bind_use_cases: tuple[str, ...] = (), **adapter_kwargs) -> dict:
        """采集一次：校验源启用、绑定允许用途、落盘、更新健康。
        adapter_kwargs 透传给 adapter.collect（如 mode/max_pages/symbols）。"""
        spec = self.registry.get(adapter.source_id)
        if spec is None:
            return {"ok": False, "reason": f"源 {adapter.source_id} 未在注册表登记"}
        if not spec.enabled:
            return {"ok": False, "reason": f"源 {adapter.source_id} 已禁用"}
        # 采集时权利绑定：只有源授权且闸门通过的用途才写入观察
        bound = []
        for uc in bind_use_cases:
            dec = self.gate.check(spec, uc)
            if dec.allowed:
                bound.append(uc)
        try:
            raw = adapter.collect(targets, **adapter_kwargs)
        except Exception as e:  # 失败可见，不静默吞
            self.registry.update_health(adapter.source_id, ok=False, detail=str(e)[:200])
            return {"ok": False, "reason": f"采集异常: {e}", "n": 0}
        out = []
        for o in raw:
            o.allowed_use_cases = tuple(bound)   # 把被绑定的用途快照到每条观察
            self.store.put(o)
            out.append(o)
        self.registry.update_health(adapter.source_id, ok=True)
        return {"ok": True, "n": len(out), "bound_use_cases": bound, "observations": out}

    def as_scheduler(self, jobs: list[tuple[BaseAdapter, list[str], int]],
                     max_runs: int = 3) -> None:
        """极简调度骨架（原型）：轮询 + 指数退避 + 连续失败封禁。非生产 cron。"""
        for _ in range(max_runs):
            for adapter, targets, interval in jobs:
                spec = self.registry.get(adapter.source_id)
                if spec and not spec.enabled:
                    continue
                self.run_once(adapter, targets)
                fails = spec.health.get("consecutive_failures", 0) if spec else 0
                backoff = min(interval * (2 ** fails), 600)
                time.sleep(backoff)
