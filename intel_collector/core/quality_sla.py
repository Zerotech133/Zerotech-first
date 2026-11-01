# -*- coding: utf-8 -*-
"""采集即服务平台 · 质量 SLA

解决原型审计点名的缺口之一：源质量 SLA。
把"采集是否新鲜、是否完整、是否仍在健康采集"量化成每个源可读的状态，
让面板从"有没有数据"升级到"数据可不可信、及不及时"。

设计要点：
  - 新鲜度来自 sources.json 的 sla.freshness_minutes（真实阈值，非硬编码）。
  - state 分四档：no_data（无观测）/ ok（≤freshness）/ stale（freshness~2×freshness）
    / breach（>2×freshness，触发告警）。
  - empty_ratio = 空观测数 / 总观测数（采集完整性指标）。
  - 熔断态（circuit）由调度器注入（closed/half_open/open），与 scheduler 联动。
  - now 注入，可单测、不依赖真实时间、不触网。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from .contracts import Observation


def _parse(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    s = str(ts).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# 严重度档位
STATE_NO_DATA = "no_data"
STATE_OK = "ok"
STATE_STALE = "stale"
STATE_BREACH = "breach"

_STATE_LABEL = {
    STATE_NO_DATA: "无数据",
    STATE_OK: "达标",
    STATE_STALE: "滞后",
    STATE_BREACH: "违约",
}


@dataclass
class SlaReport:
    source_id: str
    freshness_minutes: int
    max_failures: int
    observation_count: int
    empty_count: int
    empty_ratio: float
    last_collected_at: Optional[str]
    age_minutes: Optional[float]
    state: str
    circuit: str
    consecutive_failures: int
    note: str

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "freshness_minutes": self.freshness_minutes,
            "max_failures": self.max_failures,
            "observation_count": self.observation_count,
            "empty_count": self.empty_count,
            "empty_ratio": round(self.empty_ratio, 3),
            "last_collected_at": self.last_collected_at,
            "age_minutes": None if self.age_minutes is None else round(self.age_minutes, 1),
            "state": self.state,
            "state_label": _STATE_LABEL.get(self.state, self.state),
            "circuit": self.circuit,
            "consecutive_failures": self.consecutive_failures,
            "note": self.note,
        }


def compute_sla_for_source(
    source_id: str,
    spec: dict,
    observations: list[Observation],
    now: datetime,
    circuit: str = "unknown",
    consecutive_failures: int = 0,
) -> SlaReport:
    sla = spec.get("sla") or {}
    freshness = int(sla.get("freshness_minutes", 0) or 0)
    max_failures = int(sla.get("max_failures", 0) or 0)

    mine = [o for o in observations if o.source_id == source_id]
    count = len(mine)
    if count == 0:
        return SlaReport(
            source_id=source_id, freshness_minutes=freshness, max_failures=max_failures,
            observation_count=0, empty_count=0, empty_ratio=0.0,
            last_collected_at=None, age_minutes=None, state=STATE_NO_DATA,
            circuit=circuit, consecutive_failures=consecutive_failures,
            note="暂无观测：未采集或可采集源未登记到 store",
        )

    last = max((_parse(o.clock.collected_at) for o in mine), key=lambda d: d or datetime.min.replace(tzinfo=timezone.utc))
    last_ts = max((o.clock.collected_at for o in mine if o.clock.collected_at), default=None)
    empty = sum(1 for o in mine if (o.status or "").lower() == "empty")
    empty_ratio = empty / count if count else 0.0

    if last is None:
        age = None
        state = STATE_NO_DATA
    else:
        age = (now - last).total_seconds() / 60.0
        if freshness <= 0:
            state = STATE_OK  # 未设阈值视为不考核新鲜度
        elif age <= freshness:
            state = STATE_OK
        elif age <= freshness * 2:
            state = STATE_STALE
        else:
            state = STATE_BREACH

    if circuit == "open":
        note = "熔断已开：连续失败达阈值或源被禁用"
    elif empty_ratio >= 0.5:
        note = "空观测占比偏高，采集内容可能枯竭"
    elif state == STATE_BREACH:
        note = "超 2×freshness，需排查采集链路"
    elif state == STATE_STALE:
        note = "超 freshness，接近违约"
    else:
        note = "新鲜度达标"

    return SlaReport(
        source_id=source_id, freshness_minutes=freshness, max_failures=max_failures,
        observation_count=count, empty_count=empty, empty_ratio=empty_ratio,
        last_collected_at=last_ts, age_minutes=age, state=state,
        circuit=circuit, consecutive_failures=consecutive_failures, note=note,
    )


def compute_sla(
    sources: dict,
    observations: list[Observation],
    now: datetime,
    circuit_map: Optional[dict[str, dict]] = None,
) -> dict[str, SlaReport]:
    """对 sources.json 中每个源计算 SLA 报告。circuit_map 形如 {source_id: {circuit, consecutive_failures}}。"""
    circuit_map = circuit_map or {}
    out: dict[str, SlaReport] = {}
    for sid, spec in sources.items():
        ci = circuit_map.get(sid, {})
        out[sid] = compute_sla_for_source(
            sid, spec, observations, now,
            circuit=ci.get("circuit", "unknown"),
            consecutive_failures=int(ci.get("consecutive_failures", 0) or 0),
        )
    return out
