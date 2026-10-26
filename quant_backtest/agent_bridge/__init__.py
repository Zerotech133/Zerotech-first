"""Composition-only adapter between watch reports and quant-sector intake."""

from quant_backtest.agent_bridge.osleg_contract import (
    ContractViolation,
    OslegInbox,
    T_EXPIRE_SEC,
)
from quant_backtest.agent_bridge.mapping import (
    ACK_FIELD_MAP,
    REPORT_FIELD_MAP,
    OutboxBindingAdapter,
    to_agent_ack,
    to_sector_report,
)

__all__ = [
    "ContractViolation",
    "OslegInbox",
    "T_EXPIRE_SEC",
    "ACK_FIELD_MAP",
    "REPORT_FIELD_MAP",
    "OutboxBindingAdapter",
    "to_agent_ack",
    "to_sector_report",
]
