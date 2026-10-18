"""Isolated tests for offline B-owner append-only rule qualification."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from quant_backtest.watch_owner_rules import (
    ConsentProof,
    Principal,
    RuleCommand,
    RuleEvent,
    SecurityKey,
    StreamHead,
    event_digest,
    propose_owner_rule,
    read_owner_rules,
)


NOW = datetime(2026, 10, 19, 10, tzinfo=timezone.utc)
GENESIS = "0" * 64
KEY = SecurityKey("A", "000001", "alice")
OTHER_MARKET = SecurityKey("HK", "000001", "alice")


class OutsideAuthority:
    """Test-only independently supplied identity, anchor, policy and consent ports.

    This fixture is not a production credential, DB or persisted external root.
    """

    def __init__(self) -> None:
        self.principal = Principal("alice")
        self.grants: set[tuple[SecurityKey, str]] = {(KEY, "read"), (KEY, "write")}
        self.heads: dict[SecurityKey, StreamHead] = {KEY: StreamHead(KEY, 0, GENESIS)}
        self.consents: dict[str, ConsentProof] = {}
        self.broken = False

    def authenticate(self, context: object) -> Principal | None:
        if self.broken:
            raise RuntimeError("independent authority offline")
        return self.principal if context == "authenticated-token" else None

    def authorize(self, principal: Principal, key: SecurityKey, operation: str) -> bool:
        return (key, operation) in self.grants and principal == self.principal

    def stream_head(self, key: SecurityKey) -> StreamHead | None:
        return self.heads.get(key)

    def classify_rule(self, body: str) -> str | None:
        if body.startswith("COST:"):
            return "COST"
        if body.startswith("WATCH:"):
            return "STANDARD"
        return None

    def cost_consent(self, consent_id: str) -> ConsentProof | None:
        return self.consents.get(consent_id)

    def anchor(self, key: SecurityKey, events: list[RuleEvent]) -> None:
        self.heads[key] = StreamHead(key, len(events),
                                     event_digest(events[-1]) if events else GENESIS)


def command(action: str = "CREATE", *, version: int = 1, input_version: int = 1,
            body: str = "WATCH:price>10", consent: str = "",
            event_id: str = "event-1", start: datetime | None = None,
            end: datetime | None = None) -> RuleCommand:
    """Build an explicit rule command, not an authorization receipt."""
    return RuleCommand(action, "rule-1", version, input_version, body, consent,
                       start if start is not None else NOW - timedelta(hours=1),
                       end if end is not None else NOW + timedelta(hours=1), event_id)


def propose(authority: OutsideAuthority, events: list[RuleEvent],
            requested: RuleCommand, *, at: datetime = NOW,
            key: SecurityKey = KEY):
    """Propose without silently modifying the independently held stream head."""
    return propose_owner_rule(events, requested, key=key,
                              context="authenticated-token", as_of=at,
                              authority=authority)


def commit(authority: OutsideAuthority, events: list[RuleEvent],
           requested: RuleCommand, *, at: datetime = NOW,
           key: SecurityKey = KEY) -> RuleEvent:
    """Model external commit and independent head anchoring in the test fixture."""
    verdict = propose(authority, events, requested, at=at, key=key)
    assert verdict.status == "PROPOSED_UNCOMMITTED" and verdict.proposal is not None
    events.append(verdict.proposal)
    authority.anchor(key, events)
    return verdict.proposal


def read(authority: OutsideAuthority, events: list[RuleEvent], *,
         key: SecurityKey = KEY, at: datetime = NOW,
         context: object = "authenticated-token"):
    """Read only the independently authenticated owner partition."""
    return read_owner_rules(events, key=key, context=context, as_of=at,
                            authority=authority)


def test_append_is_uncommitted_until_external_stream_and_anchor_agree() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    result = propose(authority, events, command())
    assert result.status == "PROPOSED_UNCOMMITTED"
    assert result.production_permitted is False
    assert result.proposal is not None
    assert read(authority, events).rules == ()
    events.append(result.proposal)
    assert read(authority, events).status == "UNVERIFIED"
    authority.anchor(KEY, events)
    qualified = read(authority, events)
    assert qualified.status == "VERIFIED_OFFLINE"
    assert qualified.rules[0].rule_body == "WATCH:price>10"
    assert qualified.production_permitted is False


def test_owner_enumeration_and_unauthenticated_reads_fail_closed() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    commit(authority, events, command())
    authority.principal = Principal("mallory")
    denied = read(authority, events)
    assert denied.code == "ACCESS_DENIED" and denied.rules == ()
    assert denied.production_permitted is False
    authority.principal = Principal("alice")
    assert read(authority, events, context="spoofed").code == "ACCESS_DENIED"
    assert propose(authority, events, command(action="DELETE", version=2,
                                               input_version=2, body="",
                                               event_id="event-2"),
                   key=SecurityKey("A", "000001", "mallory")).code == "ACCESS_DENIED"


def test_same_code_cross_market_and_forged_anchor_rejected() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    commit(authority, events, command())
    authority.grants.add((OTHER_MARKET, "read"))
    authority.heads[OTHER_MARKET] = StreamHead(OTHER_MARKET, 1, event_digest(events[0]))
    assert read(authority, events, key=OTHER_MARKET).code == "EVENT_INVALID"
    assert read(authority, [replace(events[0], rule_body="WATCH:fake")]).code == "STREAM_ANCHOR_INVALID"
    authority.heads[KEY] = StreamHead(KEY, 1, "f" * 64)
    assert read(authority, events).code == "STREAM_ANCHOR_INVALID"


def test_forged_event_version_identity_and_replay_fail_closed() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    original = commit(authority, events, command())
    for mutation in (replace(original, rule_version=2),
                     replace(original, principal_id="mallory"),
                     replace(original, input_version=0),
                     replace(original, previous_hash="f" * 64)):
        authority.anchor(KEY, [mutation])
        assert read(authority, [mutation]).status == "UNVERIFIED"
    authority.anchor(KEY, events)
    assert propose(authority, events, command(action="UPDATE", version=1,
                                               input_version=2, event_id="event-2")).code == "VERSION_REPLAY"
    assert propose(authority, events, command(action="UPDATE", version=2,
                                               input_version=1, event_id="event-2")).code == "VERSION_REPLAY"
    assert propose(authority, events, command(action="UPDATE", version=2,
                                               input_version=2, event_id="event-1")).code == "EVENT_INVALID"
    assert read(authority, [original, original]).status == "UNVERIFIED"


def test_revoke_and_delete_tombstones_prevent_resurrection() -> None:
    for action in ("REVOKE", "DELETE"):
        authority = OutsideAuthority()
        events: list[RuleEvent] = []
        commit(authority, events, command())
        commit(authority, events, command(action, version=2, input_version=2,
                                          body="", event_id="event-2", start=NOW))
        assert read(authority, events).rules == ()
        resurrect = propose(authority, events, command("CREATE", version=1,
                                                        input_version=3, event_id="event-3"))
        assert resurrect.code == "TOMBSTONE_REPLAY"
        old_snapshot = read(authority, events[:1])
        assert old_snapshot.code == "STREAM_ANCHOR_INVALID" and old_snapshot.rules == ()
        authority.anchor(KEY, events[:1])
        assert read(authority, events).code == "STREAM_ANCHOR_INVALID"


def test_cost_rules_require_independent_revision_bound_user_consent() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    requested = command(body="COST:commission=0.001", consent="consent-1")
    assert propose(authority, events, requested).code == "CONSENT_INVALID"
    body_sha = hashlib.sha256(requested.rule_body.encode()).hexdigest()
    proof = ConsentProof("consent-1", KEY, "alice", "rule-1", 1, body_sha,
                         NOW - timedelta(hours=2), NOW + timedelta(hours=2), False)
    authority.consents[proof.consent_id] = replace(proof, rule_version=2)
    assert propose(authority, events, requested).code == "CONSENT_INVALID"
    authority.consents[proof.consent_id] = proof
    commit(authority, events, requested)
    assert len(read(authority, events).rules) == 1
    authority.consents[proof.consent_id] = replace(proof, revoked=True)
    assert read(authority, events).code == "CONSENT_INVALID"
    assert read(authority, events).production_permitted is False
    assert propose(authority, events, command("REVOKE", version=2,
                                               input_version=2, body="",
                                               event_id="event-2", start=NOW)).status == "PROPOSED_UNCOMMITTED"
    commit(authority, events, command("REVOKE", version=2,
                                      input_version=2, body="", event_id="event-2",
                                      start=NOW))
    assert read(authority, events).status == "VERIFIED_OFFLINE"
    assert read(authority, events).rules == ()


def test_revoked_cost_consent_rejects_fresh_proposals_but_allows_tombstone() -> None:
    for tombstone_action in ("REVOKE", "DELETE"):
        authority = OutsideAuthority()
        events: list[RuleEvent] = []
        requested = command(body="COST:commission=0.001", consent="consent-1")
        body_sha = hashlib.sha256(requested.rule_body.encode()).hexdigest()
        proof = ConsentProof("consent-1", KEY, "alice", "rule-1", 1, body_sha,
                             NOW - timedelta(hours=2), NOW + timedelta(hours=2), True)
        authority.consents[proof.consent_id] = proof
        assert propose(authority, events, requested).code == "CONSENT_INVALID"
        authority.consents[proof.consent_id] = replace(proof, revoked=False)
        commit(authority, events, requested)
        authority.consents[proof.consent_id] = proof
        assert read(authority, events).code == "CONSENT_INVALID"
        # Committed history is replayed despite later consent withdrawal; it
        # cannot be read or updated as a cost rule, but can be tombstoned.
        authority.consents["consent-2"] = replace(
            proof, consent_id="consent-2", rule_version=2)
        updated = command("UPDATE", version=2, input_version=2,
                          event_id="event-update", consent="consent-2",
                          body=requested.rule_body)
        assert propose(authority, events, updated).code == "CONSENT_INVALID"
        commit(authority, events, command(tombstone_action, version=2,
                                          input_version=2, body="",
                                          event_id="event-2", start=NOW))
        result = read(authority, events)
        assert result.status == "VERIFIED_OFFLINE" and result.rules == ()
        assert result.production_permitted is False


def test_effective_window_is_half_open_and_utc_only() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    start = NOW
    end = NOW + timedelta(hours=1)
    commit(authority, events, command(start=start, end=end), at=start)
    assert len(read(authority, events, at=start).rules) == 1
    assert len(read(authority, events, at=end - timedelta(microseconds=1)).rules) == 1
    assert read(authority, events, at=end).rules == ()
    assert read(authority, events, at=NOW.replace(tzinfo=None)).code == "TIME_INVALID"
    assert propose(authority, events, command("UPDATE", version=2, input_version=2,
                                               event_id="event-2", start=end,
                                               end=end)).status == "UNVERIFIED"


def test_tombstones_reject_scheduling_and_backdating_then_never_revive() -> None:
    for action in ("REVOKE", "DELETE"):
        authority = OutsideAuthority()
        events: list[RuleEvent] = []
        commit(authority, events, command())
        future = command(action, version=2, input_version=2,
                         body="", event_id="event-2",
                         start=NOW + timedelta(minutes=30),
                         end=NOW + timedelta(hours=2))
        assert propose(authority, events, future).code == "TOMBSTONE_INVALID"
        backdated = command(action, version=2, input_version=2,
                            body="", event_id="event-2",
                            start=NOW - timedelta(microseconds=1))
        assert propose(authority, events, backdated).code == "TOMBSTONE_INVALID"
        assert len(read(authority, events).rules) == 1
        for rejected in (future, backdated):
            forged = replace(events[0], event_id="event-2", sequence=2,
                             previous_hash=event_digest(events[0]),
                             action=action, rule_version=2, input_version=2,
                             rule_body="", valid_from=rejected.valid_from,
                             valid_until=rejected.valid_until)
            authority.anchor(KEY, [events[0], forged])
            assert read(authority, [events[0], forged]).code == "TOMBSTONE_INVALID"
        authority.anchor(KEY, events)
        commit(authority, events, command(action, version=2, input_version=2,
                                          body="", event_id="event-2", start=NOW))
        assert read(authority, events).rules == ()
        assert propose(authority, events, command("CREATE", input_version=3,
                                                   event_id="event-3")).code == "TOMBSTONE_REPLAY"


def test_cost_consent_covers_future_start_but_can_expire_before_rule_end() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    start = NOW + timedelta(hours=3)
    end = NOW + timedelta(hours=4)
    requested = command(body="COST:commission=0.001", consent="consent-1",
                        start=start, end=end)
    body_sha = hashlib.sha256(requested.rule_body.encode()).hexdigest()
    partial = ConsentProof("consent-1", KEY, "alice", "rule-1", 1, body_sha,
                           NOW - timedelta(minutes=1),
                           NOW + timedelta(minutes=5), False)
    authority.consents[partial.consent_id] = partial
    assert propose(authority, events, requested).code == "CONSENT_INVALID"
    authority.consents[partial.consent_id] = replace(partial, valid_until=start)
    assert propose(authority, events, requested).code == "CONSENT_INVALID"
    authority.consents[partial.consent_id] = replace(
        partial, valid_until=start + timedelta(minutes=5))
    assert propose(authority, events, requested).status == "PROPOSED_UNCOMMITTED"
    commit(authority, events, requested)
    assert len(read(authority, events, at=start).rules) == 1
    assert read(authority, events, at=start + timedelta(minutes=5)).code == "CONSENT_INVALID"


def test_authority_outage_and_malformed_inputs_fail_closed() -> None:
    authority = OutsideAuthority()
    events: list[RuleEvent] = []
    assert read(authority, None).production_permitted is False  # type: ignore[arg-type]
    assert read(authority, [object()]).production_permitted is False  # type: ignore[list-item]
    assert read(authority, events, key=SecurityKey("A", "../x", "alice")).status == "UNVERIFIED"
    authority.broken = True
    assert read(authority, events).code == "AUTHORITY_UNAVAILABLE"
    assert propose(authority, events, command()).code == "AUTHORITY_UNAVAILABLE"
