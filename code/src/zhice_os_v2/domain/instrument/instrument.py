"""统一证券/交易标的模型。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    instrument_id: str
    market: str
    symbol: str
    exchange: str
    currency: str
    name: str = ""
    lot_size: int | None = None
    status: str = "ACTIVE"
