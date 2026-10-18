"""告警模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class AlertLevel(StrEnum):
    INFO = "INFO"
    WARN = "WARN"
    CRITICAL = "CRITICAL"


class AlertStatus(StrEnum):
    OPEN = "OPEN"
    ACKED = "ACKED"
    RESOLVED = "RESOLVED"


@dataclass
class Alert:
    alert_id: str
    level: AlertLevel
    title: str
    message: str = ""
    status: AlertStatus = AlertStatus.OPEN
    created_at: datetime = field(default_factory=utc_now)
    acked_at: datetime | None = None
