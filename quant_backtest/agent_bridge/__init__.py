"""Composition-only adapter between watch reports and quant-sector intake."""

from quant_backtest.agent_bridge.mapping import (
    ACK_FIELD_MAP,
    REPORT_FIELD_MAP,
    OutboxBindingAdapter,
    to_agent_ack,
    to_sector_report,
)

__all__ = [
    "ACK_FIELD_MAP",
    "REPORT_FIELD_MAP",
    "OutboxBindingAdapter",
    "to_agent_ack",
    "to_sector_report",
]
