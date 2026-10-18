"""账户资金预留领域对象（v2_account_reservation）。

两阶段资金语义的台账条目：reserve 冻结（RESERVED）、consume 实扣（CONSUMED）、
release 解冻（RELEASED）。归属 (account_id, user_strategy_id)；idempotency_key 唯一，
保证同一业务动作重复调用不重复预留/扣减。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class ReservationStatus(StrEnum):
    RESERVED = "RESERVED"
    CONSUMED = "CONSUMED"
    RELEASED = "RELEASED"


@dataclass
class Reservation:
    reservation_id: str
    account_id: str
    user_strategy_id: str
    idempotency_key: str
    amount: Decimal
    status: ReservationStatus = ReservationStatus.RESERVED
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
