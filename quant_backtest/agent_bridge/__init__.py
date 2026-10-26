"""Composition-only adapter between watch reports and quant-sector intake."""

from quant_backtest.agent_bridge.osleg_contract import (
    ContractViolation,
    OslegInbox,
    T_EXPIRE_SEC,
)
from quant_backtest.agent_bridge.osleg_http_contract import (
    HttpContractViolation,
    OslegHttpPeer,
    SOURCE_WHITELIST,
    build_submit_request,
    parse_submit_response,
    validate_auth,
)
from quant_backtest.agent_bridge.osleg_inbox_channel import (
    KIND_TO_GATEWAY,
    ChannelViolation,
    InboxChannelBridge,
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
    # W4 通道①同进程 inbox（K-QUANT-20261026-OSLEG-CH1）
    "InboxChannelBridge",
    "ChannelViolation",
    "KIND_TO_GATEWAY",
    # W4 通道②HTTP 8795 离线协议（K-QUANT-20261026-OSLEG-CH2）
    "HttpContractViolation",
    "OslegHttpPeer",
    "SOURCE_WHITELIST",
    "validate_auth",
    "build_submit_request",
    "parse_submit_response",
]
