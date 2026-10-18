"""Pure offline WATCH1 source qualification; never a production authorization.

The injected authority is an independently controlled, read-only trust root, NOT a
mapping copied from the alert. No adapter for a live source, Portal or delivery is
provided. In particular, a snapshot's canonical JSON digest is not original quote
provenance. Failure of any lookup, parser or external policy fails closed.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Protocol, Sequence

from quant_backtest.watch_alert_contract import validate_alert


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_LINE = re.compile(r'^(?:var\s+)?hq_str_([A-Za-z0-9_]+)="([^"\r\n]*)";?\s*$')
_NATIVE_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
_NATIVE_TIME = re.compile(r"\d{2}:\d{2}:\d{2}\Z")
_MARKET_ZONE = {"A": timezone(timedelta(hours=8), "Asia/Shanghai")}


@dataclass(frozen=True)
class StrategyMembershipProof:
    """Authority-issued approval for exactly one A-pool strategy membership."""

    market: str
    code: str
    strategy: str
    strategy_version: str
    source_batch: str
    membership_event_id: str
    approved: bool
    revoked: bool
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True)
class OwnerInputProof:
    """Server-issued authorization for a specific persisted B-pool input version."""

    market: str
    code: str
    owner: str
    watch_rule: str
    watch_rule_version: str
    membership_event_id: str
    input_version: int
    persisted_input_id: str
    authorized_principal: str
    revoked: bool
    valid_from: datetime
    valid_until: datetime


@dataclass(frozen=True)
class RawHttpResponseProof:
    """Trusted original HTTP content bytes and their independently retained anchor."""

    source_ref: str
    provider_id: str
    content: bytes
    content_sha256: str
    content_length: int
    encoding: str
    hq_key: str
    price_field: int
    native_date_field: int = 30
    native_time_field: int = 31
    received_at: datetime | None = None


@dataclass(frozen=True)
class TradingSessionProof:
    """Independent exchange-calendar session and retained policy version."""

    market: str
    session_date: date
    opens_at: datetime
    closes_at: datetime
    calendar_id: str
    calendar_version: str


@dataclass(frozen=True)
class ProviderLicenseProof:
    """Independent provider grant for the exact market and as-of time."""

    provider_id: str
    market: str
    license_id: str
    valid_from: datetime
    valid_until: datetime
    revoked: bool


class WatchSourceAuthority(Protocol):
    """Independently controlled, read-only resolver; never backed by alert claims."""

    def strategy_roster(self, market: str, code: str, at: datetime) -> Sequence[tuple[str, str, str]] | None:
        """Return the complete approved (strategy, version, batch) set at time."""

    def strategy_membership(self, event_id: str) -> StrategyMembershipProof | None:
        """Resolve an independently issued A membership receipt."""

    def owner_input(self, event_id: str) -> OwnerInputProof | None:
        """Resolve an independently issued B owner/input receipt."""

    def authorized_input_floor(self, principal: str, persisted_input_id: str) -> int | None:
        """Return server-held minimum acceptable persisted input version."""

    def raw_response(self, source_ref: str) -> RawHttpResponseProof | None:
        """Resolve captured HTTP bytes and independently retained hash/length."""

    def raw_anchor(self, source_ref: str) -> tuple[str, int] | None:
        """Return separately retained immutable original-byte hash and byte length."""

    def trading_session(self, market: str, session_date: date) -> TradingSessionProof | None:
        """Resolve an independently approved trading calendar."""

    def provider_license(self, provider_id: str, market: str) -> ProviderLicenseProof | None:
        """Resolve an independently approved provider license."""


@dataclass(frozen=True)
class SourceQualification:
    """Non-actionable offline decision; production_permitted is always False."""

    status: str
    code: str
    production_permitted: bool = False


class _SourceFailure(ValueError):
    """A rejected or absent trust-chain prerequisite."""


def _require(value: bool, reason: str) -> None:
    if not value:
        raise _SourceFailure(reason)


def _utc(value: datetime | None) -> bool:
    return (type(value) is datetime and value.tzinfo is not None
            and value.utcoffset() is not None
            and value.utcoffset().total_seconds() == 0)


def _claimed_time(value: Any) -> datetime:
    _require(type(value) is str and (value.endswith("Z") or value.endswith("+00:00")), "TIME")
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _SourceFailure("TIME") from exc
    _require(_utc(instant), "TIME")
    return instant


def _time_window(start: datetime, end: datetime, at: datetime) -> bool:
    return _utc(start) and _utc(end) and start <= at <= end


def _check_member(event: Mapping[str, Any], authority: WatchSourceAuthority,
                  quote_at: datetime) -> None:
    membership = event["membership_event"]
    event_id = membership["event_id"]
    if event["pool"] == "A":
        proof = authority.strategy_membership(event_id)
        _require(type(proof) is StrategyMembershipProof, "A_RECEIPT_MISSING")
        _require(proof.approved is True and proof.revoked is False
                 and (proof.market, proof.code, proof.strategy, proof.strategy_version,
                      proof.source_batch, proof.membership_event_id) ==
                 (event["market"], event["code"], event["strategy"],
                  event["strategy_version"], event["source_batch"], event_id)
                 and _time_window(proof.valid_from, proof.valid_until, quote_at)
                 and proof.valid_from <= _claimed_time(membership["as_of"]),
                 "A_RECEIPT_INVALID")
    else:
        proof = authority.owner_input(event_id)
        _require(type(proof) is OwnerInputProof, "B_AUTH_MISSING")
        _require((proof.market, proof.code, proof.owner, proof.watch_rule,
                  proof.watch_rule_version, proof.membership_event_id) ==
                 (event["market"], event["code"], event["owner"], event["watch_rule"],
                  event["watch_rule_version"], event_id)
                 and proof.revoked is False and type(proof.input_version) is int
                 and proof.input_version > 0 and bool(proof.persisted_input_id)
                 and bool(proof.authorized_principal)
                 and _time_window(proof.valid_from, proof.valid_until, quote_at)
                 and proof.valid_from <= _claimed_time(membership["as_of"]),
                 "B_AUTH_INVALID")
        floor = authority.authorized_input_floor(proof.authorized_principal,
                                                  proof.persisted_input_id)
        _require(type(floor) is int and floor > 0 and proof.input_version >= floor,
                 "B_INPUT_ROLLBACK")


def _check_raw(event: Mapping[str, Any], authority: WatchSourceAuthority,
               quote_at: datetime, evaluation_as_of: datetime) -> str:
    raw = authority.raw_response(event["source_ref"])
    _require(type(raw) is RawHttpResponseProof and raw.source_ref == event["source_ref"],
             "RAW_MISSING")
    _require(type(raw.content) is bytes and len(raw.content) > 0
             and type(raw.content_length) is int and len(raw.content) == raw.content_length
             and type(raw.content_sha256) is str
             and _SHA256.fullmatch(raw.content_sha256) is not None
             and hashlib.sha256(raw.content).hexdigest() == raw.content_sha256,
             "RAW_INTEGRITY")
    anchor = authority.raw_anchor(event["source_ref"])
    _require(type(anchor) is tuple and len(anchor) == 2
             and (raw.content_sha256, raw.content_length) == anchor, "RAW_ANCHOR")
    _require(_utc(raw.received_at) and quote_at <= raw.received_at <= evaluation_as_of,
             "RAW_RECEIPT_TIME")
    _require(raw.encoding in ("utf-8", "gbk"), "RAW_ENCODING")
    try:
        decoded = raw.content.decode(raw.encoding, errors="strict")
    except (UnicodeError, LookupError) as exc:
        raise _SourceFailure("RAW_DECODE") from exc
    lines: dict[str, list[str]] = {}
    for line in decoded.splitlines():
        _require(bool(line.strip()), "RAW_LINE")
        match = _LINE.fullmatch(line)
        _require(match is not None, "RAW_LINE")
        key = match.group(1)
        _require(key not in lines, "DUPLICATE_HQ_KEY")
        lines[key] = match.group(2).split(",")
    _require(len(lines) > 0 and type(raw.hq_key) is str and raw.hq_key in lines,
             "HQ_KEY_MISSING")
    _require(event["market"] in _MARKET_ZONE, "NATIVE_MARKET_UNSUPPORTED")
    # The mapping of market/code to hq key must come from a trusted response proof.
    _require(type(event["code"]) is str and re.fullmatch(r"\d{6}", event["code"])
             is not None and raw.hq_key ==
             (("sh" if event["code"].startswith("6") else "sz") + event["code"])
             and type(raw.provider_id) is str and bool(raw.provider_id), "HQ_IDENTITY")
    fields = lines[raw.hq_key]
    _require(all(type(index) is int and index >= 0 for index in
                 (raw.price_field, raw.native_date_field, raw.native_time_field))
             and (raw.native_date_field, raw.native_time_field) == (30, 31)
             and len(fields) > max(raw.price_field, 30, 31), "RAW_FIELDS")
    _require(_NATIVE_DATE.fullmatch(fields[30]) is not None
             and _NATIVE_TIME.fullmatch(fields[31]) is not None, "NATIVE_TIME_MISSING")
    try:
        native = datetime.strptime(fields[30] + " " + fields[31], "%Y-%m-%d %H:%M:%S")
        localized = native.replace(tzinfo=_MARKET_ZONE[event["market"]])
        observed = Decimal(event["observed"])
        parsed = Decimal(fields[raw.price_field])
    except (ValueError, InvalidOperation, OverflowError) as exc:
        raise _SourceFailure("RAW_VALUE") from exc
    _require(parsed.is_finite() and observed == parsed, "RAW_PRICE_MISMATCH")
    _require(localized.astimezone(timezone.utc) == quote_at
             and quote_at <= raw.received_at, "NATIVE_TIME_MISMATCH")
    return raw.provider_id


def _check_policies(event: Mapping[str, Any], authority: WatchSourceAuthority,
                    provider_id: str, quote_at: datetime) -> None:
    session_date = quote_at.astimezone(_MARKET_ZONE[event["market"]]).date()
    session = authority.trading_session(event["market"], session_date)
    _require(type(session) is TradingSessionProof and session.market == event["market"]
             and type(session.session_date) is date
             and session.session_date == quote_at.astimezone(
                 _MARKET_ZONE[event["market"]]).date()
             and type(session.calendar_id) is str and bool(session.calendar_id)
             and type(session.calendar_version) is str and bool(session.calendar_version)
             and _time_window(session.opens_at, session.closes_at, quote_at),
             "CALENDAR_MISSING")
    license_proof = authority.provider_license(provider_id, event["market"])
    _require(type(license_proof) is ProviderLicenseProof
             and license_proof.provider_id == provider_id
             and license_proof.market == event["market"]
             and type(license_proof.license_id) is str and bool(license_proof.license_id)
             and license_proof.revoked is False
             and _time_window(license_proof.valid_from, license_proof.valid_until, quote_at),
             "PROVIDER_LICENSE_MISSING")


def verify_watch_sources(events: Sequence[Mapping[str, Any]], *,
                         evaluation_as_of: datetime,
                         authority: WatchSourceAuthority) -> SourceQualification:
    """Verify all supplied alerts, including every approved strategy of each A ticker.

    A successful offline qualification is not permission to publish, notify or trade.
    Independent authority storage, capture authenticity, calendar and licensing must
    be controlled by a separate operator; client-supplied authority DTOs are untrusted.
    """
    try:
        _require(_utc(evaluation_as_of) and authority is not None, "EVALUATION_TIME")
        _require(isinstance(events, (list, tuple)) and bool(events), "EMPTY_EVENTS")
        present: dict[tuple[str, str], set[tuple[str, str, str]]] = {}
        for claimed in events:
            alert = validate_alert(claimed, evaluation_as_of=evaluation_as_of)
            _require(alert.event is not None and alert.code == "OK", "ALERT_INVALID")
            event = alert.event
            quote_at = _claimed_time(event["quote_as_of"])
            _check_member(event, authority, quote_at)
            provider_id = _check_raw(event, authority, quote_at, evaluation_as_of)
            _check_policies(event, authority, provider_id, quote_at)
            if event["pool"] == "A":
                ticker = (event["market"], event["code"])
                strategy = (event["strategy"], event["strategy_version"],
                            event["source_batch"])
                strategies = present.setdefault(ticker, set())
                _require(strategy not in strategies, "DUPLICATE_STRATEGY")
                strategies.add(strategy)
        for (market, code), strategies in present.items():
            roster = authority.strategy_roster(market, code, evaluation_as_of)
            _require(isinstance(roster, (list, tuple)) and bool(roster)
                     and all(type(item) is tuple and len(item) == 3
                             and all(type(part) is str and bool(part) for part in item)
                             for item in roster)
                     and len(roster) == len(set(roster)) and set(roster) == strategies,
                     "A_ROSTER_MISMATCH")
        return SourceQualification("QUALIFIED_OFFLINE", "OK")
    except (_SourceFailure, TypeError, ValueError, KeyError, IndexError, AttributeError,
            OverflowError, UnicodeError) as exc:
        reason = str(exc) if isinstance(exc, _SourceFailure) else "AUTHORITY_UNAVAILABLE"
        return SourceQualification("SOURCE_UNVERIFIED", reason)
