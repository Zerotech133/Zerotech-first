"""Synthetic offline contract tests, not evidence of production trusted roots."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from quant_backtest.watch_b_membership_offline import (
    BBindingProof,
    verify_b_membership,
)
from quant_backtest.watch_owner_rules import (
    ConsentProof,
    Principal,
    RuleEvent,
    SecurityKey,
    StreamHead,
    event_digest,
)

NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
KEY = SecurityKey("A", "000001", "alice")
GENESIS = "0" * 64


class RulePort:
    """Synthetic independent head, auth, classifier and consent port."""

    def __init__(self) -> None:
        self.head = StreamHead(KEY, 0, GENESIS)
        self.consent: ConsentProof | None = None
        self.fail = False
        self.drift = False
        self.calls = 0

    def authenticate(self, context: object) -> Principal | None:
        if self.fail:
            raise RuntimeError("private rule authority failure")
        return Principal("alice") if context == "token" else None

    def authorize(self, principal: Principal, key: SecurityKey, operation: str) -> bool:
        return principal == Principal("alice") and key == KEY and operation == "read"

    def stream_head(self, key: SecurityKey) -> StreamHead | None:
        self.calls += 1
        if self.fail:
            raise RuntimeError("private head failure")
        if self.drift and self.calls >= 3:
            return replace(self.head, digest="f" * 64)
        return self.head

    def classify_rule(self, body: str) -> str | None:
        return "COST" if body.startswith("COST:") else "STANDARD" if body.startswith("WATCH:") else None

    def cost_consent(self, consent_id: str) -> ConsentProof | None:
        return self.consent if self.consent and self.consent.consent_id == consent_id else None

    def anchor(self, events: list[RuleEvent]) -> None:
        self.head = StreamHead(KEY, len(events), event_digest(events[-1]) if events else GENESIS)
        self.calls = 0


class BindingPort:
    """Synthetic receipt and floor; not a real persisted authority."""

    def __init__(self, proof: BBindingProof) -> None:
        self.proof = proof
        self.floor: object = 1
        self.fail_resolve = False
        self.fail_floor = False

    def resolve(self, key: SecurityKey, membership_event_id: str) -> BBindingProof | None:
        if self.fail_resolve:
            raise RuntimeError("private receipt error")
        return self.proof

    def authorized_input_floor(self, key: SecurityKey, authorized_principal: str,
                               persisted_input_id: str) -> int:
        if self.fail_floor:
            raise RuntimeError("private floor error")
        return self.floor  # type: ignore[return-value]


def fixture(body: str = "WATCH:price>10", *, cost: bool = False
            ) -> tuple[list[RuleEvent], RulePort, BindingPort]:
    """Build separately held but only synthetic rule and membership evidence."""
    authority = RulePort()
    events = [RuleEvent(KEY, "rule-event-1", 1, GENESIS, "CREATE", "rule-1", 1,
                        3, "alice", NOW - timedelta(minutes=5),
                        NOW - timedelta(hours=1), NOW + timedelta(hours=1),
                        body, "consent-1" if cost else "")]
    authority.anchor(events)
    if cost:
        authority.consent = ConsentProof(
            "consent-1", KEY, "alice", "rule-1", 1,
            hashlib.sha256(body.encode("utf-8")).hexdigest(),
            NOW - timedelta(hours=2), NOW + timedelta(hours=2), False,
        )
    proof = BBindingProof(
        KEY, "member-event-77", "persisted-input-9", "alice", "rule-1", 1, 3,
        "rule-event-1", event_digest(events[0]), authority.head,
        NOW - timedelta(minutes=1), NOW + timedelta(minutes=1), False,
    )
    return events, authority, BindingPort(proof)


def check(events: list[RuleEvent], rules: RulePort, binding: BindingPort, *,
          key: SecurityKey = KEY, at: datetime = NOW,
          member_id: str = "member-event-77"):
    """Exercise only the new offline binding verifier."""
    return verify_b_membership(events, key=key, context="token", as_of=at,
                               membership_event_id=member_id,
                               rule_authority=rules, binding_authority=binding)


def assert_closed(result: object) -> None:
    """Assert every failure lacks any production privilege or receipt leak."""
    assert result.status == "UNVERIFIED"
    assert result.proof is None
    assert result.production_permitted is False
    assert result.notification_permitted is False
    assert result.trading_permitted is False
    assert not hasattr(result, "rule_body")


def test_success_is_only_offline_and_ids_are_separate() -> None:
    events, rules, binding = fixture()
    result = check(events, rules, binding)
    assert result.status == "B_BOUND_OFFLINE" and result.code == "OK"
    assert result.proof == binding.proof
    assert binding.proof.membership_event_id != binding.proof.rule_event_id
    assert result.production_permitted is False
    assert result.notification_permitted is False
    assert result.trading_permitted is False
    assert not hasattr(result, "rule_body")


@pytest.mark.parametrize("field,value", [
    ("key", SecurityKey("HK", "000001", "alice")),
    ("key", SecurityKey("A", "000001", "mallory")),
    ("authorized_principal", "mallory"),
    ("membership_event_id", "wrong-member"),
    ("persisted_input_id", ""),
    ("rule_id", "other-rule"),
    ("rule_version", 2),
    ("input_version", 2),
    ("rule_event_id", "wrong-event"),
    ("event_digest", "f" * 64),
    ("stream_head", StreamHead(KEY, 1, "f" * 64)),
    ("revoked", True),
    ("revoked", 0),
    ("valid_until", NOW),
    ("valid_from", NOW + timedelta(seconds=1)),
    ("valid_from", NOW.replace(tzinfo=None)),
])
def test_receipt_exact_binding_and_half_open_window(field: str, value: object) -> None:
    events, rules, binding = fixture()
    binding.proof = replace(binding.proof, **{field: value})
    assert_closed(check(events, rules, binding))


def test_cross_market_owner_and_bad_request_fail_closed() -> None:
    events, rules, binding = fixture()
    for key in (SecurityKey("HK", "000001", "alice"),
                SecurityKey("A", "000001", "mallory")):
        assert_closed(check(events, rules, binding, key=key))
    assert_closed(check(events, rules, binding, at=NOW.replace(tzinfo=None)))
    assert_closed(check(events, rules, binding, member_id="../malformed"))


def test_body_digest_stream_terminal_missing_tail_and_head_drift() -> None:
    events, rules, binding = fixture()
    assert_closed(check([replace(events[0], rule_body="WATCH:forged")], rules, binding))
    assert_closed(check([], rules, binding))
    rules.drift = True
    assert_closed(check(events, rules, binding))
    rules.drift = False
    rules.anchor(events)
    extra = replace(events[0], event_id="event-2", sequence=2,
                    previous_hash=event_digest(events[0]), action="UPDATE",
                    rule_version=2, input_version=4, rule_body="WATCH:updated")
    events.append(extra)
    rules.anchor(events)
    assert_closed(check(events, rules, binding))


@pytest.mark.parametrize("mutation", ["replace-earlier", "in-place-earlier", "remove-earlier", "append"])
def test_replay_to_receipt_local_stream_mutation_fails_closed(mutation: str) -> None:
    """Replay may succeed before the receipt port changes a nonterminal event."""
    events, rules, binding = fixture()
    earlier = replace(events[0], event_id="other-event", rule_id="other-rule",
                      input_version=1, issued_at=NOW - timedelta(minutes=10))
    current = replace(events[0], sequence=2, previous_hash=event_digest(earlier))
    events[:] = [earlier, current]
    rules.anchor(events)
    binding.proof = replace(binding.proof, stream_head=rules.head,
                            event_digest=event_digest(current))

    class MutatingPort(BindingPort):
        """Synthetic adversarial trusted port mutating caller-owned input."""

        def resolve(self, key: SecurityKey, membership_event_id: str) -> BBindingProof | None:
            proof = super().resolve(key, membership_event_id)
            if mutation == "replace-earlier":
                events[0] = replace(events[0], rule_body="WATCH:changed")
            elif mutation == "in-place-earlier":
                object.__setattr__(events[0], "rule_body", "WATCH:changed")
            elif mutation == "remove-earlier":
                events.pop(0)
            else:
                events.append(replace(events[-1], event_id="unexpected"))
            return proof

    result = check(events, rules, MutatingPort(binding.proof))
    assert_closed(result)
    assert result.code == "STREAM_ANCHOR_INVALID"
    if mutation != "append":
        assert events[-1] == current
        assert rules.head.digest == event_digest(current)


def test_tombstone_and_cost_consent_withdrawal() -> None:
    events, rules, binding = fixture("COST:commission=1", cost=True)
    assert check(events, rules, binding).status == "B_BOUND_OFFLINE"
    rules.consent = replace(rules.consent, revoked=True)
    assert_closed(check(events, rules, binding))
    tombstone = replace(events[0], event_id="event-2", sequence=2,
                        previous_hash=event_digest(events[0]), action="REVOKE",
                        rule_version=2, input_version=4, rule_body="",
                        consent_id="", issued_at=NOW, valid_from=NOW)
    events.append(tombstone)
    rules.anchor(events)
    assert_closed(check(events, rules, binding))


@pytest.mark.parametrize("floor", [True, 0, 4, "3", None, 3])
def test_independent_floor_exact_int_and_minimum(floor: object) -> None:
    events, rules, binding = fixture()
    binding.floor = floor
    result = check(events, rules, binding)
    if floor == 3:
        assert result.status == "B_BOUND_OFFLINE"
    else:
        assert_closed(result)


def test_trusted_port_failures_and_missing_receipt_are_closed() -> None:
    events, rules, binding = fixture()
    binding.fail_resolve = True
    assert_closed(check(events, rules, binding))
    binding.fail_resolve = False
    binding.fail_floor = True
    assert_closed(check(events, rules, binding))
    binding.fail_floor = False
    rules.fail = True
    assert_closed(check(events, rules, binding))
    rules.fail = False
    binding.proof = None  # type: ignore[assignment]
    assert_closed(check(events, rules, binding))
