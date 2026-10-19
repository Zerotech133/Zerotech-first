"""Failure-closed, purely offline binding of a B member receipt to an owner rule.

This diagnostic neither persists nor issues a membership, notification, or trade.
The authorities must be external to the caller's event list. Even matching independent
reads leave TOCTOU, rollback, and ABA exposure: production needs an atomic,
independent, non-rollbackable version root across the event stream, membership
receipt, input floor, authorization and consent. This is not a production gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol, Sequence

from quant_backtest.watch_owner_rules import (
    RuleAuthority,
    RuleEvent,
    RuleView,
    SecurityKey,
    StreamHead,
    event_digest,
    read_owner_rules,
)

_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


@dataclass(frozen=True)
class BBindingProof:
    """Independent receipt for one member/input and an exact committed rule event."""

    key: SecurityKey
    membership_event_id: str
    persisted_input_id: str
    authorized_principal: str
    rule_id: str
    rule_version: int
    input_version: int
    rule_event_id: str
    event_digest: str
    stream_head: StreamHead
    valid_from: datetime
    valid_until: datetime
    revoked: bool


@dataclass(frozen=True)
class BBindingVerdict:
    """Offline diagnostic; no status or receipt can convey write permission."""

    status: str
    code: str
    proof: BBindingProof | None = None

    @property
    def production_permitted(self) -> bool:
        """Deny production consumption even when the offline binding matches."""
        return False

    @property
    def notification_permitted(self) -> bool:
        """Deny notification delivery unconditionally."""
        return False

    @property
    def trading_permitted(self) -> bool:
        """Deny trade placement unconditionally."""
        return False


class BBindingAuthority(Protocol):
    """External receipt and minimum persisted-input-version trust ports.

    Neither port may derive its authority from the supplied event list or context.
    A live implementation must pin the receipt/floor/head atomically to a separate
    non-rollbackable root; this offline adapter cannot provide that guarantee.
    """

    def resolve(self, key: SecurityKey, membership_event_id: str) -> BBindingProof | None:
        """Resolve the independently persisted receipt for this exact partition."""

    def authorized_input_floor(
        self, key: SecurityKey, authorized_principal: str, persisted_input_id: str
    ) -> int:
        """Return the independently retained minimum authorized input version."""


def _label(value: object) -> bool:
    """Validate identifiers without coercing attacker-controlled values."""
    return type(value) is str and _LABEL.fullmatch(value) is not None


def _utc(value: object) -> bool:
    """Accept only exact aware datetime instances with zero UTC offset."""
    return (type(value) is datetime and value.tzinfo is not None
            and value.utcoffset() is not None
            and value.utcoffset().total_seconds() == 0)


def _head(head: object, key: SecurityKey) -> bool:
    """Check exact head identity, count, and canonical digest syntax."""
    return (type(head) is StreamHead and type(head.key) is SecurityKey
            and head.key == key and type(head.count) is int and head.count > 0
            and type(head.digest) is str and _SHA.fullmatch(head.digest) is not None)


def _event_matches_view(event: RuleEvent, view: RuleView) -> bool:
    """Compare the event's full projected rule revision and half-open window."""
    return (event.rule_id == view.rule_id
            and type(event.rule_version) is int
            and event.rule_version == view.rule_version
            and type(event.input_version) is int
            and event.input_version == view.input_version
            and event.rule_body == view.rule_body
            and event.valid_from == view.valid_from
            and event.valid_until == view.valid_until)


def _snapshot(events: Sequence[RuleEvent]) -> tuple[RuleEvent, ...]:
    """Copy complete event envelopes, including nested exact security identities."""
    if type(events) not in (tuple, list):
        raise ValueError("EVENT_STREAM_INVALID")
    frozen: list[RuleEvent] = []
    for event in events:
        if type(event) is not RuleEvent or type(event.key) is not SecurityKey:
            raise ValueError("EVENT_INVALID")
        frozen.append(replace(event, key=replace(event.key)))
    return tuple(frozen)


def _stable_stream(events: Sequence[RuleEvent], snapshot: tuple[RuleEvent, ...],
                   before: StreamHead, after: StreamHead, key: SecurityKey) -> bool:
    """Recheck each original envelope/digest and the full anchored hash chain."""
    if (type(events) not in (tuple, list) or not _head(after, key)
            or before != after or len(events) != len(snapshot)
            or before.count != len(snapshot)):
        return False
    previous = "0" * 64
    for original, frozen in zip(events, snapshot):
        if (type(original) is not RuleEvent or type(original.key) is not SecurityKey
                or original != frozen or original.key != key
                or frozen.key != key or frozen.previous_hash != previous):
            return False
        original_hash = event_digest(original)
        frozen_hash = event_digest(frozen)
        if original_hash != frozen_hash:
            return False
        previous = frozen_hash
    return previous == before.digest


def verify_b_membership(
    events: Sequence[RuleEvent], *, key: SecurityKey, context: object,
    as_of: datetime, membership_event_id: str, rule_authority: RuleAuthority,
    binding_authority: BBindingAuthority,
) -> BBindingVerdict:
    """Cross-bind a live authorized rule revision and an independent B receipt.

    The supplied events and synthetic authorities are never credentials. Reads
    before/after this operation reduce accidental drift but do not eliminate
    TOCTOU, rollback, or ABA; production requires an atomic independent,
    non-rollbackable version root and its own authorization decision.
    """
    def denied(code: str) -> BBindingVerdict:
        return BBindingVerdict("UNVERIFIED", code)

    try:
        if type(key) is not SecurityKey or not _utc(as_of) or not _label(membership_event_id):
            return denied("INPUT_INVALID")
        # Capture the independent anchor around the authoritative read as well
        # as around receipt/floor resolution, not just around the local replay.
        before = rule_authority.stream_head(key)
        if not _head(before, key):
            return denied("STREAM_ANCHOR_INVALID")
        snapshot = _snapshot(events)
        if not _stable_stream(events, snapshot, before, before, key):
            return denied("STREAM_ANCHOR_INVALID")
        rule_verdict = read_owner_rules(
            events, key=key, context=context, as_of=as_of, authority=rule_authority
        )
        if rule_verdict.status != "VERIFIED_OFFLINE" or rule_verdict.code != "OK":
            return denied("RULE_NOT_VERIFIED")
        proof = binding_authority.resolve(key, membership_event_id)
        if type(proof) is not BBindingProof:
            return denied("RECEIPT_INVALID")
        if (type(proof.key) is not SecurityKey or proof.key != key
                or proof.membership_event_id != membership_event_id
                or not _label(proof.persisted_input_id)
                or not _label(proof.authorized_principal)
                or proof.authorized_principal != key.owner
                or not _label(proof.rule_id) or not _label(proof.rule_event_id)
                or type(proof.rule_version) is not int or proof.rule_version < 1
                or type(proof.input_version) is not int or proof.input_version < 1
                or type(proof.event_digest) is not str
                or _SHA.fullmatch(proof.event_digest) is None
                or not _head(proof.stream_head, key)
                or not _utc(proof.valid_from) or not _utc(proof.valid_until)
                or not (proof.valid_from <= as_of < proof.valid_until)
                or proof.revoked is not False):
            return denied("RECEIPT_INVALID")
        # A rule ID can have many historical revisions, but only one live view.
        views = [view for view in rule_verdict.rules
                 if type(view) is RuleView and view.rule_id == proof.rule_id]
        if len(views) != 1:
            return denied("RULE_NOT_CURRENT")
        matching = [event for event in snapshot
                    if type(event) is RuleEvent and event.rule_id == proof.rule_id]
        if not matching or matching[-1].action not in ("CREATE", "UPDATE"):
            return denied("RULE_NOT_CURRENT")
        current = matching[-1]
        view = views[0]
        if (not _event_matches_view(current, view)
                or current.event_id != proof.rule_event_id
                or current.principal_id != proof.authorized_principal
                or current.key != key
                or current.rule_version != proof.rule_version
                or current.input_version != proof.input_version
                or not (current.valid_from <= as_of < current.valid_until)
                or event_digest(current) != proof.event_digest):
            return denied("RULE_BINDING_MISMATCH")
        floor = binding_authority.authorized_input_floor(
            key, proof.authorized_principal, proof.persisted_input_id
        )
        if type(floor) is not int or floor < 1 or proof.input_version < floor:
            return denied("INPUT_FLOOR_INVALID")
        after = rule_authority.stream_head(key)
        if (proof.stream_head != before
                or not _stable_stream(events, snapshot, before, after, key)):
            return denied("STREAM_ANCHOR_INVALID")
        return BBindingVerdict("B_BOUND_OFFLINE", "OK", proof)
    except Exception:
        # No exception text or opaque rule_body crosses the failure boundary.
        return denied("AUTHORITY_OR_INPUT_UNAVAILABLE")
