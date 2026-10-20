"""Read-only acceptance-evidence export for one stock and lane.

This module is read-only: every function issues SELECT statements on a
caller-owned SQLite connection and never commits, rolls back, or closes it.
Missing tables raise sqlite3.Error instead of being papered over. Coverage
attribution problems stay closed on failure: the coverage section becomes {"status": "blocked", "reason": ...} with no fabricated
numbers. Gaps are inferred only from trusted watermark state; this module
never invents date ranges or a "reached the end" conclusion. The result is
an offline evidence bundle for human review. It is not a production
acceptance conclusion.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

try:
    from . import xq_content_coverage
    from . import xq_reverse_ledger as ledger
except ImportError:
    import xq_content_coverage
    import xq_reverse_ledger as ledger


#: Most recent page attempts listed per stock and lane in ``failures``.
FAILURE_SAMPLE_LIMIT: int = 10


def _required_symbol(value: Any) -> str:
    """Require an exact nonempty stock identifier, without normalization."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("symbol must be a nonempty string")
    return value


def _required_lane(value: Any) -> str:
    """Accept only the two independently tracked lanes."""
    if not isinstance(value, str) or value not in ledger.LANES:
        raise ValueError("lane must be 'new' or 'history'")
    return value


def _required_sample_limit(value: Any) -> int:
    """Require a positive sample size; booleans are not counts."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("sample_limit must be a positive integer")
    return value


def _coverage_section(
    conn: sqlite3.Connection, symbol: str, lane: str,
) -> dict[str, Any]:
    """Build the coverage block, closed on failure without invented numbers.

    Content times and fetch-confirmation times stay separate fields. An
    attribution disagreement becomes {"status": "blocked", "reason": ...}
    with no numeric fields at all; a lane without evidenced posts is exactly
    {"status": "empty"}.
    """
    try:
        coverage = xq_content_coverage.read_content_coverage(conn, symbol, lane)
    except xq_content_coverage.CoverageAttributionBlockedError as error:
        return {"status": "blocked", "reason": str(error)}
    if coverage.evidenced_post_count == 0:
        return {"status": "empty"}
    return {
        "status": "ok",
        "earliest_created_at_ms": coverage.earliest_created_at_ms,
        "earliest_post_id": coverage.earliest_post_id,
        "latest_created_at_ms": coverage.latest_created_at_ms,
        "latest_post_id": coverage.latest_post_id,
        "evidenced_post_count": coverage.evidenced_post_count,
        "coverage_started_at": coverage.coverage_started_at,
        "coverage_updated_at": coverage.coverage_updated_at,
    }


def _post_samples(
    conn: sqlite3.Connection, symbol: str, sample_limit: int,
) -> list[dict[str, Any]]:
    """Sample evidenced originals, newest content time first.

    Rows start from this stock's relations and join the same-batch original
    version plus the stored post row. One post observed in several batches
    yields one representative row (the smallest relation batch_id) while
    ``batch_ids`` lists every batch the post was observed in. Content time
    comes from the representative batch's own original version so the sample
    stays comparable with the coverage boundary.
    """
    rows = conn.execute(
        """SELECT r.post_id, r.batch_id, v.created_at_ms,
                  p.first_fetched_at, p.last_fetched_at,
                  p.original_url, p.batch_ids_json
             FROM xq_reverse_post_symbol AS r
             JOIN xq_reverse_raw_post_version AS v
               ON v.post_id = r.post_id AND v.batch_id = r.batch_id
             JOIN xq_reverse_raw_post AS p
               ON p.post_id = r.post_id
            WHERE r.symbol = ?
            ORDER BY r.post_id, r.batch_id, r.evidence_type, r.evidence_json""",
        (symbol,),
    ).fetchall()
    per_post: dict[str, dict[str, Any]] = {}
    for (post_id, batch_id, created_at_ms, first_fetched_at,
         last_fetched_at, original_url, batch_ids_json) in rows:
        if post_id in per_post:
            continue
        try:
            batch_ids = json.loads(batch_ids_json)
        except (TypeError, ValueError) as error:
            raise ValueError("stored batch_ids_json must be a JSON list") from error
        if not isinstance(batch_ids, list) or not batch_ids or any(
                not isinstance(item, str) or not item.strip() for item in batch_ids):
            raise ValueError("stored batch_ids_json must list nonempty batch strings")
        per_post[post_id] = {
            "post_id": post_id,
            "created_at_ms": created_at_ms,
            "first_fetched_at": first_fetched_at,
            "last_fetched_at": last_fetched_at,
            "original_url": original_url,
            "batch_id": batch_id,
            "batch_ids": batch_ids,
        }
    samples = sorted(
        per_post.values(),
        key=lambda item: (-item["created_at_ms"], item["post_id"]),
    )
    return samples[:sample_limit]


def _source_chain(conn: sqlite3.Connection, symbol: str) -> dict[str, Any]:
    """List every stored relation for this stock as its traceable chain.

    Downstream split units are outside this read model. They are explicitly
    marked ``downstream_units: "not_linked"`` instead of being pretended.
    """
    rows = conn.execute(
        """SELECT DISTINCT post_id, evidence_type, batch_id
             FROM xq_reverse_post_symbol
            WHERE symbol = ?
            ORDER BY post_id, batch_id, evidence_type""",
        (symbol,),
    ).fetchall()
    links: dict[str, list[dict[str, str]]] = {}
    for post_id, evidence_type, batch_id in rows:
        links.setdefault(post_id, []).append({
            "symbol": symbol,
            "evidence_type": evidence_type,
            "batch_id": batch_id,
        })
    return {
        "downstream_units": "not_linked",
        "post_links": links,
    }


def _failures(conn: sqlite3.Connection, symbol: str, lane: str) -> list[dict[str, Any]]:
    """List the most recent unconfirmed attempts, newest first."""
    rows = conn.execute(
        """SELECT attempt_id, batch_id, page_key, status, detail, fetched_at
             FROM xq_reverse_page_attempt
            WHERE symbol = ? AND lane = ?
            ORDER BY attempt_id DESC
            LIMIT ?""",
        (symbol, lane, FAILURE_SAMPLE_LIMIT),
    ).fetchall()
    return [
        {
            "attempt_id": row[0],
            "batch_id": row[1],
            "page_key": row[2],
            "status": row[3],
            "detail": row[4],
            "fetched_at": row[5],
        }
        for row in rows
    ]


def _pending(conn: sqlite3.Connection, symbol: str, lane: str) -> dict[str, Any]:
    """Report queued work faithfully, including an absent queue table."""
    table = conn.execute(
        """SELECT name FROM sqlite_master
            WHERE type = 'table' AND name = 'xq_offline_queue'""",
    ).fetchone()
    if table is None:
        return {"queue": "not_initialized"}
    row = conn.execute(
        """SELECT state, page_key, claim_id
             FROM xq_offline_queue
            WHERE symbol = ? AND lane = ?""",
        (symbol, lane),
    ).fetchone()
    if row is None:
        return {"queue": "initialized", "state": None,
                "page_key": None, "claim_id": None}
    return {"queue": "initialized", "state": row[0],
            "page_key": row[1], "claim_id": row[2]}


def _gaps(lane: str, watermark: dict[str, Any],
          pending: dict[str, Any]) -> dict[str, Any]:
    """Infer only watermark-provable gaps; never fabricate dates or an end.

    A history lane with a confirmed ``next_cursor`` is demonstrably
    unfinished. A history task waiting on a cursor request is also
    unfinished even though its resume key is unknown. Everything else is
    ``{"type": "unknown"}``; a missing cursor is never read as "history has
    ended", and no date interval is ever invented.
    """
    if lane == "history":
        cursor = watermark.get("next_cursor")
        if cursor is not None:
            return {"type": "history_unfinished", "next_cursor": cursor}
        if (pending.get("queue") == "initialized"
                and pending.get("state") == "awaiting_cursor"):
            return {"type": "history_unfinished", "next_cursor": None}
    return {"type": "unknown"}


def export_evidence(conn: sqlite3.Connection, symbol: str, lane: str,
                    sample_limit: int = 5) -> dict[str, Any]:
    """Export one read-only acceptance-evidence bundle for a stock and lane.

    The bundle contains: ``symbol`` and ``lane``; ``watermark`` with the
    ledger checkpoint's scan progress (version, confirmed_page_key,
    next_cursor, first_status_id, last_status_id, coverage_started_at,
    coverage_updated_at, state, batch_id, fetched_at); ``coverage`` with
    content-time boundaries and the separate fetch-confirmation times, or
    exactly ``{"status": "empty"}`` / ``{"status": "blocked", "reason": ...}``;
    ``post_samples`` with original content time and fetch times plus the
    full observed ``batch_ids`` list; ``source_chain`` mapping each post to
    its stored relations and marking ``downstream_units`` as "not_linked";
    ``failures`` with the most recent page attempts; ``pending`` with the
    offline queue state (or ``{"queue": "not_initialized"}``); and ``gaps``
    with only watermark-provable pending boundaries.

    This function only issues SELECT statements on the caller-owned
    connection; it neither creates schema nor commits, rolls back, or
    closes a transaction.

    Raises:
        ValueError: The requested stock, lane, or sample size is invalid,
            or a stored batch list cannot be parsed without fabrication.
        sqlite3.Error: The required tables are absent or SQLite cannot read.
    """
    symbol = _required_symbol(symbol)
    lane = _required_lane(lane)
    sample_limit = _required_sample_limit(sample_limit)
    checkpoint = ledger.get_checkpoint(conn, symbol, lane)
    watermark = {
        "version": checkpoint["version"],
        "confirmed_page_key": checkpoint["confirmed_page_key"],
        "next_cursor": checkpoint["next_cursor"],
        "first_status_id": checkpoint["first_status_id"],
        "last_status_id": checkpoint["last_status_id"],
        "coverage_started_at": checkpoint["coverage_started_at"],
        "coverage_updated_at": checkpoint["coverage_updated_at"],
        "state": checkpoint["state"],
        "batch_id": checkpoint["batch_id"],
        "fetched_at": checkpoint["fetched_at"],
    }
    pending = _pending(conn, symbol, lane)
    return {
        "symbol": symbol,
        "lane": lane,
        "watermark": watermark,
        "coverage": _coverage_section(conn, symbol, lane),
        "post_samples": _post_samples(conn, symbol, sample_limit),
        "source_chain": _source_chain(conn, symbol),
        "failures": _failures(conn, symbol, lane),
        "pending": pending,
        "gaps": _gaps(lane, watermark, pending),
    }


def export_all(conn: sqlite3.Connection, lane: str) -> list[dict[str, Any]]:
    """Export one bundle per stock that has a checkpoint row in this lane.

    Read-only, and closed on failure per stock: a stock whose attribution is
    blocked reports ``status: blocked`` in its own bundle without stopping
    the others. Missing tables still raise sqlite3.Error. Symbols without a
    checkpoint row carry no scan progress and are not synthesized here.
    """
    lane = _required_lane(lane)
    rows = conn.execute(
        """SELECT symbol FROM xq_reverse_checkpoint
            WHERE lane = ? ORDER BY symbol""",
        (lane,),
    ).fetchall()
    return [export_evidence(conn, row[0], lane) for row in rows]
