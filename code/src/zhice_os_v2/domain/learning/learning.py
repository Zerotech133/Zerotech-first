"""学习案例。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from zhice_os_v2.domain.ontology.objects import utc_now


@dataclass
class LearningCase:
    learning_id: str
    strategy_version_id: str
    market: str
    replay_id: str
    insight: str
    ts_code: str = ""
    pnl_pct: Decimal | None = None
    lesson_summary: str | None = None
    created_at: datetime = field(default_factory=utc_now)
