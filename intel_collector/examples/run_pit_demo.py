# -*- coding: utf-8 -*-
"""PIT 时间旅行演示（不触网）：用合成跨时点数据演示「消费契约」的时间旅行能力。

同一标的 astock:SH600519，在 10-21 收盘与 10-22 收盘两个时点的跨源覆盖状态不同——
晚时点能看到更多源/更多观测，早时点只看到当时已采集的部分。这是对 obs_store.as_of
（单源单实体）的升级：面向下游的「全局时点快照」。

注：本演示用受控合成集（与 dashboard_build._demo_pit_states 同源），不读取真实 store，
以保证时点数据可控、可教学。PIT 对真实 store 的过滤能力由 tests/test_pit.py 覆盖。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.contracts import Observation, SourceClock
from core.pit import pit_entity_state

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 目标实体（A 股标的）
DOMAIN, KEY = "astock", "SH600519"

# 两个回溯时点：10-21 收盘后 vs 10-22 收盘后
AS_OF_EARLY = "2026-10-21T23:59:59Z"
AS_OF_LATE = "2026-10-22T23:59:59Z"


def _demo_obs() -> list[Observation]:
    """构造跨 10-21/10-22 的 SH600519 合成观测（与面板同源）。"""
    def mk(sid, payload, ts, status="complete"):
        return Observation(
            source_id=sid, snapshot_id=f"pit-{sid}-{ts}", entity_type="post",
            entity_id="SH600519", clock=SourceClock(collected_at=ts), status=status,
            completeness="exhausted", payload=payload, raw_sha256="x",
            source_payload_sha256="y", allowed_use_cases=("internal_retention",),
            source_field="feed",
        )
    return [
        mk("guba", {"code6": "SH600519"}, "2026-10-21T09:00:00Z"),
        mk("news", {"items": [{"title": "贵州茅台 600519 三季报超预期"}]}, "2026-10-21T10:00:00Z"),
        mk("xueqiu", {"target": "SH600519"}, "2026-10-22T09:00:00Z"),
        mk("guba", {"code6": "SH600519"}, "2026-10-22T12:00:00Z", status="empty"),
    ]


def main() -> None:
    obs = _demo_obs()
    e = pit_entity_state(obs, AS_OF_EARLY, DOMAIN, KEY)
    l = pit_entity_state(obs, AS_OF_LATE, DOMAIN, KEY)

    print(f"PIT 时间旅行 · 实体 {DOMAIN}:{KEY}（合成集共 {len(obs)} 条观测）\n")
    for label, st, t in (("时点A 10-21收盘", e, AS_OF_EARLY),
                         ("时点B 10-22收盘", l, AS_OF_LATE)):
        d = st.to_dict()
        print(f"── {label} (as_of={t}) ──")
        print(f"  存在: {st.exists}  观测数: {d['observation_count']}  覆盖源数: {d['source_count']}")
        print(f"  覆盖源: {d['sources']}")
        print(f"  状态分账: {d['statuses']}")
        print(f"  时间窗: {d['first_collected']} ~ {d['last_collected']}")
        print()

    gained = sorted(set(l.sources) - set(e.sources))
    print(">> 时间旅行差异：")
    print(f"  新增覆盖源(从A→B): {gained if gained else '无'}")
    print(f"  观测数增长: {e.observation_count} → {l.observation_count}")


if __name__ == "__main__":
    main()
