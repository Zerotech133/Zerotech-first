"""Offline WATCH1 per-instrument alert contract (v1).

This module checks only structural self-consistency; a successful validation DOES NOT
confer production eligibility or independently verify source authenticity. It does not
authenticate producer signatures, fetch quotes, notify anyone, or place orders. A trusted
caller must independently verify batch manifests and membership receipts, raw quote
provenance and original raw-byte hash, quality, clock and notification receipts before
acting. The source_hash below hashes canonical JSON of the supplied snapshot only: it
is NOT the external original raw-byte hash. source_ref is an opaque logical identifier,
not a filesystem location; this module performs no filesystem or network I/O. A display
summary is never per-instrument evidence.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


FIELDS = frozenset(
    {
        "schema_version",
        "alert_id",
        "trace_id",
        "dedupe_key",
        "pool",
        "run_mode",
        "market",
        "code",
        "instrument_type",
        "strategy",
        "strategy_version",
        "source_batch",
        "owner",
        "watch_rule",
        "watch_rule_version",
        "membership_event",
        "rule",
        "comparator",
        "threshold",
        "observed",
        "quote_as_of",
        "visible_at",
        "source_hash",
        "source_ref",
        "source_snapshot",
        "source_kind",
        "quality",
        "facts",
        "hypotheses",
        "supporting_refs",
        "contradicting_refs",
        "evidence_assessment",
        "notification",
        "sim_handoff",
    }
)
HEX = re.compile(r"[0-9a-f]{64}\Z")
TEXT = re.compile(r"[^\s].*\Z", re.DOTALL)
DECIMAL = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
SOURCE_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9_:-]{0,127}\Z")
ACTIVE_KINDS = frozenset({"ENTER", "REENTER", "HIT", "HOLD"})
COMPARATORS = {
    "GT": lambda left, right: left > right,
    "GTE": lambda left, right: left >= right,
    "LT": lambda left, right: left < right,
    "LTE": lambda left, right: left <= right,
    "EQ": lambda left, right: left == right,
    "NE": lambda left, right: left != right,
}


@dataclass(frozen=True)
class AlertValidation:
    """Decision and precise rejection code; an event is emitted only when judged."""

    status: str
    code: str
    event: dict[str, Any] | None = None
    diagnostic: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-compatible snapshot; diagnostics never authorize action."""
        return {
            "status": self.status,
            "code": self.code,
            "event": json.loads(json.dumps(self.event))
            if self.event is not None
            else None,
            "diagnostic": (
                json.loads(json.dumps(self.diagnostic))
                if self.diagnostic is not None
                else None
            ),
        }


class _Reject(ValueError):
    """A stable fail-closed contract violation."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise _Reject(code)


def _text(value: Any, code: str) -> None:
    _require(
        type(value) is str and bool(TEXT.fullmatch(value)) and value == value.strip(),
        code,
    )


def _digest(value: Any, code: str) -> None:
    _require(type(value) is str and HEX.fullmatch(value) is not None, code)


def _timestamp(value: Any, code: str) -> datetime:
    _text(value, code)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _Reject(code) from exc
    _require(
        parsed.tzinfo is not None
        and parsed.utcoffset() is not None
        and parsed.utcoffset().total_seconds() == 0
        and (value.endswith("Z") or value.endswith("+00:00")),
        code,
    )
    return parsed


def _refs(value: Any, code: str, *, nonempty: bool = False) -> None:
    _require(type(value) is list and (not nonempty or bool(value)), code)
    for reference in value:
        _text(reference, code)
    _require(len(value) == len(set(value)), code)


def _decimal(value: Any, code: str) -> Decimal:
    _require(type(value) is str and DECIMAL.fullmatch(value) is not None, code)
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise _Reject(code) from exc
    _require(number.is_finite(), code)
    return number


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def make_alert_ids(payload: Mapping[str, Any]) -> dict[str, str]:
    """Derive three separate deterministic identities from a complete event claim.

    The dedupe key identifies one rule evaluation, not a global ticker or a summary
    card. The alert ID additionally binds its observed value, evidence and source.
    This helper is not an attestation and must not be used as proof of provenance.
    """
    identity = {
        key: payload[key]
        for key in (
            "schema_version",
            "pool",
            "run_mode",
            "market",
            "code",
            "instrument_type",
            "strategy",
            "strategy_version",
            "source_batch",
            "owner",
            "watch_rule",
            "watch_rule_version",
            "membership_event",
            "rule",
            "comparator",
            "threshold",
            "quote_as_of",
        )
    }
    base = hashlib.sha256(_canonical(identity)).hexdigest()
    evidence = {
        key: payload[key]
        for key in (
            "observed",
            "visible_at",
            "source_hash",
            "source_ref",
            "source_snapshot",
            "source_kind",
            "quality",
            "facts",
            "hypotheses",
            "supporting_refs",
            "contradicting_refs",
            "evidence_assessment",
        )
    }
    alert = hashlib.sha256(
        _canonical({"identity": identity, "evidence": evidence})
    ).hexdigest()
    return {
        "dedupe_key": base,
        "trace_id": hashlib.sha256(_canonical({"watch_trace_v1": base})).hexdigest(),
        "alert_id": alert,
    }


def _receipt(value: Any, code: str, visible: datetime, evaluation: datetime) -> str:
    _require(type(value) is dict and set(value) == {"status", "receipt_id", "at"}, code)
    status = value["status"]
    _require(
        status in ("NOT_ATTEMPTED", "RECORDED") if type(status) is str else False, code
    )
    if status == "NOT_ATTEMPTED":
        _require(value["receipt_id"] is None and value["at"] is None, code)
    else:
        _text(value["receipt_id"], code)
        at = _timestamp(value["at"], code)
        _require(visible <= at <= evaluation, code + "_TIME")
    return status


def validate_alert(
    payload: Mapping[str, Any], *, evaluation_as_of: datetime
) -> AlertValidation:
    """Validate one isolated event with an externally supplied UTC evaluation clock.

    This only checks self-consistency, never production eligibility. UNJUDGEABLE
    means no safe actionable predicate can be inferred. NOT_TRIGGERED requires
    complete identity, membership, quote, evidence and temporal gates. Contradicting
    facts remain in non-actionable diagnostics instead of being silently discarded.
    """
    try:
        _require(
            isinstance(payload, Mapping) and set(payload) == FIELDS, "SCHEMA_FIELDS"
        )
        _require(
            isinstance(evaluation_as_of, datetime)
            and evaluation_as_of.tzinfo is not None
            and evaluation_as_of.utcoffset() is not None
            and evaluation_as_of.utcoffset().total_seconds() == 0,
            "EVALUATION_TIME",
        )
        # Round-trip rejects custom objects, NaN/Infinity and non-JSON structures.
        event = json.loads(_canonical(dict(payload)))
        _require(event["schema_version"] == "WATCH1_V1", "SCHEMA_VERSION")
        _require(
            event["pool"] in ("A", "B") if type(event["pool"]) is str else False, "POOL"
        )
        _require(
            event["run_mode"] in ("SHADOW", "SIM_MARKET")
            if type(event["run_mode"]) is str
            else False,
            "RUN_MODE",
        )
        for field in ("market", "code", "instrument_type", "rule"):
            _text(event[field], "IDENTITY_" + field.upper())
        _require(
            event["instrument_type"] in ("STOCK", "ETF", "INDEX", "FUND"),
            "INSTRUMENT_TYPE",
        )
        for field in (
            "strategy",
            "strategy_version",
            "source_batch",
            "owner",
            "watch_rule",
            "watch_rule_version",
        ):
            if event[field] is not None:
                _text(event[field], "QUALIFICATION_" + field.upper())
        if event["pool"] == "A":
            _require(
                all(
                    event[key] is not None
                    for key in ("strategy", "strategy_version", "source_batch")
                )
                and all(
                    event[key] is None
                    for key in ("owner", "watch_rule", "watch_rule_version")
                ),
                "POOL_A_QUALIFICATION",
            )
        else:
            _require(
                all(
                    event[key] is not None
                    for key in ("owner", "watch_rule", "watch_rule_version")
                )
                and all(
                    event[key] is None
                    for key in ("strategy", "strategy_version", "source_batch")
                ),
                "POOL_B_QUALIFICATION",
            )
        membership = event["membership_event"]
        _require(
            type(membership) is dict
            and set(membership)
            == {"event_id", "kind", "as_of", "market", "code", "pool", "qualification"},
            "MEMBERSHIP_SCHEMA",
        )
        for field in ("event_id", "market", "code"):
            _text(membership[field], "MEMBERSHIP_SCHEMA")
        qualification_keys = (
            ("strategy", "strategy_version", "source_batch")
            if event["pool"] == "A"
            else ("owner", "watch_rule", "watch_rule_version")
        )
        qualification = {key: event[key] for key in qualification_keys}
        _require(
            membership["market"] == event["market"]
            and membership["code"] == event["code"]
            and membership["pool"] == event["pool"]
            and membership["qualification"] == qualification
            and type(membership["qualification"]) is dict,
            "MEMBERSHIP_IDENTITY",
        )
        _require(
            membership["kind"] in ACTIVE_KINDS
            if type(membership["kind"]) is str
            else False,
            "MEMBERSHIP_INACTIVE",
        )
        member_as_of = _timestamp(membership["as_of"], "MEMBERSHIP_TIMEZONE")
        quote = _timestamp(event["quote_as_of"], "QUOTE_TIMEZONE")
        visible = _timestamp(event["visible_at"], "VISIBLE_TIMEZONE")
        _require(
            member_as_of <= quote <= visible <= evaluation_as_of,
            "FUTURE_OR_ORDERED_TIME",
        )
        _digest(event["source_hash"], "SOURCE_HASH")
        _require(
            type(event["source_ref"]) is str
            and SOURCE_REF.fullmatch(event["source_ref"]) is not None,
            "SOURCE_REF",
        )
        _require(event["source_kind"] == "DIRECT_QUOTE", "SUMMARY_NOT_EVIDENCE")
        _require(event["quality"] == "VERIFIED", "QUOTE_QUALITY")
        observed = _decimal(event["observed"], "OBSERVED")
        snapshot = event["source_snapshot"]
        snapshot_fields = {
            "source_ref",
            "source_kind",
            "market",
            "code",
            "instrument_type",
            "observed",
            "quote_as_of",
            "visible_at",
            "quality",
        }
        _require(
            type(snapshot) is dict and set(snapshot) == snapshot_fields,
            "SOURCE_SNAPSHOT_SCHEMA",
        )
        _require(
            all(snapshot[key] == event[key] for key in snapshot_fields),
            "SOURCE_SNAPSHOT_MISMATCH",
        )
        # This canonicalization detects internal mutation only, not original-byte integrity.
        _require(
            hashlib.sha256(_canonical(snapshot)).hexdigest() == event["source_hash"],
            "SOURCE_SNAPSHOT_HASH",
        )
        for field in ("facts", "hypotheses", "supporting_refs", "contradicting_refs"):
            _refs(
                event[field],
                "EVIDENCE_" + field.upper(),
                nonempty=field in ("facts", "supporting_refs"),
            )
        _require(
            set(event["supporting_refs"]).issubset(event["facts"])
            and set(event["contradicting_refs"]).issubset(event["facts"])
            and not set(event["supporting_refs"]).intersection(
                event["contradicting_refs"]
            ),
            "EVIDENCE_CONFLICT",
        )
        contested = bool(event["contradicting_refs"])
        _require(
            event["evidence_assessment"] == ("CONTESTED" if contested else "SUPPORTED"),
            "EVIDENCE_ASSESSMENT",
        )
        _require(
            event["comparator"] in COMPARATORS
            if type(event["comparator"]) is str
            else False,
            "COMPARATOR",
        )
        threshold = _decimal(event["threshold"], "THRESHOLD")
        expected = make_alert_ids(event)
        _require(
            all(event[key] == value for key, value in expected.items()),
            "IDENTITY_DIGEST",
        )
        notification = _receipt(
            event["notification"], "NOTIFICATION_RECEIPT", visible, evaluation_as_of
        )
        handoff = _receipt(
            event["sim_handoff"], "SIM_HANDOFF_RECEIPT", visible, evaluation_as_of
        )
        _require(
            notification != "RECORDED"
            or handoff != "RECORDED"
            or event["notification"]["receipt_id"]
            != event["sim_handoff"]["receipt_id"],
            "CROSS_DOMAIN_RECEIPT_ID",
        )
        triggered = COMPARATORS[event["comparator"]](observed, threshold)
        _require(
            triggered or (notification == handoff == "NOT_ATTEMPTED"),
            "NON_TRIGGERED_RECEIPT",
        )
        _require(
            event["run_mode"] == "SIM_MARKET"
            or (notification == handoff == "NOT_ATTEMPTED"),
            "SHADOW_SIDE_EFFECT",
        )
        if contested and triggered:
            _require(notification == handoff == "NOT_ATTEMPTED", "CONTESTED_RECEIPT")
            # Keep only non-actionable evidence identifiers and comparison context.
            diagnostic = {
                key: event[key]
                for key in (
                    "alert_id",
                    "trace_id",
                    "source_ref",
                    "source_hash",
                    "facts",
                    "supporting_refs",
                    "contradicting_refs",
                    "evidence_assessment",
                    "comparator",
                    "threshold",
                    "observed",
                )
            }
            return AlertValidation(
                "UNJUDGEABLE", "EVIDENCE_CONTESTED", None, diagnostic
            )
        return AlertValidation(
            "TRIGGERED" if triggered else "NOT_TRIGGERED", "OK", event
        )
    except (_Reject, TypeError, ValueError, OverflowError, KeyError) as exc:
        return AlertValidation(
            "UNJUDGEABLE",
            str(exc) if isinstance(exc, _Reject) else "INVALID_JSON_VALUE",
        )


def load_alert_json(raw: str, *, evaluation_as_of: datetime) -> AlertValidation:
    """Validate encoded JSON, rejecting duplicate keys before validation."""

    def unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise _Reject("DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        payload = json.loads(
            raw,
            object_pairs_hook=unique_pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(
                _Reject("INVALID_JSON_VALUE")
            ),
        )
    except _Reject as exc:
        return AlertValidation("UNJUDGEABLE", str(exc))
    except (TypeError, ValueError, json.JSONDecodeError):
        return AlertValidation("UNJUDGEABLE", "INVALID_JSON_VALUE")
    return validate_alert(payload, evaluation_as_of=evaluation_as_of)
