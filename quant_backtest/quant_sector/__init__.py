"""Quant-sector upstream intake and three-route dispatch (W-Q1, offline).

Independent module for the approved W-Q1 scope: the quant trading sector's
own receiving inbox for upward watch reports (ANOMALY / HEALTH), a
three-route offline dispatch ledger (FEISHU_INFO / RESEARCH_REVIEW /
EXCHANGE_OBSERVATION) and read-only projections.

The semantics deliberately mirror the watch-side outbox (lease-bounded
claims, terminal UNKNOWN, epoch-only-forward revocation) but the code is
fully independent: this package NEVER imports watch_dispatch.

Offline only: no network, no persistence, no execution semantics. Nothing
in this package carries any trading permission; EXCHANGE_OBSERVATION only
routes observation candidates to the trader for review.
"""
from quant_backtest.quant_sector.dispatch_ledger import (
    ACCEPTED_VERIFIED,
    CANCELLED,
    CLAIMED,
    DEFINITELY_REJECTED,
    DispatchAttempt,
    DispatchIntent,
    DispatchLedger,
    DispatchLedgerError,
    READY,
    UNKNOWN,
    _ROUTES as DISPATCH_ROUTES,
)
from quant_backtest.quant_sector.upstream_inbox import (
    QuantUpstreamReport,
    UpstreamAck,
    UpstreamInbox,
    UpstreamInboxError,
    UpstreamReceipt,
    digest,
    utc_text,
)

__all__ = [
    "ACCEPTED_VERIFIED",
    "CANCELLED",
    "CLAIMED",
    "DEFINITELY_REJECTED",
    "DISPATCH_ROUTES",
    "DispatchAttempt",
    "DispatchIntent",
    "DispatchLedger",
    "DispatchLedgerError",
    "QuantUpstreamReport",
    "READY",
    "UNKNOWN",
    "UpstreamAck",
    "UpstreamInbox",
    "UpstreamInboxError",
    "UpstreamReceipt",
    "digest",
    "utc_text",
]
