"""Offline public-API composition of watch reporting and sector acceptance.

The in-memory sector inbox is an injected test double for a sector-owned ledger,
not production provenance, agent authentication, delivery, or trading authority.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.agent_bridge.mapping import to_agent_ack, to_sector_report
from quant_backtest.agent_bridge.trusted_acceptance import TrustedAcceptanceBindingAdapter
from quant_backtest.agent_bridge.upstream_ack_authority import UpstreamAckAuthority
from quant_backtest.quant_sector.upstream_inbox import UpstreamInbox, UpstreamReceipt
from quant_backtest.watch_dispatch import freeze_offline
from quant_backtest.watch_dispatch.agent_reporting import (
    AgentAck,
    AgentIdentity,
    AgentReport,
    AgentUpstreamReporter,
)
from quant_backtest.watch_dispatch.contracts import EvidenceEnvelope
from quant_backtest.watch_dispatch.evidence_store import EvidenceStore
from quant_backtest.watch_dispatch.route_outbox import (
    ACCEPTED_VERIFIED,
    CLAIMED,
    UNKNOWN,
    RouteAttempt,
    RouteOutbox,
)
from quant_backtest.watch_dispatch.signal_pool import (
    STATUS_ACCEPTED,
    STATUS_CLAIMED,
    AcceptanceReceipt,
    SignalPool,
    SignalPoolDraft,
    SignalPoolError,
)

T = datetime(2026, 10, 19, 2, 0, tzinfo=timezone.utc)
END = T + timedelta(minutes=30)
ROUTE = "QUANT_UPSTREAM_REPORT"
CONTROL_EPOCH = 7


def at(seconds: int) -> datetime:
    """Return an explicit UTC point on the deterministic scenario timeline."""
    return T + timedelta(seconds=seconds)


class TestDirectory:
    """Fixed local agent registry fixture, not a production identity source."""

    __test__ = False

    def __init__(self, receiver: AgentIdentity) -> None:
        self.receiver = receiver

    def resolve(self, agent_id: str, *, role: str,
                now: datetime) -> AgentIdentity | None:
        """Resolve exactly the registered sector identity in this test."""
        if (agent_id == self.receiver.agent_id
                and role == self.receiver.role
                and self.receiver.registered_at <= now < self.receiver.valid_until):
            return self.receiver
        return None


class TestAckBinding:
    """Test-only report-field verifier; live receipt authority is separate."""

    __test__ = False

    def verify(self, ack: AgentAck, report: AgentReport,
               now: datetime) -> AgentAck | None:
        """Verify shape and report binding, not live sector provenance."""
        if (type(ack) is AgentAck and ack.report_id == report.report_id
                and ack.receiver_agent_id == report.receiver.agent_id
                and report.created_at <= ack.acked_at <= now):
            return ack
        return None


@dataclass
class Scenario:
    """Only public participants and issued records of one offline pipeline."""

    envelope: EvidenceEnvelope
    sender: AgentIdentity
    inbox: UpstreamInbox
    outbox: RouteOutbox
    reporter: AgentUpstreamReporter
    report: AgentReport
    pool: SignalPool
    first_claim: RouteAttempt
    first_result: RouteAttempt


def make_scenario(*, active_port: bool) -> Scenario:
    """Run report and wrong-ACK g0 before sector acceptance at T+4."""
    envelope = freeze_offline(
        schema_version=1, event_id="ev-public-composition", evidence_version=1,
        previous_version_hash=None, pool="B", tenant_or_owner_id="owner1",
        market="SH", code="600000", source_membership_id="member1",
        source_revision="rev1", rule_id="r1", rule_version=1,
        comparator="GE", threshold="1.00", unit="ratio", window="5m",
        observed_value="1.10", baseline_value="1.00", raw_ref="raw:1",
        raw_digest="a" * 64, source_id="src1", calendar_id="cal1",
        source_as_of=T, visible_at=T, detected_at=T, freeze_at=T,
        valid_until=END, facts=("observation",), hypotheses=("candidate",),
        supporting_refs=("raw:1",), contradicting_refs=(),
        confirmation_condition="confirm", invalidation_condition="invalidate")
    sender = AgentIdentity("watch1", "WATCH", "registry:watch",
                           T - timedelta(days=1), END)
    receiver = AgentIdentity("sector1", "QUANT_SECTOR", "registry:sector",
                             T - timedelta(days=1), END)
    store = EvidenceStore()
    store.freeze(envelope)
    pool = SignalPool(store)
    inbox = UpstreamInbox()
    # Constructor injection is the only authority installation in these tests.
    outbox = RouteOutbox(active_ack_port=UpstreamAckAuthority(inbox)
                         if active_port else None)
    reporter = AgentUpstreamReporter(sender, TestDirectory(receiver), TestAckBinding())
    pool.append(SignalPoolDraft("signal1", "ANOMALY", envelope.event_id,
                                envelope.envelope_hash, envelope.evidence_version),
                producer=sender, store=store, now=T)
    pool.claim("signal1", lease_until=at(20), now=at(1))
    report = reporter.report(
        envelope, outbox, report_kind="ANOMALY", receiver_agent_id=receiver.agent_id,
        report_id="report1", control_epoch=CONTROL_EPOCH, now=at(2))
    wrong_ack = AgentAck("wrong-ack", "different-report", receiver.agent_id,
                         at(3), "test-only-untrusted")
    first_claim, first_result = reporter.claim_and_confirm(
        envelope, outbox, report, claim_token="g0", claim_at=at(3),
        lease_until=at(20), ack=wrong_ack, now=at(3))
    assert first_claim.outcome == CLAIMED
    assert first_result.outcome == UNKNOWN and first_result.receipt is None
    assert outbox.get_intent(envelope, route=ROUTE, target_type="AGENT",
                             target_id=receiver.agent_id).status == UNKNOWN
    assert pool.get("signal1").status == STATUS_CLAIMED
    return Scenario(envelope, sender, inbox, outbox, reporter, report,
                    pool, first_claim, first_result)


def retry(scenario: Scenario) -> AgentReport:
    """Plan g1 at T+5 without changing the original report identity."""
    original = scenario.reporter.retry_report(
        scenario.envelope, scenario.outbox, scenario.report,
        generation=1, control_epoch=CONTROL_EPOCH, now=at(5))
    assert original is scenario.report
    intent = scenario.outbox.get_intent(
        scenario.envelope, route=ROUTE, target_type="AGENT",
        target_id=scenario.report.receiver.agent_id)
    assert intent is not None and intent.intent_id == "report-report1-g1"
    assert intent.created_at == at(5) and intent.control_epoch == CONTROL_EPOCH
    return original


def confirm_retry(scenario: Scenario, ack: AgentAck) -> tuple[RouteAttempt, RouteAttempt]:
    """Claim g1 at T+6 and finish at T+8 with the supplied sector-shaped ACK."""
    claimed, settled = scenario.reporter.claim_and_confirm(
        scenario.envelope, scenario.outbox, scenario.report,
        claim_token="g1", claim_at=at(6), lease_until=at(20),
        ack=ack, now=at(8))
    assert settled is not None
    assert claimed.outcome == CLAIMED and claimed.attempt_no == 2
    assert scenario.outbox.get_attempts(
        scenario.envelope, route=ROUTE, target_type="AGENT",
        target_id=scenario.report.receiver.agent_id)[0] == scenario.first_result
    return claimed, settled


def assert_pool_claimed(scenario: Scenario, attempt_id: str,
                        ack_id: str, *, now: datetime) -> None:
    """Reject a premature or stale route attempt without promoting the pool."""
    binding = TrustedAcceptanceBindingAdapter(
        scenario.outbox, scenario.envelope, scenario.report, scenario.inbox, now=now)
    assert binding.binding_for(attempt_id) is None
    with pytest.raises(SignalPoolError, match="RECEIPT_ATTEMPT_UNKNOWN"):
        scenario.pool.mark_accepted(
            "signal1", receipt=AcceptanceReceipt(ROUTE, attempt_id, ack_id),
            binding_port=binding)
    assert scenario.pool.get("signal1").status == STATUS_CLAIMED
    assert scenario.pool.receipt("signal1") is None


def test_public_reporter_old_live_ack_settles_g1_and_pool() -> None:
    """Real report/retry/claim interfaces compose with a live old sector ACK."""
    scenario = make_scenario(active_port=True)
    assert_pool_claimed(scenario, scenario.first_claim.attempt_id,
                        "wrong-ack", now=at(3))
    # The sector payload hash includes report identity and is NOT the watch
    # evidence hash; neither hash is a signature or a source-authentication root.
    mapped = to_sector_report(scenario.report)
    assert mapped.evidence_hash == scenario.envelope.envelope_hash
    assert mapped.envelope_hash != mapped.evidence_hash
    accepted = scenario.inbox.accept(mapped, now=at(4))
    assert accepted.envelope_hash == mapped.envelope_hash
    assert accepted.ledger_epoch == scenario.inbox.ledger_epoch == 0
    assert_pool_claimed(scenario, scenario.first_claim.attempt_id,
                        accepted.ack.ack_id, now=at(4))
    retry(scenario)
    claimed, settled = confirm_retry(scenario, to_agent_ack(accepted.ack))
    assert settled.outcome == ACCEPTED_VERIFIED and settled.receipt is not None
    assert settled.receipt.observed_at == at(4) < claimed.claim_at == at(6)
    assert settled.receipt.external_message_id == accepted.ack.ack_id
    assert scenario.first_result.outcome == UNKNOWN
    assert scenario.outbox.epoch_floor(scenario.envelope) == CONTROL_EPOCH
    binding = TrustedAcceptanceBindingAdapter(
        scenario.outbox, scenario.envelope, scenario.report, scenario.inbox,
        now=at(9))
    assert binding.binding_for(claimed.attempt_id) is not None
    pool_receipt = AcceptanceReceipt(ROUTE, claimed.attempt_id, accepted.ack.ack_id)
    assert scenario.pool.mark_accepted(
        "signal1", receipt=pool_receipt, binding_port=binding).status == STATUS_ACCEPTED
    assert scenario.pool.receipt("signal1") == pool_receipt
    assert not any((binding.production_permitted, binding.notification_permitted,
                    binding.trading_permitted, scenario.outbox.production_permitted,
                    scenario.pool.trading_permitted))


def test_inbox_epoch_is_not_outbox_control_epoch() -> None:
    """Unrelated inbox revocation changes its epoch without changing g1 authority."""
    scenario = make_scenario(active_port=True)
    # T+4: advance only the inbox ledger using a distinct report, then accept
    # the real report at the same clock tick. The watch control epoch stays 7.
    unrelated = replace(to_sector_report(scenario.report), report_id="unrelated")
    obsolete = scenario.inbox.accept(unrelated, now=at(4))
    scenario.inbox.revoke(obsolete.receipt_id, now=at(4))
    accepted = scenario.inbox.accept(to_sector_report(scenario.report), now=at(4))
    assert accepted.ledger_epoch == scenario.inbox.ledger_epoch == 1
    assert scenario.inbox.is_active(accepted.receipt_id)
    assert scenario.outbox.epoch_floor(scenario.envelope) == CONTROL_EPOCH
    retry(scenario)
    claimed, settled = confirm_retry(scenario, to_agent_ack(accepted.ack))
    assert settled.outcome == ACCEPTED_VERIFIED
    binding = TrustedAcceptanceBindingAdapter(
        scenario.outbox, scenario.envelope, scenario.report, scenario.inbox,
        now=at(9))
    assert binding.binding_for(claimed.attempt_id) is not None
    assert scenario.pool.mark_accepted(
        "signal1", receipt=AcceptanceReceipt(ROUTE, claimed.attempt_id,
                                               accepted.ack.ack_id),
        binding_port=binding).status == STATUS_ACCEPTED


def test_no_constructor_bound_active_port_cannot_promote_old_ack() -> None:
    """A genuine inbox ACK alone cannot authorize a pre-g1 observation."""
    scenario = make_scenario(active_port=False)
    accepted = scenario.inbox.accept(to_sector_report(scenario.report), now=at(4))
    retry(scenario)
    claimed, settled = confirm_retry(scenario, to_agent_ack(accepted.ack))
    assert settled.outcome == UNKNOWN and settled.receipt is None
    assert_pool_claimed(scenario, claimed.attempt_id, accepted.ack.ack_id, now=at(9))


def test_revoked_target_receipt_cannot_promote_old_ack() -> None:
    """The target inbox receipt revoked at T+7 fails live check at T+8."""
    scenario = make_scenario(active_port=True)
    accepted: UpstreamReceipt = scenario.inbox.accept(
        to_sector_report(scenario.report), now=at(4))
    retry(scenario)
    scenario.inbox.revoke(accepted.receipt_id, now=at(7))
    assert not scenario.inbox.is_active(accepted.receipt_id)
    claimed, settled = confirm_retry(scenario, to_agent_ack(accepted.ack))
    assert settled.outcome == UNKNOWN and settled.receipt is None
    assert_pool_claimed(scenario, claimed.attempt_id, accepted.ack.ack_id, now=at(9))


def test_wrong_ack_id_cannot_promote_even_when_report_fields_match() -> None:
    """A test verifier's field match cannot replace the live sector ACK ID."""
    scenario = make_scenario(active_port=True)
    accepted = scenario.inbox.accept(to_sector_report(scenario.report), now=at(4))
    retry(scenario)
    forged = replace(to_agent_ack(accepted.ack), ack_id="different-ack")
    claimed, settled = confirm_retry(scenario, forged)
    assert settled.outcome == UNKNOWN and settled.receipt is None
    assert_pool_claimed(scenario, claimed.attempt_id, forged.ack_id, now=at(9))
