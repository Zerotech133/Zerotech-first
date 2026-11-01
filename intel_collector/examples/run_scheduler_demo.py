# -*- coding: utf-8 -*-
"""调度编排演示（原型态，不触网、不污染真实注册表）

- 从真实 sources.json 读取 5 源元数据，构建【内存】注册表（path=None，不落盘）；
- 用合成适配器（除 xueqiu 故意失败外其余成功）驱动 Scheduler；
- 虚拟时钟每 tick 推进 65 分钟，跑 5 个 tick，演示：到期触发 / 指数退避 / 熔断 open；
- 输出 scheduler_status.json 供 dashboard_build.py 渲染"调度与熔断"章节。

运行：python examples/run_scheduler_demo.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.source_registry import SourceRegistry, SourceSpec
from core.orchestrator import Orchestrator
from core.scheduler import Scheduler
from core.base_adapter import BaseAdapter
from core.contracts import UseCase, Status
from store.obs_store import ObservationStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "registry", "sources.json")
OUT_PATH = os.path.join(ROOT, "scheduler_status.json")


class SyntheticAdapter(BaseAdapter):
    def __init__(self, source_id, fail=False, n=2):
        super().__init__(source_id)
        self.fail = fail
        self.n = n

    def collect(self, targets, **kwargs):
        if self.fail:
            raise RuntimeError("合成失败：演示熔断")
        out = []
        for i in range(self.n):
            out.append(self.make_observation(
                entity_type="synthetic", entity_id=f"{self.source_id}:{i}",
                payload={"demo": i, "source": self.source_id},
                status=Status.COMPLETE,
                allowed_use_cases=(UseCase.INTERNAL_RETENTION.value,),
            ))
        return out


def main():
    # 真实元数据 → 内存注册表（不持久化，避免污染 sources.json）
    with open(SOURCES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    reg = SourceRegistry()  # path=None → 不落盘
    for sid, d in raw.items():
        reg.register(SourceSpec.from_dict(d))

    orch = Orchestrator(reg, ObservationStore())
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])

    # xueqiu 故意失败以演示熔断；其余成功
    jobs = {}
    for sid in raw:
        jobs[sid] = {
            "adapter": SyntheticAdapter(sid, fail=(sid == "xueqiu")),
            "targets": ["DEMO"],
            "bind": (UseCase.INTERNAL_RETENTION.value,),
        }

    N_TICKS = 5
    STEP = timedelta(minutes=65)  # > 最严格 freshness(30m)*2，保证到期
    for i in range(N_TICKS):
        sched.run_due(orch, jobs)
        clock["t"] += STEP

    # 汇总状态
    sources_status = []
    for spec in reg.list():
        sources_status.append({
            "source_id": spec.source_id,
            "name": spec.name,
            "enabled": spec.enabled,
            "consecutive_failures": spec.health.get("consecutive_failures", 0),
            "circuit": sched.circuit_state(spec.source_id),
            "freshness_minutes": spec.sla.get("freshness_minutes"),
            "max_failures": spec.sla.get("max_failures"),
            "last_outcome": (sched.ledger[-1]["outcome"]
                             if sched.ledger and sched.ledger[-1]["source_id"] == spec.source_id
                             else ""),
        })

    status = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": "原型态合成调度演示，不触网、不污染真实注册表",
        "tick_count": N_TICKS,
        "sources": sources_status,
        "ledger_count": len(sched.ledger),
        "ledger_tail": sched.ledger[-8:],
    }
    with open(OUT_PATH, "w", encoding="utf-8") as f:
        json.dump(status, f, ensure_ascii=False, indent=2)
    print(f"已生成调度状态：{OUT_PATH}")
    print(f"  tick={N_TICKS} ledger={len(sched.ledger)}")
    for s in sources_status:
        print(f"  {s['source_id']:18s} enabled={s['enabled']!s:5s} "
              f"fails={s['consecutive_failures']} circuit={s['circuit']}")


if __name__ == "__main__":
    main()
