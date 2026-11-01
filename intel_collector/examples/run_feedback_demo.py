# -*- coding: utf-8 -*-
"""分析→采集反馈环演示（原型态，不触网、不污染真实注册表）

- 读真实 sources.json 元数据 → 构建【内存】注册表（path=None，save 无效）；
- 读 store/observations.db（无则空态）→ 跑真实 compute_sla + scan_store_pii；
- 用 FeedbackEngine.run 演示"分析结论回流采集层"的闭环动作；
- 打印每个源本帧会被触发的反馈动作，并展示落点（加速/开闸/降级/复核）。

注意：演示直接作用于内存注册表，不写 sources.json；真实副作用只在生产 CronRunner tick 内发生。

运行：python examples/run_feedback_demo.py
"""
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from registry.source_registry import SourceRegistry, SourceSpec
from store.obs_store import ObservationStore
from core.quality_sla import compute_sla
from core.pii import scan_store_pii
from core.scheduler import Scheduler
from core.feedback import FeedbackEngine, FeedbackActionType

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCES_PATH = os.path.join(ROOT, "registry", "sources.json")
DB = os.path.join(ROOT, "store", "observations.db")


def main():
    with open(SOURCES_PATH, encoding="utf-8") as f:
        raw = json.load(f)
    reg = SourceRegistry()  # 内存态，不落盘
    for sid, d in raw.items():
        reg.register(SourceSpec.from_dict(d))

    store = ObservationStore(DB if os.path.exists(DB) else ":memory:")
    obs = store.all()
    now = datetime.now(timezone.utc)

    sla_reports = compute_sla(raw, obs, now, None)
    pii_counts = scan_store_pii(obs)

    sched = Scheduler(reg, now_provider=lambda: now)
    eng = FeedbackEngine(registry=reg, scheduler=sched)
    actions = eng.run(sla_reports, pii_counts)

    print(f"反馈环演示 · now={now.strftime('%Y-%m-%dT%H:%M:%SZ')} · 观测={len(obs)}")
    print("-" * 60)
    if not actions:
        print("本帧未产出反馈动作：各源新鲜度达标且无 PII 命中。")
    for a in actions:
        print(f"  {a.source_id:18s} [{a.action.value:16s}] {a.reason}")
    print("-" * 60)
    # 展示落点后的注册表状态（仅内存）
    for spec in reg.list():
        flags = []
        if not spec.enabled:
            flags.append("熔断禁用")
        if spec.health.get("review_flag"):
            flags.append("待复核")
        revoked = [k for k, v in spec.rights.items() if v is False]  # 示意
        if "training" in revoked:
            flags.append("已撤training")
        print(f"  {spec.source_id:18s} enabled={spec.enabled} flags={flags or '—'}")
    print("（以上为内存态演示，未写入 sources.json）")


if __name__ == "__main__":
    main()
