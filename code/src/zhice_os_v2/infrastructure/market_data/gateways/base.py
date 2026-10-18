"""DataGateway 协议（各市场网关结构对齐，复刻 V1 data_provider/base.py 精简版）。"""
from __future__ import annotations

from typing import Protocol

from .dto import DailyBarDTO, LatestSnapshotDTO, MinuteBarDTO


class DataGateway(Protocol):
    def get_daily_bars_batch(
        self, ts_codes: list[str], limit: int = 60, start: str | None = None, end: str | None = None
    ) -> dict[str, list[DailyBarDTO]]: ...

    def get_minute_bars(self, ts_code: str, trade_date: str) -> list[MinuteBarDTO]: ...

    def get_latest_snapshot_batch(self, ts_codes: list[str]) -> dict[str, LatestSnapshotDTO]: ...

    def close(self) -> None: ...
