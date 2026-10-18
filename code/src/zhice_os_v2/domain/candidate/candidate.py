"""候选池与候选。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from zhice_os_v2.domain.ontology.objects import utc_now


@dataclass
class Candidate:
    candidate_id: str
    strategy_version_id: str
    market: str
    ts_code: str
    score: float
    reason: str
    factor_values: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    status: str = "ACTIVE"


@dataclass
class CandidatePool:
    pool_id: str
    strategy_version_id: str
    market: str
    trade_date: date
    candidates: list[Candidate] = field(default_factory=list)
    created_at: datetime = field(default_factory=utc_now)
