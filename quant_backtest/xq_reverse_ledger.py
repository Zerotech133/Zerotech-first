"""Purely offline, per-symbol reverse-crawl checkpoint ledger.

Only :func:`commit_page` advances a confirmed scan checkpoint. The checkpoint's
page key, cursor and version track scanned pages, including legitimate transit
pages without a mention of the target symbol. Coverage timestamps and status
IDs track only original posts with verified target-symbol content evidence.
Posts, evidence, receipt, page members and checkpoint share one SQLite
transaction.
The caller owns the connection, schema creation, and every network decision.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict
from typing import Any

try:
    from . import xq_reverse_store
except ImportError:
    import xq_reverse_store


LANES: frozenset[str] = frozenset({"new", "history"})
ATTEMPT_STATUSES: frozenset[str] = frozenset({
    "page_limit", "active_stop", "stopped", "timeout", "empty", "duplicate", "403",
    "http_404",
    "token_invalid", "partial_page_failed", "failed",
})


class CheckpointConflictError(RuntimeError):
    """Raised when a page is out of order or a checkpoint version is stale."""


class PageIdentityError(ValueError):
    """Raised when a proposed confirmed page lacks trustworthy post IDs."""


def init_schema(conn: sqlite3.Connection) -> None:
    """Install the ledger tables on a caller-owned SQLite connection.

    SQLite executescript can commit an existing transaction. Schema creation
    is therefore forbidden while the caller has an active transaction.
    """
    if conn.in_transaction:
        raise RuntimeError("init_schema requires a connection outside a transaction")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS xq_reverse_checkpoint (
            symbol TEXT NOT NULL,
            lane TEXT NOT NULL CHECK (lane IN ('new', 'history')),
            version INTEGER NOT NULL DEFAULT 0 CHECK (version >= 0),
            confirmed_page_key TEXT,
            next_cursor TEXT,
            first_status_id TEXT,
            last_status_id TEXT,
            coverage_started_at INTEGER,
            coverage_updated_at INTEGER,
            state TEXT NOT NULL DEFAULT 'pending'
                CHECK (state IN ('pending', 'confirmed')),
            batch_id TEXT,
            fetched_at INTEGER,
            PRIMARY KEY (symbol, lane)
        );
        CREATE TABLE IF NOT EXISTS xq_reverse_page_receipt (
            symbol TEXT NOT NULL,
            lane TEXT NOT NULL CHECK (lane IN ('new', 'history')),
            page_key TEXT NOT NULL,
            batch_id TEXT NOT NULL,
            payload_hash TEXT NOT NULL,
            post_count INTEGER NOT NULL CHECK (post_count > 0),
            first_status_id TEXT NOT NULL,
            last_status_id TEXT NOT NULL,
            next_cursor TEXT,
            confirmed_version INTEGER NOT NULL,
            fetched_at INTEGER NOT NULL,
            PRIMARY KEY (symbol, lane, batch_id, page_key)
        );
        CREATE TABLE IF NOT EXISTS xq_reverse_page_attempt (
            attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            lane TEXT NOT NULL CHECK (lane IN ('new', 'history')),
            batch_id TEXT NOT NULL,
            page_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN (
                'page_limit', 'active_stop', 'stopped', 'timeout', 'empty', 'duplicate',
                '403', 'http_404', 'token_invalid', 'partial_page_failed', 'failed'
            )),
            detail TEXT NOT NULL DEFAULT '',
            fetched_at INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS xq_reverse_attempt_lookup
            ON xq_reverse_page_attempt (symbol, lane, page_key);
        CREATE TABLE IF NOT EXISTS xq_reverse_page_member (
            symbol TEXT NOT NULL,
            lane TEXT NOT NULL CHECK (lane IN ('new', 'history')),
            batch_id TEXT NOT NULL,
            page_key TEXT NOT NULL,
            ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
            post_id TEXT NOT NULL,
            PRIMARY KEY (symbol, lane, batch_id, page_key, ordinal),
            UNIQUE (symbol, lane, batch_id, page_key, post_id)
        );
    """)


def _nonempty(value: Any, name: str) -> str:
    """Validate a caller-provided, nonempty string without coercing IDs."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")
    return value


def _timestamp(value: Any) -> int:
    """Validate the source store's Unix-millisecond timestamp contract."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("fetched_at must be a nonnegative Unix millisecond integer")
    return value


def _validate_page(posts: Sequence[xq_reverse_store.RawPostInput]) -> tuple[str, str]:
    """Demand original top-level IDs rather than search or inferred identities."""
    if not isinstance(posts, (list, tuple)) or not posts:
        raise PageIdentityError("confirmed page requires at least one original post")
    identities: list[str] = []
    for post in posts:
        if not isinstance(post, xq_reverse_store.RawPostInput):
            raise PageIdentityError("each original post must be RawPostInput")
        identity = post.post_id
        if not isinstance(identity, str) or not identity.strip():
            raise PageIdentityError("each original post needs a real post_id")
        try:
            # Reject invented IDs before any receipt or watermark is considered.
            xq_reverse_store._post_payload(post)
        except (TypeError, ValueError) as error:
            raise PageIdentityError("original post identity or payload is invalid") from error
        identities.append(identity)
    if len(set(identities)) != len(identities):
        raise PageIdentityError("duplicate original posts cannot confirm a page")
    return identities[0], identities[-1]


def _page_digest(posts: Sequence[xq_reverse_store.RawPostInput],
                 relations: Sequence[xq_reverse_store.PostSymbolInput],
                 next_cursor: str | None) -> str:
    """Fingerprint replay payloads, so a page key cannot alias different data."""
    try:
        payload = json.dumps(
            {"posts": [asdict(post) for post in posts],
             "relations": [asdict(relation) for relation in relations],
             "next_cursor": next_cursor},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as error:
        raise ValueError("page payload must be finite JSON-compatible data") from error
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_checkpoint(conn: sqlite3.Connection, symbol: str,
                   lane: str) -> dict[str, Any]:
    """Read one independently confirmed lane (or its unstarted defaults)."""
    symbol = _nonempty(symbol, "symbol")
    if lane not in LANES:
        raise ValueError("lane must be 'new' or 'history'")
    row = conn.execute("""
        SELECT version, confirmed_page_key, next_cursor, first_status_id,
               last_status_id, coverage_started_at, coverage_updated_at,
               state, batch_id, fetched_at
          FROM xq_reverse_checkpoint WHERE symbol = ? AND lane = ?
    """, (symbol, lane)).fetchone()
    names = ("version", "confirmed_page_key", "next_cursor",
             "first_status_id", "last_status_id", "coverage_started_at",
             "coverage_updated_at", "state", "batch_id", "fetched_at")
    values = row if row is not None else (0, None, None, None, None,
                                          None, None, "pending", None, None)
    return {"symbol": symbol, "lane": lane, **dict(zip(names, values))}


def record_page_attempt(conn: sqlite3.Connection, symbol: str, lane: str,
                        batch_id: str, page_key: str, status: str,
                        fetched_at: int, detail: str = "") -> int:
    """Record an unconfirmed outcome without changing either lane's waterline.

    Must be called outside another transaction; a failing commit_page already
    rolls back, so its failure may be recorded through this separate transaction.
    Empty and duplicate are observations, never a history-end inference.
    """
    symbol = _nonempty(symbol, "symbol")
    if lane not in LANES:
        raise ValueError("lane must be 'new' or 'history'")
    batch_id = _nonempty(batch_id, "batch_id")
    page_key = _nonempty(page_key, "page_key")
    fetched_at = _timestamp(fetched_at)
    if status not in ATTEMPT_STATUSES:
        raise ValueError(f"invalid page attempt status: {status!r}")
    if not isinstance(detail, str):
        raise ValueError("detail must be a string")
    if conn.in_transaction:
        raise RuntimeError("record_page_attempt requires a standalone transaction")
    try:
        conn.execute("BEGIN IMMEDIATE")
        result = conn.execute("""
            INSERT INTO xq_reverse_page_attempt
                (symbol, lane, batch_id, page_key, status, detail, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (symbol, lane, batch_id, page_key, status, detail, fetched_at))
        attempt_id = result.lastrowid
        conn.commit()
        assert attempt_id is not None
        return attempt_id
    except Exception:
        conn.rollback()
        raise


def commit_page(conn: sqlite3.Connection, symbol: str, lane: str,
                batch_id: str, page_key: str,
                validated_posts: Sequence[xq_reverse_store.RawPostInput],
                relations: Sequence[xq_reverse_store.PostSymbolInput],
                next_cursor: str | None, expected_version: int,
                fetched_at: int) -> dict[str, Any]:
    """Atomically save original posts and advance only the selected lane.

    The *caller* supplies opaque page keys and next cursors; neither is
    fabricated from status IDs. After the first confirmed page, the next
    page_key must equal the previous receipt's explicit next_cursor for
    history, and inside a current new-post batch. A later new-post batch
    receives its own caller-provided starting key and must still pass CAS;
    no API cursor is synthesized. Confirmed scan progress includes transit
    pages with no target mention; coverage fields advance only when this page
    contains a validated target-symbol relation. The page's full member
    roster is persisted in ``xq_reverse_page_member`` with 1..n ordinals in
    the same transaction; identical replay is a no-op that does not rewrite
    the roster. Divergent replay and CAS races fail visibly.
    """
    symbol = _nonempty(symbol, "symbol")
    if lane not in LANES:
        raise ValueError("lane must be 'new' or 'history'")
    batch_id = _nonempty(batch_id, "batch_id")
    page_key = _nonempty(page_key, "page_key")
    fetched_at = _timestamp(fetched_at)
    if next_cursor is not None:
        next_cursor = _nonempty(next_cursor, "next_cursor")
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version < 0:
        raise ValueError("expected_version must be a nonnegative integer")
    first_id, last_id = _validate_page(validated_posts)
    if not isinstance(relations, (list, tuple)) or any(
        not isinstance(relation, xq_reverse_store.PostSymbolInput)
        for relation in relations
    ):
        raise ValueError("relations must be a list of PostSymbolInput values")
    page_post_ids = {post.post_id for post in validated_posts}
    if any(relation.post_id not in page_post_ids for relation in relations):
        raise ValueError("each page relation requires an original on this page")
    digest = _page_digest(validated_posts, relations, next_cursor)
    if conn.in_transaction:
        raise RuntimeError("commit_page requires a connection outside a transaction")
    try:
        conn.execute("BEGIN IMMEDIATE")
        receipt = conn.execute("""
            SELECT payload_hash, confirmed_version
              FROM xq_reverse_page_receipt
             WHERE symbol = ? AND lane = ? AND batch_id = ? AND page_key = ?
        """, (symbol, lane, batch_id, page_key)).fetchone()
        if receipt is not None:
            if receipt[0] != digest:
                raise CheckpointConflictError("page key already confirmed with another payload")
            checkpoint = get_checkpoint(conn, symbol, lane)
            conn.commit()
            return {**checkpoint, "replayed": True, "receipt_version": receipt[1]}

        checkpoint = get_checkpoint(conn, symbol, lane)
        if checkpoint["version"] != expected_version:
            raise CheckpointConflictError(
                f"stale {lane} checkpoint: expected {expected_version}, "
                f"current {checkpoint['version']}")
        if checkpoint["version"] > 0:
            if lane == "history" or checkpoint["batch_id"] == batch_id:
                expected_page_key = checkpoint["next_cursor"]
                if expected_page_key is None or page_key != expected_page_key:
                    raise CheckpointConflictError(
                        "page is not contiguous with the confirmed next_cursor")

        xq_reverse_store.save_posts(
            conn, validated_posts, relations, batch_id, fetched_at)
        # A fetch target is not evidence. Only same-page posts with validated
        # content relations to this symbol can advance evidence coverage.
        target_post_ids = {relation.post_id for relation in relations
                           if relation.symbol == symbol}
        evidenced_ids = [post.post_id for post in validated_posts
                         if post.post_id in target_post_ids]
        coverage_first_id = evidenced_ids[0] if evidenced_ids else None
        coverage_last_id = evidenced_ids[-1] if evidenced_ids else None
        coverage_at = fetched_at if evidenced_ids else None
        version = expected_version + 1
        conn.execute("""
            INSERT INTO xq_reverse_page_receipt
                (symbol, lane, page_key, batch_id, payload_hash, post_count,
                 first_status_id, last_status_id, next_cursor,
                 confirmed_version, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (symbol, lane, page_key, batch_id, digest, len(validated_posts),
              first_id, last_id, next_cursor, version, fetched_at))
        # Persist the whole-page member roster in the same transaction, so
        # multi-post receipts stay exactly attributable on the read side.
        conn.executemany("""
            INSERT INTO xq_reverse_page_member
                (symbol, lane, batch_id, page_key, ordinal, post_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [(symbol, lane, batch_id, page_key, ordinal, post.post_id)
              for ordinal, post in enumerate(validated_posts, start=1)])
        conn.execute("""
            INSERT INTO xq_reverse_checkpoint
                (symbol, lane, version, confirmed_page_key, next_cursor,
                 first_status_id, last_status_id, coverage_started_at,
                 coverage_updated_at, state, batch_id, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?)
            ON CONFLICT(symbol, lane) DO UPDATE SET
                version = excluded.version,
                confirmed_page_key = excluded.confirmed_page_key,
                next_cursor = excluded.next_cursor,
                first_status_id = COALESCE(xq_reverse_checkpoint.first_status_id,
                                           excluded.first_status_id),
                last_status_id = COALESCE(excluded.last_status_id,
                                          xq_reverse_checkpoint.last_status_id),
                coverage_started_at = COALESCE(
                    xq_reverse_checkpoint.coverage_started_at,
                    excluded.coverage_started_at),
                coverage_updated_at = COALESCE(
                    excluded.coverage_updated_at,
                    xq_reverse_checkpoint.coverage_updated_at),
                state = excluded.state,
                batch_id = excluded.batch_id,
                fetched_at = excluded.fetched_at
            WHERE xq_reverse_checkpoint.version = ?
        """, (symbol, lane, version, page_key, next_cursor,
              coverage_first_id, coverage_last_id, coverage_at, coverage_at,
              batch_id, fetched_at, expected_version))
        checkpoint = get_checkpoint(conn, symbol, lane)
        if checkpoint["version"] != version:
            raise CheckpointConflictError("checkpoint CAS failed")
        conn.commit()
        return {**checkpoint, "replayed": False, "receipt_version": version}
    except BaseException:
        conn.rollback()
        raise
