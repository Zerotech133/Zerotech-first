"""Fail-closed, read-only strategy-pool membership projection.

No independent production runner, approved strategy universe or trusted batch
archive is wired here. In-memory/offline receipts, strategy catalog cards and
Watch Tower source events are never sufficient evidence of membership.
"""

from __future__ import annotations

from datetime import date, datetime
import re
from urllib.parse import parse_qs


class ProjectionQueryError(ValueError):
    """A malformed or unsupported membership query."""


_ALLOWED = frozenset(
    {"pool", "strategy_name", "strategy_version", "as_of", "market", "code"}
)
_NAME = re.compile(r"^[^\x00-\x1f\x7f<>]{1,128}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+:-]{0,63}$")
_MARKET = re.compile(r"^[A-Z][A-Z0-9_]{0,15}$")
_CODE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")


def _as_of(value: str) -> str:
    """Accept a real calendar date or a timezone-qualified instant only."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        try:
            return date.fromisoformat(value).isoformat()
        except ValueError as error:
            raise ProjectionQueryError("invalid as_of") from error
    try:
        instant = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProjectionQueryError("invalid as_of") from error
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ProjectionQueryError("as_of datetime requires timezone")
    return instant.isoformat()


def project_membership(query_string: str) -> dict[str, object]:
    """Validate one A-pool query and return a fixed unverified projection.

    This function intentionally has no access to the offline strategy-pool
    engine or any filesystem, producer receipt, account or trading endpoint.
    """
    if not isinstance(query_string, str) or len(query_string) > 4096:
        raise ProjectionQueryError("invalid query length")
    try:
        fields = parse_qs(
            query_string,
            keep_blank_values=True,
            strict_parsing=True,
            max_num_fields=6,
            errors="strict",
        )
    except (ValueError, UnicodeDecodeError) as error:
        raise ProjectionQueryError("invalid query encoding or fields") from error
    if set(fields) - _ALLOWED or any(len(values) != 1 for values in fields.values()):
        raise ProjectionQueryError("unknown or duplicate query parameter")
    if not {"pool", "strategy_name", "strategy_version", "as_of"} <= fields.keys():
        raise ProjectionQueryError(
            "pool, strategy_name, strategy_version and as_of are required"
        )
    if fields["pool"][0] != "A":
        raise ProjectionQueryError(
            "only the independent A-pool projection is available"
        )
    name = fields["strategy_name"][0]
    version = fields["strategy_version"][0]
    if name != name.strip() or not _NAME.fullmatch(name):
        raise ProjectionQueryError("invalid strategy_name")
    if not _VERSION.fullmatch(version):
        raise ProjectionQueryError("invalid strategy_version")
    if ("market" in fields) != ("code" in fields):
        raise ProjectionQueryError("market and code must be supplied together")
    market = fields.get("market", [None])[0]
    code = fields.get("code", [None])[0]
    if market is not None and (
        not _MARKET.fullmatch(market) or not _CODE.fullmatch(code)
    ):
        raise ProjectionQueryError("invalid market or code")
    as_of = _as_of(fields["as_of"][0])
    return {
        "pool": "A",
        "strategy_name": name,
        "strategy_version": version,
        "as_of": as_of,
        "market": market,
        "code": code,
        "status": "SOURCE_UNVERIFIED",
        "reason_code": "TRUSTED_SOURCE_UNAVAILABLE",
        "coverage": {"verified": False, "reason_code": "TRUSTED_SOURCE_UNAVAILABLE"},
        "verified_memberships": [],
        "events": [],
        "unverified_observations": [],
    }
