"""Quant-sector upstream intake, three-route dispatch and dual gate (W-Q1+W-Q2).

Independent module for the approved W-Q1 scope: the quant trading sector's
own receiving inbox for upward watch reports (ANOMALY / HEALTH), a
three-route offline dispatch ledger (FEISHU_INFO / RESEARCH_REVIEW /
EXCHANGE_OBSERVATION) and read-only projections.

W-Q2 extends this package with the offline dual-gate permission state
machine (PermissionGate), migrated verbatim from watch_dispatch as the
quant sector now owns authorization. It still imports only the frozen
watch_dispatch evidence contracts (canonical/contracts); no watch-side
authorization entry point remains there.

The inbox/ledger semantics deliberately mirror the watch-side outbox
(lease-bounded claims, terminal UNKNOWN, epoch-only-forward revocation)
but the W-Q1 code is fully independent.

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
from quant_backtest.quant_sector.dual_gate import (
    AuthorizationAuthority,
    AuthorizationScope,
    DispatchVerdict,
    GateError,
    HumanAuthorization,
    PermissionGate,
    ResearchReview,
    RiskDecision,
    RiskGuardPort,
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
    "AuthorizationAuthority",
    "AuthorizationScope",
    "DispatchVerdict",
    "GateError",
    "HumanAuthorization",
    "PermissionGate",
    "QuantUpstreamReport",
    "READY",
    "ResearchReview",
    "RiskDecision",
    "RiskGuardPort",
    "UNKNOWN",
    "UpstreamAck",
    "UpstreamInbox",
    "UpstreamInboxError",
    "UpstreamReceipt",
    "digest",
    "utc_text",
]
