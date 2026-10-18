"""账户快照。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from zhice_os_v2.domain.ontology.objects import utc_now


@dataclass
class AccountSnapshot:
    account_id: str
    market: str
    initial_capital: Decimal
    cash: Decimal
    market_value: Decimal = Decimal("0")
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def total_equity(self) -> Decimal:
        return self.cash + self.market_value
