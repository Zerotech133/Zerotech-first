"""Offline B-pool rule ledger reader and uncommitted append proposal.

The event stream, its head, authentication, authorization, rule classification and
consent are independently injected. Neither this module nor its caller's event list
is a credential, persistent store, delivery authorization or production gateway.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence


_GENESIS = "0" * 64
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MARKET = re.compile(r"[A-Z][A-Z0-9_]{0,15}\Z")
_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,31}\Z")
_ACTIONS = frozenset(("CREATE", "UPDATE", "REVOKE", "DELETE"))


@dataclass(frozen=True)
class SecurityKey:
    """Exact market/code/owner tenant partition, never a ticker-only lookup."""

    market: str
    code: str
    owner: str


@dataclass(frozen=True)
class Principal:
    """Identity returned by an independent authentication authority."""

    principal_id: str


@dataclass(frozen=True)
class StreamHead:
    """Independently retained committed event-count and terminal hash."""

    key: SecurityKey
    count: int
    digest: str


@dataclass(frozen=True)
class ConsentProof:
    """Independent, revocable consent for one cost-affecting rule revision."""

    consent_id: str
    key: SecurityKey
    principal_id: str
    rule_id: str
    rule_version: int
    rule_sha256: str
    valid_from: datetime
    valid_until: datetime
    revoked: bool


@dataclass(frozen=True)
class RuleEvent:
    """Immutable-envelope fields of one externally persisted append-only event.

    rule_body is opaque retained content; the independent classifier must identify
    cost-affecting semantics. Tombstones take effect immediately at issued_at,
    are never scheduled/backdated, and carry empty rule_body and no consent_id.
    """

    key: SecurityKey
    event_id: str
    sequence: int
    previous_hash: str
    action: str
    rule_id: str
    rule_version: int
    input_version: int
    principal_id: str
    issued_at: datetime
    valid_from: datetime
    valid_until: datetime
    rule_body: str
    consent_id: str


@dataclass(frozen=True)
class RuleCommand:
    """Request to prepare an append; never itself an authorized ledger event."""

    action: str
    rule_id: str
    rule_version: int
    input_version: int
    rule_body: str
    consent_id: str
    valid_from: datetime
    valid_until: datetime
    event_id: str


@dataclass(frozen=True)
class RuleView:
    """Authorized, currently effective offline rule projection."""

    rule_id: str
    rule_version: int
    input_version: int
    rule_body: str
    classification: str
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True)
class RuleVerdict:
    """Failure-closed result; never grants production permission."""

    status: str
    code: str
    rules: tuple[RuleView, ...] = ()
    proposal: RuleEvent | None = None
    production_permitted: bool = False


class RuleAuthority(Protocol):
    """External trust ports; event stream and context must not implement these."""

    def authenticate(self, context: object) -> Principal | None:
        """Authenticate context outside the local event stream."""

    def authorize(self, principal: Principal, key: SecurityKey, operation: str) -> bool:
        """Authorize the exact tenant and security for read/write."""

    def stream_head(self, key: SecurityKey) -> StreamHead | None:
        """Return independently retained committed stream anchor."""

    def classify_rule(self, body: str) -> str | None:
        """Return STANDARD or COST from independent, maintained rule policy."""

    def cost_consent(self, consent_id: str) -> ConsentProof | None:
        """Resolve independent user agreement; not an event field alone."""


class _Rejected(ValueError):
    """Locally invalid data or failed independent authority verification."""


def _ensure(condition: bool, reason: str) -> None:
    if not condition:
        raise _Rejected(reason)


def _label(value: object, pattern: re.Pattern[str] = _LABEL) -> bool:
    return type(value) is str and pattern.fullmatch(value) is not None


def _instant(value: object) -> bool:
    return (
        type(value) is datetime
        and value.tzinfo is not None
        and value.utcoffset() is not None
        and value.utcoffset().total_seconds() == 0
    )


def _window(start: datetime, end: datetime, as_of: datetime) -> bool:
    return _instant(start) and _instant(end) and start <= as_of < end


def _trusted(authority: RuleAuthority, method: str, *args: object) -> Any:
    try:
        return getattr(authority, method)(*args)
    except Exception as exc:
        raise _Rejected("AUTHORITY_UNAVAILABLE") from exc


def _identity(key: SecurityKey) -> None:
    _ensure(
        type(key) is SecurityKey
        and _label(key.market, _MARKET)
        and _label(key.code, _CODE)
        and _label(key.owner),
        "IDENTITY_INVALID",
    )


def _access(
    authority: RuleAuthority, context: object, key: SecurityKey, operation: str
) -> Principal:
    _identity(key)
    principal = _trusted(authority, "authenticate", context)
    _ensure(
        type(principal) is Principal
        and _label(principal.principal_id)
        and principal.principal_id == key.owner,
        "ACCESS_DENIED",
    )
    _ensure(
        _trusted(authority, "authorize", principal, key, operation) is True,
        "ACCESS_DENIED",
    )
    return principal


def event_digest(event: RuleEvent) -> str:
    """Canonical event hash; not an independent signature or trust root."""
    if type(event) is not RuleEvent:
        raise ValueError("EVENT_INVALID")
    data = {
        "key": {
            "market": event.key.market,
            "code": event.key.code,
            "owner": event.key.owner,
        },
        "event_id": event.event_id,
        "sequence": event.sequence,
        "previous_hash": event.previous_hash,
        "action": event.action,
        "rule_id": event.rule_id,
        "rule_version": event.rule_version,
        "input_version": event.input_version,
        "principal_id": event.principal_id,
        "issued_at": event.issued_at.isoformat(),
        "valid_from": event.valid_from.isoformat(),
        "valid_until": event.valid_until.isoformat(),
        "rule_body": event.rule_body,
        "consent_id": event.consent_id,
    }
    return hashlib.sha256(
        json.dumps(
            data, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()


def _consent(
    authority: RuleAuthority,
    event: RuleEvent,
    as_of: datetime,
    *,
    require_active: bool = True,
) -> None:
    _ensure(_label(event.consent_id), "CONSENT_REQUIRED")
    proof = _trusted(authority, "cost_consent", event.consent_id)
    digest = hashlib.sha256(event.rule_body.encode("utf-8")).hexdigest()
    _ensure(
        type(proof) is ConsentProof
        and proof.consent_id == event.consent_id
        and proof.key == event.key
        and proof.principal_id == event.principal_id
        and proof.rule_id == event.rule_id
        and type(proof.rule_version) is int
        and proof.rule_version == event.rule_version
        and proof.rule_sha256 == digest
        and (not require_active or proof.revoked is False)
        and _window(proof.valid_from, proof.valid_until, event.issued_at)
        and proof.valid_until > event.valid_from
        and (not require_active or _window(proof.valid_from, proof.valid_until, as_of)),
        "CONSENT_INVALID",
    )


def _validate_event(
    event: RuleEvent,
    key: SecurityKey,
    principal: Principal,
    expected_seq: int,
    previous: str,
    last_time: datetime | None,
    as_of: datetime,
    states: dict[str, RuleEvent],
    tombstones: set[str],
    authority: RuleAuthority,
    seen_ids: set[str],
    last_input_version: int,
) -> None:
    _ensure(
        type(event) is RuleEvent
        and type(event.key) is SecurityKey
        and event.key == key
        and _label(event.event_id)
        and event.event_id not in seen_ids
        and type(event.sequence) is int
        and event.sequence == expected_seq
        and event.previous_hash == previous
        and event.action in _ACTIONS
        and _label(event.rule_id)
        and type(event.rule_version) is int
        and event.rule_version > 0
        and type(event.input_version) is int
        and event.input_version > last_input_version
        and event.principal_id == principal.principal_id
        and _instant(event.issued_at)
        and event.issued_at <= as_of
        and (last_time is None or event.issued_at >= last_time)
        and _instant(event.valid_from)
        and _instant(event.valid_until)
        and event.valid_from < event.valid_until,
        "EVENT_INVALID",
    )
    existing = states.get(event.rule_id)
    _ensure(event.rule_id not in tombstones, "TOMBSTONE_REPLAY")
    if event.action == "CREATE":
        _ensure(existing is None and event.rule_version == 1, "VERSION_REPLAY")
    else:
        _ensure(
            existing is not None and event.rule_version == existing.rule_version + 1,
            "VERSION_REPLAY",
        )
    if event.action in ("CREATE", "UPDATE"):
        _ensure(
            type(event.rule_body) is str
            and bool(event.rule_body.strip())
            and len(event.rule_body.encode("utf-8")) <= 65536,
            "RULE_BODY_INVALID",
        )
        classification = _trusted(authority, "classify_rule", event.rule_body)
        _ensure(classification in ("STANDARD", "COST"), "RULE_CLASSIFICATION_UNKNOWN")
        if classification == "COST":
            # A subsequently withdrawn agreement blocks reads of the live rule,
            # but must not prevent replaying a committed revoke/delete tombstone.
            _consent(authority, event, event.issued_at, require_active=False)
        else:
            _ensure(event.consent_id == "", "CONSENT_UNEXPECTED")
        states[event.rule_id] = event
    else:
        # Tombstones take effect exactly at issuance. Reject both backdated and
        # future effective starts so the persisted event has no timing ambiguity.
        _ensure(
            event.rule_body == ""
            and event.consent_id == ""
            and event.valid_from == event.issued_at
            and _window(event.valid_from, event.valid_until, event.issued_at),
            "TOMBSTONE_INVALID",
        )
        tombstones.add(event.rule_id)
        states.pop(event.rule_id)
    seen_ids.add(event.event_id)


def _replay(
    events: Sequence[RuleEvent],
    key: SecurityKey,
    principal: Principal,
    as_of: datetime,
    authority: RuleAuthority,
) -> tuple[dict[str, RuleEvent], str, int]:
    _ensure(type(events) in (tuple, list), "EVENT_STREAM_INVALID")
    head = _trusted(authority, "stream_head", key)
    _ensure(
        type(head) is StreamHead
        and head.key == key
        and type(head.count) is int
        and head.count >= 0
        and _label(head.digest, _SHA)
        and head.count == len(events),
        "STREAM_ANCHOR_INVALID",
    )
    previous = _GENESIS
    states: dict[str, RuleEvent] = {}
    tombstones: set[str] = set()
    seen_ids: set[str] = set()
    last_time: datetime | None = None
    last_input_version = 0
    for index, event in enumerate(events, 1):
        _validate_event(
            event,
            key,
            principal,
            index,
            previous,
            last_time,
            as_of,
            states,
            tombstones,
            authority,
            seen_ids,
            last_input_version,
        )
        previous = event_digest(event)
        last_time = event.issued_at
        last_input_version = event.input_version
    _ensure(previous == head.digest, "STREAM_ANCHOR_INVALID")
    return states, previous, last_input_version


def read_owner_rules(
    events: Sequence[RuleEvent],
    *,
    key: SecurityKey,
    context: object,
    as_of: datetime,
    authority: RuleAuthority,
) -> RuleVerdict:
    """Read only authenticated owner's current rules after full anchored replay."""
    try:
        _ensure(_instant(as_of), "TIME_INVALID")
        principal = _access(authority, context, key, "read")
        states, _, _ = _replay(events, key, principal, as_of, authority)
        views: list[RuleView] = []
        for event in states.values():
            if _window(event.valid_from, event.valid_until, as_of):
                classification = _trusted(authority, "classify_rule", event.rule_body)
                _ensure(
                    classification in ("STANDARD", "COST"),
                    "RULE_CLASSIFICATION_UNKNOWN",
                )
                if classification == "COST":
                    _consent(authority, event, as_of)
                else:
                    _ensure(event.consent_id == "", "CONSENT_UNEXPECTED")
                views.append(
                    RuleView(
                        event.rule_id,
                        event.rule_version,
                        event.input_version,
                        event.rule_body,
                        classification,
                        event.valid_from,
                        event.valid_until,
                    )
                )
        return RuleVerdict(
            "VERIFIED_OFFLINE", "OK", tuple(sorted(views, key=lambda v: v.rule_id))
        )
    except (
        _Rejected,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        UnicodeError,
    ) as exc:
        return RuleVerdict(
            "UNVERIFIED", str(exc) if isinstance(exc, _Rejected) else "INPUT_INVALID"
        )


def propose_owner_rule(
    events: Sequence[RuleEvent],
    command: RuleCommand,
    *,
    key: SecurityKey,
    context: object,
    as_of: datetime,
    authority: RuleAuthority,
) -> RuleVerdict:
    """Prepare an uncommitted event; external writer must persist and anchor it.

    The returned proposal has no effect in read_owner_rules until the independent
    persisted stream AND independent stream_head both include its exact bytes.
    """
    try:
        _ensure(_instant(as_of) and type(command) is RuleCommand, "INPUT_INVALID")
        principal = _access(authority, context, key, "write")
        states, previous, last_input_version = _replay(
            events, key, principal, as_of, authority
        )
        _ensure(
            type(command.input_version) is int
            and command.input_version > last_input_version,
            "VERSION_REPLAY",
        )
        event = RuleEvent(
            key,
            command.event_id,
            len(events) + 1,
            previous,
            command.action,
            command.rule_id,
            command.rule_version,
            command.input_version,
            principal.principal_id,
            as_of,
            command.valid_from,
            command.valid_until,
            command.rule_body,
            command.consent_id,
        )
        tombstones = {
            old.rule_id for old in events if old.action in ("DELETE", "REVOKE")
        }
        _validate_event(
            event,
            key,
            principal,
            len(events) + 1,
            previous,
            events[-1].issued_at if events else None,
            as_of,
            states,
            tombstones,
            authority,
            {old.event_id for old in events},
            last_input_version,
        )
        if event.action in ("CREATE", "UPDATE"):
            # Historical replay permits subsequently withdrawn consent solely to
            # reach tombstones; a fresh proposal must have live consent right now.
            classification = _trusted(authority, "classify_rule", event.rule_body)
            _ensure(
                classification in ("STANDARD", "COST"), "RULE_CLASSIFICATION_UNKNOWN"
            )
            if classification == "COST":
                _consent(authority, event, as_of)
            else:
                # A changing/malicious classifier cannot downgrade a previously
                # validated COST revision carrying an independent consent ID.
                _ensure(event.consent_id == "", "RULE_CLASSIFICATION_CHANGED")
        return RuleVerdict("PROPOSED_UNCOMMITTED", "OK", proposal=event)
    except (
        _Rejected,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,
        UnicodeError,
    ) as exc:
        return RuleVerdict(
            "UNVERIFIED", str(exc) if isinstance(exc, _Rejected) else "INPUT_INVALID"
        )
