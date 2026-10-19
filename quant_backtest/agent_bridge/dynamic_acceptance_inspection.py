"""Read-only, offline exact-attempt diagnosis; never an acceptance authority.

A caller-provided clock (including a FakeClock) is merely a local simulation,
not a trusted time root. A consistent lookup at its supplied time proves neither
current validity/authorization nor the historical finish time: RouteReceipt has
no finished_at, and an observed ACK can legitimately predate a retry claim.
Outbox and inbox reads are not an atomic revocation snapshot. No result from
this module may promote a controlled-pool signal or authorize production work.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Literal

from quant_backtest.agent_bridge.trusted_acceptance import TrustedAcceptanceBindingAdapter
from quant_backtest.quant_sector.upstream_inbox import UpstreamInbox
from quant_backtest.watch_dispatch.agent_reporting import AgentReport
from quant_backtest.watch_dispatch.canonical import utc_text
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.route_outbox import RouteOutbox
from quant_backtest.watch_dispatch.signal_pool import AttemptBinding

InspectionStatus = Literal["CONSISTENT_OFFLINE", "UNAVAILABLE"]


@dataclass(frozen=True)
class AttemptInspection:
    """A time-stamped diagnostic only, not a receipt, ACK, or authorization."""

    attempt_id: str
    checked_at: datetime | None
    status: InspectionStatus

    @property
    def production_permitted(self) -> bool:
        """Never grant production authority."""
        return False

    @property
    def notification_permitted(self) -> bool:
        """Never grant notification authority."""
        return False

    @property
    def trading_permitted(self) -> bool:
        """Never grant trading authority."""
        return False


class DynamicAttemptInspector:
    """Re-read live offline ledgers with one newly sampled local clock per query.

    This is intentionally not ReceiptBindingPort and exposes no binding_for.
    Time and field consistency do not prove validity, historical finish timing,
    atomic revocation, real ACK origin, or permission to mark_accepted.
    """

    def __init__(self, outbox: RouteOutbox, envelope: EvidenceEnvelope,
                 report: AgentReport, inbox: UpstreamInbox, *,
                 clock: Callable[[], datetime]) -> None:
        self._outbox = outbox
        self._envelope = envelope
        self._report = report
        self._inbox = inbox
        self._clock = clock

    @property
    def production_permitted(self) -> bool:
        """Never grant production authority."""
        return False

    @property
    def notification_permitted(self) -> bool:
        """Never grant notification authority."""
        return False

    @property
    def trading_permitted(self) -> bool:
        """Never grant trading authority."""
        return False

    def inspect(self, attempt_id: str) -> AttemptInspection:
        """Sample once, then diagnose an exact attempt without returning evidence.

        A malformed or failing clock makes checked_at unavailable; a valid
        sampled clock is retained when a ledger lookup fails closed.
        """
        try:
            checked_at = self._clock()
            if type(checked_at) is not datetime:
                return AttemptInspection(attempt_id, None, "UNAVAILABLE")
            utc_text(checked_at)
        except Exception:
            return AttemptInspection(attempt_id, None, "UNAVAILABLE")
        try:
            adapter = TrustedAcceptanceBindingAdapter(
                self._outbox, self._envelope, self._report, self._inbox,
                now=checked_at)
            binding = adapter.binding_for(attempt_id)
            status: InspectionStatus = (
                "CONSISTENT_OFFLINE" if (
                    type(binding) is AttemptBinding
                    and binding.attempt_id == attempt_id)
                else "UNAVAILABLE")
        except Exception:
            status = "UNAVAILABLE"
        return AttemptInspection(attempt_id, checked_at, status)
