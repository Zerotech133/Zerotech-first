"""Read-only progress-view data model for the Xueqiu primary source.

Presentation-layer read model (PRD P1.2): assemble a per-stock progress
view and a multi-stock board from the existing offline modules without
building any UI, scheduling any work, or advancing any watermark. Every
function only reads through ``xq_acceptance_evidence.export_evidence``,
``xq_reverse_ledger.get_checkpoint``, ``xq_offline_queue.tasks`` and
``xq_history_priority.gap_alerts`` on a caller-owned SQLite connection;
this module issues no INSERT/UPDATE/DELETE and never commits, rolls
back, or closes the connection.

closed on failure: an invalid stock identifier, a non-integer or
negative ``as_of_ms`` (booleans are not integers here), a non-positive
``sample_limit``, or a malformed ``symbols`` list raises ``ValueError``
before any result is produced. A partial board or a partially populated
lane is never returned. Missing data surfaces as explicit ``None`` or as
the upstream module's own empty markers; no number is ever fabricated.

Freshness is derived exclusively from the caller-supplied ``as_of_ms``
minus stored timestamps; the wall clock is never read, and a negative
``age_since_fetch_ms`` is preserved verbatim rather than clamped.

Scope of the underlying evidence sections is documented on
:func:`build_progress_view`: ``post_samples``/``source_chain`` are
stock-level (shared across lanes by symbol) while ``coverage``,
``watermark``, ``failures``, ``pending`` and ``gaps`` are lane-level.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict
from typing import Any

try:
    from . import xq_acceptance_evidence
    from . import xq_history_priority
    from . import xq_offline_queue
    from . import xq_reverse_ledger as ledger
except ImportError:
    import xq_acceptance_evidence
    import xq_history_priority
    import xq_offline_queue
    import xq_reverse_ledger as ledger


#: The two independently tracked lanes, in board-column order.
LANES: tuple[str, ...] = ("new", "history")

#: Exact top-level keys of one progress view.
VIEW_KEYS: tuple[str, ...] = ("symbol", "lanes", "generated_as_of_ms", "notes")

#: Exact keys of one lane's progress row.
LANE_KEYS: tuple[str, ...] = (
    "queue_state", "watermark", "freshness", "coverage",
    "samples", "gaps", "failures", "alerts",
)

#: The ten ledger checkpoint keys carried inside ``watermark``.
WATERMARK_KEYS: tuple[str, ...] = (
    "version", "confirmed_page_key", "next_cursor", "first_status_id",
    "last_status_id", "coverage_started_at", "coverage_updated_at",
    "state", "batch_id", "fetched_at",
)

#: Notes every progress view must carry, verbatim.
NOTES: tuple[str, ...] = (
    "read-only progress view; does not advance watermarks",
    "downstream units not linked",
)


def _required_symbol(value: Any) -> str:
    """Require an exact nonempty stock identifier, without normalization.

    closed on failure: booleans, numbers, blank strings and whitespace-only
    strings all raise ``ValueError`` instead of being coerced.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError("symbol must be a nonempty string")
    return value


def _required_as_of_ms(value: Any) -> int:
    """Require a nonnegative integer evaluation time; the wall clock is banned.

    closed on failure: booleans, strings, floats, negative integers and
    every other type raise ``ValueError``. The caller's instant is the only
    clock this module ever consults.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("as_of_ms must be a nonnegative integer")
    return value


def _required_sample_limit(value: Any) -> int:
    """Require a positive sample size; booleans are not counts.

    closed on failure: zero, negative values, booleans and non-integers
    raise ``ValueError`` instead of producing a partial sample list.
    """
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("sample_limit must be a positive integer")
    return value


def _queue_state(
    conn: sqlite3.Connection, symbol: str, lane: str,
) -> dict[str, Any] | None:
    """Return the durable queue row for one stock-lane, or None when absent.

    No task means the whole value is ``None``; a present task is reduced to
    exactly ``{state, page_key, claim_id, batch_id}``. Nothing is invented
    when the queue is empty for this stock-lane.
    """
    for task in xq_offline_queue.tasks(conn):
        if task.symbol == symbol and task.lane == lane:
            return {
                "state": task.state,
                "page_key": task.page_key,
                "claim_id": task.claim_id,
                "batch_id": task.batch_id,
            }
    return None


def _freshness(
    as_of_ms: int, coverage: dict[str, Any], checkpoint: dict[str, Any],
) -> dict[str, Any]:
    """Derive freshness strictly from ``as_of_ms`` and stored timestamps.

    ``latest_content_ms`` comes from the coverage boundary only when the
    coverage section actually carries one; ``latest_fetched_ms`` is the
    ledger checkpoint's ``fetched_at``; ``age_since_fetch_ms`` is
    ``as_of_ms - fetched_at`` with negative values preserved verbatim
    (never clamped). Missing inputs yield explicit ``None``.
    """
    latest_content_ms: int | None = None
    if isinstance(coverage, dict) and "latest_created_at_ms" in coverage:
        latest_content_ms = coverage["latest_created_at_ms"]
    fetched_at = checkpoint["fetched_at"]
    age_since_fetch_ms: int | None = None
    if fetched_at is not None:
        age_since_fetch_ms = as_of_ms - fetched_at
    return {
        "latest_content_ms": latest_content_ms,
        "latest_fetched_ms": fetched_at,
        "age_since_fetch_ms": age_since_fetch_ms,
    }


def _lane_view(
    conn: sqlite3.Connection, symbol: str, lane: str,
    as_of_ms: int, sample_limit: int,
) -> dict[str, Any]:
    """Assemble one lane's progress row from the upstream read models.

    ``watermark`` is the verbatim :func:`xq_reverse_ledger.get_checkpoint`
    result (carrying all ten scan-progress keys); ``coverage``, ``samples``,
    ``gaps`` and ``failures`` are passed through verbatim from the
    ``xq_acceptance_evidence`` bundle; ``alerts`` converts the
    ``xq_history_priority.gap_alerts`` rows to plain dicts.
    """
    bundle = xq_acceptance_evidence.export_evidence(conn, symbol, lane, sample_limit)
    checkpoint = ledger.get_checkpoint(conn, symbol, lane)
    coverage = bundle["coverage"]
    return {
        "queue_state": _queue_state(conn, symbol, lane),
        "watermark": dict(checkpoint),
        "freshness": _freshness(as_of_ms, coverage, checkpoint),
        "coverage": coverage,
        "samples": bundle["post_samples"],
        "gaps": bundle["gaps"],
        "failures": bundle["failures"],
        "alerts": [asdict(alert) for alert in xq_history_priority.gap_alerts(bundle)],
    }


def build_progress_view(
    conn: sqlite3.Connection, symbol: str, *,
    as_of_ms: int, sample_limit: int = 5,
) -> dict[str, Any]:
    """Build the read-only two-lane progress view for one stock.

    The returned dict carries exactly ``symbol``, ``lanes``,
    ``generated_as_of_ms`` and ``notes``; ``lanes`` carries exactly the
    keys ``{"new", "history"}``; each lane row carries exactly
    ``queue_state``, ``watermark``, ``freshness``, ``coverage``,
    ``samples``, ``gaps``, ``failures`` and ``alerts``.

    Section scope inside the underlying evidence bundle: ``post_samples``
    (surfaced here as ``samples``) and ``source_chain`` are stock-level,
    shared across lanes by symbol; ``coverage``, ``watermark``,
    ``failures``, ``pending`` and ``gaps`` are lane-level.

    This function is strictly read-only: it never advances watermarks,
    never schedules or claims queue work, and never writes to the
    database. ``generated_as_of_ms`` echoes the caller's ``as_of_ms``
    verbatim; the wall clock is never consulted.

    closed on failure: an invalid symbol, ``as_of_ms`` or ``sample_limit``
    raises ``ValueError`` before any lane is assembled, so a partial view
    is never returned.

    Args:
        conn: Caller-owned SQLite connection holding the offline schema.
        symbol: Exact nonempty stock identifier.
        as_of_ms: Nonnegative integer evaluation instant (Unix ms).
        sample_limit: Positive maximum number of post samples per lane.

    Returns:
        The progress view dict described above. ``notes`` always contains
        "read-only progress view; does not advance watermarks" and
        "downstream units not linked".

    Raises:
        ValueError: The stock, evaluation instant, or sample size is
            invalid. Closed on failure.
        sqlite3.Error: The required tables are absent or SQLite cannot
            read.
    """
    symbol = _required_symbol(symbol)
    as_of_ms = _required_as_of_ms(as_of_ms)
    sample_limit = _required_sample_limit(sample_limit)
    lanes = {
        lane: _lane_view(conn, symbol, lane, as_of_ms, sample_limit)
        for lane in LANES
    }
    return {
        "symbol": symbol,
        "lanes": lanes,
        "generated_as_of_ms": as_of_ms,
        "notes": list(NOTES),
    }


def build_board(
    conn: sqlite3.Connection, symbols: list[str], *,
    as_of_ms: int, sample_limit: int = 5,
) -> list[dict[str, Any]]:
    """Build one progress view per stock, preserving the caller's order.

    The whole input is validated before any view is assembled: ``symbols``
    must be a list of nonempty strings and ``as_of_ms``/``sample_limit``
    must be valid. A single invalid entry rejects the entire board.

    closed on failure: any invalid input raises ``ValueError`` before the
    first view is built, so a partial board is never returned.

    Args:
        conn: Caller-owned SQLite connection holding the offline schema.
        symbols: Stock identifiers in the caller's desired board order.
        as_of_ms: Nonnegative integer evaluation instant (Unix ms).
        sample_limit: Positive maximum number of post samples per lane.

    Returns:
        A list of progress-view dicts in exactly the input order.

    Raises:
        ValueError: ``symbols`` is not a list, any entry is not a nonempty
            string, or ``as_of_ms``/``sample_limit`` is invalid. Closed on
            failure; no partial board.
        sqlite3.Error: The required tables are absent or SQLite cannot
            read.
    """
    if not isinstance(symbols, list):
        raise ValueError("symbols must be a list of nonempty strings")
    for item in symbols:
        _required_symbol(item)
    as_of_ms = _required_as_of_ms(as_of_ms)
    sample_limit = _required_sample_limit(sample_limit)
    return [
        build_progress_view(
            conn, item, as_of_ms=as_of_ms, sample_limit=sample_limit)
        for item in symbols
    ]
