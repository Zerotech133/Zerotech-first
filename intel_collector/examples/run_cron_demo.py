# -*- coding: utf-8 -*-
"""统一 cron 实体演示（原型态，不触网、不污染真实注册表）

- 读真实 sources.json 元数据 → 构建【内存】注册表；
- 用 DemoDispatcher 注入采集（xueqiu 前两次合成失败以演示失败可见/退避）；
- 每个 tick 末跑 analyzer（compute_sla + scan_store_pii）→ FeedbackEngine 落反馈；
- 虚拟时钟每 tick 推进 35 分钟，跑 6 个 tick，展示 CronRunner 单一实体驱动采集闭环。

展示：到期派发 / 失败退避 / 分析反馈环动作 / 连续失败累计（strike 会话态）。

运行：python examples/run_cron_demo.py
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.source_registry import SourceRegistry, SourceSpec
from core.scheduler import Scheduler
from core.cron_runner import CronRunner
from core.feedback import FeedbackEngine
from core.quality_sla import compute_sla
from core.pii import scan_store_pii
from store.obs_store import ObservationStore

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "registry", "sources.json")
DB = os.path.join(ROOT, "store", "observations.db")


class DemoDispatcher:
    def __init__(self):
        self.n = 0

    def collect_source(self, source_id, now):
        self.n += 1
        # 模拟：xueqiu 前两次合成失败，之后恢复，演示失败可见与退避
        ok = not (source_id == "xueqiu" and self.n <= 2)
        return {"ok": ok, "n": 1, "reason": "" if ok else "synthetic fail (演示)"}


def main():
    with open(SOURCES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    reg = SourceRegistry()  # 内存态
    for sid, d in raw.items():
        reg.register(SourceSpec.from_dict(d))

    store = ObservationStore(DB if os.path.exists(DB) else ":memory:")
    clock = {"t": datetime(2026, 11, 1, 0, 0, tzinfo=timezone.utc)}
    sched = Scheduler(reg, now_provider=lambda: clock["t"])
    eng = FeedbackEngine(registry=reg, scheduler=sched)

    def analyzer(now):
        obs = store.all()
        return compute_sla(raw, obs, now, None), scan_store_pii(obs)

    runner = CronRunner(
        sched, DemoDispatcher(), analyzer=analyzer, feedback_engine=eng,
        now_provider=lambda: clock["t"],
    )

    N_TICKS = 6
    STEP = timedelta(minutes=35)  # > 最严 freshness(30m)，保证到期
    print(f"统一 cron 实体演示 · {N_TICKS} ticks × {STEP}")
    print("-" * 64)
    for i in range(N_TICKS):
        rep = runner.tick(clock["t"])
        fb = ", ".join(a.action.value for a in rep.feedback_actions) or "—"
        print(f"tick{i + 1} {clock['t'].strftime('%H:%M')} "
              f"派发={rep.dispatched} 反馈={fb}")
        clock["t"] += STEP
    print("-" * 64)
    print("说明：CronRunner 收敛调度；反馈环每次 tick 末基于 SLA/PII 回流采集层。"
          "本演示为内存态，不写 sources.json、不触网。")


if __name__ == "__main__":
    main()
