# -*- coding: utf-8 -*-
"""采集即服务平台 · PIT 时间旅行查询（core/pit.py）

消费契约承诺的「时点旅行」能力：给定时点 as_of，只看到该时点及之前已采集的
Observation，跨源归并出的实体状态也随之回溯。这是对 obs_store.as_of（单源单实体）
的升级——面向下游消费方的「全局时点快照」。

设计：纯函数，输入 Observation 列表，不依赖存储内部；调用方负责从 store 取全量观测。
时间比较用 ISO8601 解析（兼容尾缀 Z），不靠字符串字典序赌运气。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable, Optional

from core.contracts import Observation
from core.canonical import rollup_by_entity


def _as_dt(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def filter_as_of(observations: Iterable[Observation], as_of: str) -> list[Observation]:
    """返回 collected_at <= as_of 的观测子集。as_of 为 ISO8601 字符串。

    - as_of 无法解析时退化为「返回全部」（不静默丢数据）。
    - 观测无 collected_at 时不纳入（无法判定时点，保守排除）。
    """
    cutoff = _as_dt(as_of)
    if cutoff is None:
        return list(observations)
    out: list[Observation] = []
    for o in observations:
        t = _as_dt(o.clock.collected_at)
        if t is not None and t <= cutoff:
            out.append(o)
    return out


def pit_rollup(observations: Iterable[Observation], as_of: str):
    """按时点 as_of 做跨源实体归并（时点旅行）。返回 {(domain,key): EntityRollup}。"""
    return rollup_by_entity(filter_as_of(observations, as_of))


@dataclass
class PitEntityState:
    """某实体在 as_of 时点的快照态：是否存在、覆盖哪些源、状态分账、时间窗。"""
    domain: str
    key: str
    as_of: str
    exists: bool = False
    observation_count: int = 0
    source_count: int = 0
    sources: list[str] = field(default_factory=list)
    statuses: dict[str, int] = field(default_factory=dict)
    first_collected: Optional[str] = None
    last_collected: Optional[str] = None
    sample_texts: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "key": self.key,
            "as_of": self.as_of,
            "exists": self.exists,
            "observation_count": self.observation_count,
            "source_count": self.source_count,
            "sources": self.sources,
            "statuses": self.statuses,
            "first_collected": self.first_collected,
            "last_collected": self.last_collected,
            "sample_texts": self.sample_texts,
        }


def pit_entity_state(observations: Iterable[Observation], as_of: str,
                     domain: str, key: str) -> PitEntityState:
    """某实体（domain,key）在 as_of 时点的跨源聚合态。不存在则 exists=False。"""
    r = pit_rollup(observations, as_of).get((domain, key))
    if r is None:
        return PitEntityState(domain=domain, key=key, as_of=as_of, exists=False)
    d = r.to_dict()
    return PitEntityState(
        domain=domain, key=key, as_of=as_of,
        exists=True,
        observation_count=d["observation_count"],
        source_count=d["source_count"],
        sources=d["sources"],
        statuses=d["statuses"],
        first_collected=d["first_collected"],
        last_collected=d["last_collected"],
        sample_texts=d["sample_texts"],
    )
