"""Offline custody adapter adversarial tests; no production authority is assumed."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
import hashlib
import json

import pytest

from quant_backtest.strategy_pool.core import (
    BatchStatus, CoverageApproval, CoverageScope, EventKind,
    Evidence, Hit, PoolState, RunnerObservation, ScanBatch, SecurityId,
    StrategyId, measure_artifact, verify_runner_batch,
)
from quant_backtest.strategy_pool.trusted_receipt_store import (
    ActiveBatchHead, ApprovalCensus, SealedBatchMaterial, qualify_trusted_day,
)

KEY = b"isolated-test-only-credential-not-for-production"
START = datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
SECURITY = SecurityId("CN.SH", "600000")
OTHER = SecurityId("CN.SH", "600001")
STRATEGY = StrategyId("A", "alpha", "v1")
SECOND = StrategyId("A", "beta", "v1")
RULE_HASH = hashlib.sha256(b"isolated-rule").hexdigest()


def batch_for(strategy=STRATEGY, day=14, selected=True, scope=CoverageScope.FULL):
    """Construct an untrusted producer DTO for the fixture."""
    at = START + timedelta(days=day - 14)
    hits = (Hit(SECURITY, Evidence("scan", "signal", at)),) if selected else ()
    return ScanBatch(f"batch-{strategy.name}-{day}", strategy, date(2026, 10, day),
                     at, BatchStatus.COMPLETE, scope, hits)


def raw_hits(batch):
    """Encode the strict original-row fixture without relying on producer metadata."""
    return json.dumps([{"market": hit.security.market, "code": hit.security.code,
                        "source": hit.evidence.source,
                        "reference": hit.evidence.reference,
                        "observed_at": hit.evidence.observed_at.isoformat(),
                        "parent_ids": list(hit.evidence.parent_ids)}
                       for hit in batch.hits], separators=(",", ":")).encode("utf-8")


class IndependentPorts:
    """In-memory test double; positive checks are offline, never production proof."""

    def __init__(self, batches, universe=frozenset({SECURITY}), visited=None):
        self.batches = tuple(batches)
        self.fail_at = ""
        self.approved = True
        self.custody = True
        self.head_authenticated = True
        self.approval_authenticated = True
        self.head_override = None
        self.census = ApprovalCensus(
            batches[0].trade_date, batches[0].produced_at, "sealed-test-census",
            frozenset(batch.strategy for batch in batches),
            tuple(CoverageApproval(batch.strategy, batch.trade_date, batch.scope,
                                   universe, "approved-by-fixture", "v1", RULE_HASH,
                                   "test-calendar") for batch in batches))
        self.materials = {}
        self.heads = {}
        for batch in batches:
            raw = raw_hits(batch)
            observation = RunnerObservation(
                batch.batch_id, batch.strategy, batch.trade_date,
                batch.produced_at + timedelta(minutes=1), True,
                universe if visited is None else visited,
                frozenset(hit.security for hit in batch.hits),
                f"run/{batch.batch_id}", RULE_HASH, "test-calendar", 0,
                measure_artifact(raw, batch.hits, f"sealed/{batch.batch_id}"))
            approval = next(item for item in self.census.approvals
                            if item.strategy == batch.strategy)
            receipt = verify_runner_batch(batch, observation, approval, KEY, raw, batch.hits)
            self.materials[batch.strategy] = SealedBatchMaterial(receipt, observation, raw)
            self.heads[batch.strategy] = ActiveBatchHead(
                batch.strategy, batch.trade_date, batch.batch_id, "revision-1",
                batch.produced_at + timedelta(minutes=1))

    def _check(self, name):
        if self.fail_at == name:
            raise RuntimeError("simulated external authority failure")

    def load_census(self, trade_date, as_of):
        self._check("census")
        return self.census

    def authenticate_census(self, census):
        self._check("census_auth")
        return self.approved

    def authenticate_approval(self, approval, as_of):
        self._check("approval_auth")
        return self.approval_authenticated

    def load_material(self, batch, as_of):
        self._check("material")
        return self.materials[batch.strategy]

    def authenticate_material(self, material):
        self._check("material_auth")
        return self.custody

    def load_active_head(self, strategy, trade_date, as_of):
        self._check("head")
        return self.head_override or self.heads[strategy]

    def authenticate_active_head(self, head):
        self._check("head_auth")
        return self.head_authenticated

    def verification_key(self):
        self._check("key")
        return KEY


def qualify(batches, ports, state=PoolState(), as_of=None):
    """Use a deterministic as-of after sealing the test material."""
    reference = as_of or batches[0].produced_at + timedelta(hours=1)
    return qualify_trusted_day(state, tuple(batches), reference, ports)


def assert_closed(result, original=PoolState()):
    """Assert fail-closed projection and unchanged state."""
    assert result.status == "SOURCE_UNVERIFIED"
    assert result.reason_code == "TRUSTED_SOURCE_UNAVAILABLE"
    assert result.production_permitted is False
    assert result.state == original


def test_all_externally_injected_material_only_qualifies_offline():
    first, second = batch_for(), batch_for(SECOND)
    result = qualify((first, second), IndependentPorts((first, second)))
    assert result.status == "OFFLINE_QUALIFIED"
    assert not result.production_permitted
    assert len(result.state.members) == 2
    assert len(result.state.accepted) == 2


def test_missing_receipt_or_any_essential_port_fails_closed():
    batch = batch_for()
    ports = IndependentPorts((batch,))
    assert_closed(qualify((batch,), None))
    ports.materials[STRATEGY] = replace(ports.materials[STRATEGY], receipt=None)
    assert_closed(qualify((batch,), ports))
    for method in ("load_census", "authenticate_census", "authenticate_approval",
                   "load_material", "authenticate_material", "load_active_head",
                   "authenticate_active_head", "verification_key"):
        broken = IndependentPorts((batch,))
        setattr(broken, method, None)
        assert_closed(qualify((batch,), broken))


def test_full_approval_inventory_is_atomic_and_rejects_missing_strategy():
    first, second = batch_for(), batch_for(SECOND)
    ports = IndependentPorts((first, second))
    assert_closed(qualify((first,), ports))
    ports.census = replace(ports.census, expected_strategies=frozenset({STRATEGY}))
    assert_closed(qualify((first, second), ports))
    ports.census = replace(ports.census, expected_strategies=frozenset({STRATEGY, SECOND}),
                           approvals=(ports.census.approvals[0],))
    assert_closed(qualify((first, second), ports))
    ports = IndependentPorts((first, second))
    ports.materials[SECOND] = replace(ports.materials[SECOND], raw_bytes=b"corrupt")
    assert_closed(qualify((first, second), ports))


def test_original_bytes_change_under_same_receipt_is_not_trusted():
    batch = batch_for()
    ports = IndependentPorts((batch,))
    original = ports.materials[STRATEGY]
    ports.materials[STRATEGY] = replace(original, raw_bytes=original.raw_bytes + b" ")
    assert_closed(qualify((batch,), ports))
    ports.materials[STRATEGY] = replace(original, raw_bytes=b"[]")
    assert_closed(qualify((batch,), ports))


def test_scoped_visit_can_never_masquerade_as_full():
    scoped = batch_for(selected=False, scope=CoverageScope.SCOPED)
    ports = IndependentPorts((scoped,), frozenset({SECURITY, OTHER}),
                             frozenset({SECURITY}))
    qualified = qualify((scoped,), ports)
    assert qualified.status == "OFFLINE_QUALIFIED"
    forged = replace(scoped, scope=CoverageScope.FULL)
    assert_closed(qualify((forged,), ports))
    ports.census = replace(ports.census, approvals=(replace(
        ports.census.approvals[0], scope=CoverageScope.FULL),))
    assert_closed(qualify((forged,), ports))


def test_historical_replay_and_revoked_ledger_head_fail_closed():
    first = batch_for(day=14)
    first_ports = IndependentPorts((first,))
    accepted = qualify((first,), first_ports).state
    current = batch_for(day=15, selected=False)
    current_ports = IndependentPorts((current,))
    newer = qualify((current,), current_ports, accepted,
                    current.produced_at + timedelta(hours=1)).state
    old_result = qualify((first,), first_ports, newer,
                         current.produced_at + timedelta(hours=1))
    assert_closed(old_result, newer)
    replay = qualify((current,), current_ports, newer,
                     current.produced_at + timedelta(hours=1))
    assert replay.state is newer and not replay.production_permitted
    current_ports.head_override = replace(current_ports.heads[STRATEGY],
                                          batch_id="newer-authorized-head")
    assert_closed(qualify((current,), current_ports, newer,
                          current.produced_at + timedelta(hours=1)), newer)
    current_ports.head_override = None
    current_ports.head_authenticated = False
    assert_closed(qualify((current,), current_ports, newer,
                          current.produced_at + timedelta(hours=1)), newer)


def test_absence_does_not_automatically_exit_even_after_many_complete_days():
    first = batch_for()
    state = qualify((first,), IndependentPorts((first,))).state
    for day in (15, 16, 17, 18):
        empty = batch_for(day=day, selected=False)
        result = qualify((empty,), IndependentPorts((empty,)), state,
                         empty.produced_at + timedelta(hours=1))
        assert result.status == "OFFLINE_QUALIFIED"
        state = result.state
    assert state.members[0].active
    assert state.members[0].consecutive_absences == 4
    assert all(event.kind is not EventKind.EXIT for event in state.events)


@pytest.mark.parametrize("port", ["census", "census_auth", "approval_auth", "material",
                                  "material_auth", "head", "head_auth", "key"])
def test_any_external_authority_exception_fails_closed(port):
    batch = batch_for()
    ports = IndependentPorts((batch,))
    ports.fail_at = port
    assert_closed(qualify((batch,), ports))


def test_external_revocation_and_untrusted_time_fail_closed():
    batch = batch_for()
    ports = IndependentPorts((batch,))
    ports.approval_authenticated = False
    assert_closed(qualify((batch,), ports))
    ports = IndependentPorts((batch,))
    ports.custody = False
    assert_closed(qualify((batch,), ports))
    ports = IndependentPorts((batch,))
    assert_closed(qualify((batch,), ports, as_of=batch.produced_at))
    assert_closed(qualify((batch,), ports,
                          as_of=datetime(2026, 10, 14, 13)))
