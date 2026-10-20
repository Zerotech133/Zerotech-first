"""Offline per-symbol new/history work queue over synthetic, explicitly verified pages.

This module defines no Xueqiu endpoint, HTTP client, retry, or crawler adapter.
Each caller-provided send_once callback MUST perform at most one physical request,
without hidden retries, probes, redirects, or subsidiary requests. The queue cannot
inspect the callback's external effects. Every invocation passes through the same
persistent RequestGate.execute_once; this is not a production integration or G0 proof.
Use one SQLite connection per worker and a shared queue database and gate database.
An interrupted claimed task is NEVER automatically retried: reconcile it manually.
Manual pause, resume and reconciliation write one xq_offline_action_log audit row
in the same transaction as their state change, so neither side survives alone.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Callable, Literal, cast
from uuid import uuid4

try:
    from . import xq_reverse_ledger as ledger
    from . import xq_reverse_store as store
    from .xq_request_gate import GateDenied, RequestGate
except ImportError:
    import xq_reverse_ledger as ledger
    import xq_reverse_store as store
    from xq_request_gate import GateDenied, RequestGate


Lane = Literal["new", "history"]
TaskState = Literal["pending", "claimed", "paused", "awaiting_cursor"]
ActionKind = Literal["resume", "reconcile", "pause"]


@dataclass(frozen=True)
class QueueTask:
    """One explicitly scheduled page for a symbol and an independent lane."""

    symbol: str
    lane: Lane
    batch_id: str
    page_key: str
    state: TaskState
    sequence: int
    claim_id: str | None


@dataclass(frozen=True)
class CompletePage:
    """Caller-attested full page; assertions alone do not prove a real API.

    ordered_post_ids is the response's complete ordered ID sequence. Comparing it
    to posts, and independently checking timestamps, rejects omissions/reordering.
    query_symbol binds the response to the scheduled stock independently of any
    content relations; a transit page may have no mention of that stock.
    """

    query_symbol: str
    page_key: str
    complete: bool
    query_binding_verified: bool
    descending_time_verified: bool
    ordered_post_ids: tuple[str, ...]
    posts: tuple[store.RawPostInput, ...]
    relations: tuple[store.PostSymbolInput, ...]
    next_cursor: str | None


@dataclass(frozen=True)
class OfflineResponse:
    """One fake sender response; status is classified inside the shared gate."""

    status_code: int
    page: CompletePage | None = None
    failure_reason: str | None = None


@dataclass(frozen=True)
class StepResult:
    """Result of processing at most one task and at most one gated send."""

    outcome: str
    task: QueueTask | None
    detail: str = ""


@dataclass(frozen=True)
class _ActionAudit:
    """Caller-declared manual action; persisted atomically with its state change."""

    action: ActionKind
    approved_by: str
    reason: str
    acted_at: int


def _text(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a nonempty string")
    return value


def _time(value: int, field: str = "fetched_at") -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a nonnegative Unix millisecond integer")
    return value


def _lane(value: str) -> Lane:
    if value not in ledger.LANES:
        raise ValueError("lane must be 'new' or 'history'")
    return cast(Lane, value)


def _outside_transaction(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("queue operation requires a connection outside a transaction")


def _record_action(conn: sqlite3.Connection, symbol: str, lane: Lane,
                   audit: _ActionAudit, prior_state: str,
                   resulting_state: str) -> None:
    """Append one audit row inside the caller's already-open transaction.

    The audit row never commits alone: it is written in the same BEGIN
    IMMEDIATE block as the queue state change, so both persist or both roll
    back (closed on failure). Recording is not an approval mechanism by
    itself; approved_by remains the caller's declaration of who approved.
    """
    conn.execute(
        "INSERT INTO xq_offline_action_log "
        "(symbol,lane,action,approved_by,reason,prior_state,resulting_state,acted_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (symbol, lane, audit.action, audit.approved_by, audit.reason,
         prior_state, resulting_state, audit.acted_at))


def init_schema(conn: sqlite3.Connection) -> None:
    """Initialize isolated offline queue, action audit log, and offline stores."""
    _outside_transaction(conn)
    ledger.init_schema(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        store.init_schema(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS xq_offline_queue (
            symbol TEXT NOT NULL, lane TEXT NOT NULL CHECK(lane IN ('new','history')),
            batch_id TEXT NOT NULL, page_key TEXT NOT NULL,
            state TEXT NOT NULL CHECK(state IN
                ('pending','claimed','paused','awaiting_cursor')),
            sequence INTEGER NOT NULL, claim_id TEXT,
            PRIMARY KEY(symbol,lane),
            CHECK ((state='claimed') = (claim_id IS NOT NULL))
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS xq_offline_sequence (
            id INTEGER PRIMARY KEY CHECK(id=1), last_value INTEGER NOT NULL
        )""")
        conn.execute("INSERT OR IGNORE INTO xq_offline_sequence VALUES (1,0)")
        conn.execute("""CREATE TABLE IF NOT EXISTS xq_offline_action_log (
            action_id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, lane TEXT NOT NULL CHECK(lane IN ('new','history')),
            action TEXT NOT NULL CHECK(action IN ('resume','reconcile','pause')),
            approved_by TEXT NOT NULL, reason TEXT NOT NULL,
            prior_state TEXT NOT NULL, resulting_state TEXT NOT NULL,
            acted_at INTEGER NOT NULL
        )""")
        conn.execute("""CREATE INDEX IF NOT EXISTS xq_offline_ready
            ON xq_offline_queue(state,sequence)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS xq_offline_action_scope
            ON xq_offline_action_log(symbol,lane)""")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _next_sequence(conn: sqlite3.Connection) -> int:
    conn.execute("UPDATE xq_offline_sequence SET last_value=last_value+1 WHERE id=1")
    row = conn.execute("SELECT last_value FROM xq_offline_sequence WHERE id=1").fetchone()
    if row is None:
        raise RuntimeError("offline queue schema is not initialized")
    return int(row[0])


def _task(row: tuple[object, ...]) -> QueueTask:
    state = str(row[4])
    if state not in ("pending", "claimed", "paused", "awaiting_cursor"):
        raise ValueError("invalid stored queue state")
    claim_id = row[6]
    if claim_id is not None and not isinstance(claim_id, str):
        raise ValueError("invalid stored queue claim")
    return QueueTask(str(row[0]), _lane(str(row[1])), str(row[2]),
                     str(row[3]), cast(TaskState, state), int(row[5]), claim_id)


def tasks(conn: sqlite3.Connection) -> list[QueueTask]:
    """Read all durable tasks, including paused and unresolved claimed work."""
    return [_task(row) for row in conn.execute(
        "SELECT symbol,lane,batch_id,page_key,state,sequence,claim_id "
        "FROM xq_offline_queue ORDER BY sequence,symbol,lane"
    ).fetchall()]


def enqueue(conn: sqlite3.Connection, symbol: str, lane: Lane,
            batch_id: str, page_key: str) -> QueueTask:
    """Add a new lane or explicitly start a new batch after a cursorless page.

    Existing pending/claimed work cannot be replaced. A paused task is resumed
    with resume_task rather than silently changing its page. An awaiting_cursor
    task can start a fresh new-post batch; history cannot infer completion.
    """
    _text(symbol, "symbol")
    _lane(lane)
    _text(batch_id, "batch_id")
    _text(page_key, "page_key")
    _outside_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT state FROM xq_offline_queue WHERE symbol=? AND lane=?",
                           (symbol, lane)).fetchone()
        if row is not None and row[0] != "awaiting_cursor":
            raise ValueError("existing task must be resolved before enqueue")
        checkpoint = ledger.get_checkpoint(conn, symbol, lane)
        if row is not None:
            if lane == "history":
                raise ValueError("history has no confirmed next cursor; cannot infer continuation")
            if checkpoint["batch_id"] == batch_id:
                raise ValueError("a new-post batch must have a fresh batch_id")
            conn.execute("DELETE FROM xq_offline_queue WHERE symbol=? AND lane=?",
                         (symbol, lane))
        if lane == "history" and checkpoint["version"] > 0:
            raise ValueError("history continuation is already scheduled or lacks a cursor")
        sequence = _next_sequence(conn)
        conn.execute("INSERT INTO xq_offline_queue VALUES (?,?,?,?,?,?,NULL)",
                     (symbol, lane, batch_id, page_key, "pending", sequence))
        conn.commit()
        return QueueTask(symbol, lane, batch_id, page_key, "pending", sequence, None)
    except BaseException:
        conn.rollback()
        raise


def resume_task(conn: sqlite3.Connection, symbol: str, lane: Lane,
                approved_by: str, reason: str, acted_at: int) -> None:
    """Manually resume a paused task; gate reset is a separate human action.

    The audit row and the paused→pending state change commit in the same
    transaction (closed on failure): either both persist or neither does, so
    no unaudited resume and no orphan audit row can survive a partial error.
    Recording is not an approval mechanism by itself; approved_by is the
    caller's declaration of who approved the resume. acted_at is the explicit
    action timestamp as a nonnegative Unix millisecond integer.
    """
    for field, value in (("symbol", symbol), ("approved_by", approved_by),
                         ("reason", reason)):
        _text(value, field)
    _lane(lane)
    _time(acted_at, "acted_at")
    _outside_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sequence = _next_sequence(conn)
        changed = conn.execute(
            "UPDATE xq_offline_queue SET state='pending',sequence=? "
            "WHERE symbol=? AND lane=? AND state='paused'",
            (sequence, symbol, lane)).rowcount
        if changed != 1:
            raise ValueError("only a paused task may be resumed")
        _record_action(conn, symbol, lane,
                       _ActionAudit("resume", approved_by, reason, acted_at),
                       "paused", "pending")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def pause_task(conn: sqlite3.Connection, symbol: str, lane: Lane,
               approved_by: str, reason: str, acted_at: int) -> None:
    """Manually pause a pending task; only pending→paused is permitted.

    Pausing a claimed, paused, or awaiting_cursor task raises ValueError and
    leaves every row untouched (closed on failure). The audit row and the
    pending→paused state change commit in the same transaction (closed on
    failure), so no unaudited pause and no orphan audit row can survive a
    partial error. Recording is not an approval mechanism by itself;
    approved_by is the caller's declaration of who approved the pause. acted_at
    is the explicit action timestamp as a nonnegative Unix millisecond integer.
    """
    for field, value in (("symbol", symbol), ("approved_by", approved_by),
                         ("reason", reason)):
        _text(value, field)
    _lane(lane)
    _time(acted_at, "acted_at")
    _outside_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE xq_offline_queue SET state='paused' "
            "WHERE symbol=? AND lane=? AND state='pending'",
            (symbol, lane)).rowcount
        if changed != 1:
            raise ValueError("only a pending task may be paused")
        _record_action(conn, symbol, lane,
                       _ActionAudit("pause", approved_by, reason, acted_at),
                       "pending", "paused")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def action_log(conn: sqlite3.Connection, symbol: str, lane: Lane) -> list[dict[str, object]]:
    """Read one symbol/lane audit trail ordered by ascending action_id.

    Read-only: performs zero writes and never opens a transaction, so the
    durable manual-action history (pause, resume, reconcile) can be inspected
    freely. Each row carries action_id, action, approved_by, reason,
    prior_state, resulting_state, and acted_at. An audit row documents the
    caller's declaration; it never substitutes for the approval itself.
    """
    _text(symbol, "symbol")
    _lane(lane)
    rows = conn.execute(
        "SELECT action_id,action,approved_by,reason,prior_state,resulting_state,acted_at "
        "FROM xq_offline_action_log WHERE symbol=? AND lane=? ORDER BY action_id",
        (symbol, lane)).fetchall()
    return [{"action_id": int(row[0]), "action": str(row[1]),
             "approved_by": str(row[2]), "reason": str(row[3]),
             "prior_state": str(row[4]), "resulting_state": str(row[5]),
             "acted_at": int(row[6])} for row in rows]


def _claim(conn: sqlite3.Connection) -> QueueTask | None:
    _outside_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT symbol,lane,batch_id,page_key,state,sequence,claim_id "
            "FROM xq_offline_queue WHERE state='pending' ORDER BY sequence LIMIT 1"
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        item = _task(row)
        claim_id = str(uuid4())
        conn.execute("UPDATE xq_offline_queue SET state='claimed',claim_id=? "
                     "WHERE symbol=? AND lane=? AND state='pending'",
                     (claim_id, item.symbol, item.lane))
        conn.commit()
        return QueueTask(item.symbol, item.lane, item.batch_id, item.page_key,
                         "claimed", item.sequence, claim_id)
    except BaseException:
        conn.rollback()
        raise


def _finish(conn: sqlite3.Connection, item: QueueTask, state: TaskState,
            next_cursor: str | None = None,
            audit: _ActionAudit | None = None) -> None:
    """Release one claim; optionally record a manual action in the same transaction.

    When audit is provided its row is written in the same BEGIN IMMEDIATE block
    as the claim resolution (closed on failure): either both persist or neither
    does. Prior state is always 'claimed' here because the guarded update only
    matches a live claim.
    """
    _outside_transaction(conn)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sequence = _next_sequence(conn)
        if state == "awaiting_cursor":
            cursor = item.page_key
        elif state == "pending" and next_cursor is not None:
            cursor = next_cursor
        else:
            cursor = item.page_key
        changed = conn.execute(
            "UPDATE xq_offline_queue SET state=?,page_key=?,sequence=?,claim_id=NULL "
            "WHERE symbol=? AND lane=? AND claim_id=? AND state='claimed'",
            (state, cursor, sequence, item.symbol, item.lane, item.claim_id)).rowcount
        if changed != 1:
            raise RuntimeError("queue claim changed; task remains unresolved")
        if audit is not None:
            _record_action(conn, item.symbol, item.lane, audit, "claimed", state)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _validate_page(page: CompletePage, item: QueueTask) -> None:
    if not isinstance(page, CompletePage):
        raise ValueError("response needs an explicitly attested complete page")
    if (page.complete is not True or page.query_binding_verified is not True
            or page.descending_time_verified is not True
            or page.query_symbol != item.symbol or page.page_key != item.page_key):
        raise ValueError("page completeness, ordering, query binding or key unverified")
    if not isinstance(page.posts, tuple) or not page.posts:
        raise ValueError("empty/partial pages cannot advance a checkpoint")
    if not isinstance(page.relations, tuple) or not isinstance(page.ordered_post_ids, tuple):
        raise ValueError("page fields must be complete immutable sequences")
    if any(not isinstance(post, store.RawPostInput) for post in page.posts):
        raise ValueError("page posts must be original post inputs")
    ids = tuple(post.post_id for post in page.posts)
    if (page.ordered_post_ids != ids or
            any(not isinstance(identifier, str) or not identifier.strip()
                for identifier in ids) or len(set(ids)) != len(ids)):
        raise ValueError("ordered post IDs must exactly match unique original posts")
    times = tuple(post.created_at_ms for post in page.posts)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0
           for value in times) or any(a < b for a, b in zip(times, times[1:])):
        raise ValueError("original post times must be descending")
    if page.next_cursor is not None:
        _text(page.next_cursor, "next_cursor")
        if page.next_cursor == item.page_key:
            raise ValueError("next cursor must not repeat current page")


def _attempt(conn: sqlite3.Connection, item: QueueTask, status: str,
             fetched_at: int, detail: str) -> StepResult:
    # Persist the failure first. If this write fails, leave claim unresolved;
    # never release a task after silently dropping its attempt receipt.
    ledger.record_page_attempt(conn, item.symbol, item.lane, item.batch_id,
                               item.page_key, status, fetched_at, detail[:1024])
    _finish(conn, item, "paused")
    return StepResult(status, item, detail)


def run_one(conn: sqlite3.Connection, gate: RequestGate,
            send_once: Callable[[QueueTask], OfflineResponse],
            fetched_at: int) -> StepResult:
    """Fairly claim one task; execute exactly one callback through the gate.

    Callback must do at most one physical request. This offline adapter makes no
    network calls itself. Unclassified responses and send exceptions are tripped
    by RequestGate; neither outcome may confirm a page. Every unconfirmed result
    pauses its task pending explicit review, while other tasks retain their rows.
    """
    _time(fetched_at)
    if not isinstance(gate, RequestGate) or not callable(send_once):
        raise TypeError("gate must be RequestGate and send_once must be callable")
    _outside_transaction(conn)
    if gate.snapshot().stopped:
        return StepResult("stopped", None, "request gate requires manual reset")
    item = _claim(conn)
    if item is None:
        return StepResult("idle", None)
    try:
        response = gate.execute_once(
            lambda: send_once(item),
            status_code=lambda result: result.status_code,
            failure_reason=lambda result: result.failure_reason,
        )
    except GateDenied as error:
        reason = str(error)
        try:
            stopped = gate.snapshot().stopped
        except Exception:
            stopped = True
        status = "stopped" if stopped else "page_limit" if "budget" in reason else "failed"
        return _attempt(conn, item, status, fetched_at, reason)
    except Exception as error:
        return _attempt(conn, item, "failed", fetched_at,
                        f"send or response classification failed: {type(error).__name__}")
    if response.status_code == 403:
        return _attempt(conn, item, "403", fetched_at, "request gate stopped on HTTP 403")
    if response.status_code != 200:
        status = "http_404" if response.status_code == 404 else (
            "token_invalid" if response.status_code == 401 else "failed")
        return _attempt(conn, item, status, fetched_at,
                        f"HTTP {response.status_code}: {response.failure_reason or ''}")
    if response.failure_reason is not None:
        return _attempt(conn, item, "failed", fetched_at, response.failure_reason)
    try:
        _validate_page(response.page, item)
    except (TypeError, ValueError) as error:
        status = ("empty" if isinstance(response.page, CompletePage)
                  and isinstance(response.page.posts, tuple) and not response.page.posts
                  else "partial_page_failed")
        return _attempt(conn, item, status, fetched_at, str(error))
    page = response.page
    if page is None:
        raise RuntimeError("validated page unexpectedly absent")
    checkpoint = ledger.get_checkpoint(conn, item.symbol, item.lane)
    if (checkpoint["version"] > 0 and
            (item.lane == "history" or checkpoint["batch_id"] == item.batch_id)
            and not (checkpoint["batch_id"] == item.batch_id
                     and checkpoint["confirmed_page_key"] == item.page_key)):
        preceding = conn.execute(
            "SELECT p.created_at_ms FROM xq_reverse_page_receipt AS r "
            "JOIN xq_reverse_raw_post_version AS p "
            "ON p.post_id=r.last_status_id AND p.batch_id=r.batch_id "
            "WHERE r.symbol=? AND r.lane=? AND r.batch_id=? AND r.page_key=?",
            (item.symbol, item.lane, checkpoint["batch_id"],
             checkpoint["confirmed_page_key"])).fetchone()
        if preceding is None or page.posts[0].created_at_ms > preceding[0]:
            return _attempt(conn, item, "partial_page_failed", fetched_at,
                            "cross-page descending time boundary cannot be verified")
    try:
        result = ledger.commit_page(conn, item.symbol, item.lane, item.batch_id,
                                    item.page_key, page.posts, page.relations,
                                    page.next_cursor, checkpoint["version"], fetched_at)
    except ledger.CheckpointConflictError as error:
        return _attempt(conn, item, "duplicate", fetched_at, str(error))
    except Exception as error:
        return _attempt(conn, item, "partial_page_failed", fetched_at,
                        f"commit failed: {type(error).__name__}: {error}")
    if result["replayed"]:
        return _attempt(conn, item, "duplicate", fetched_at, "already confirmed page")
    # A failure to finish the queue row leaves a claim for manual reconciliation;
    # the independently committed original+checkpoint can never be rolled back.
    _finish(conn, item, "pending" if page.next_cursor is not None else "awaiting_cursor",
            page.next_cursor)
    return StepResult("confirmed", item)


def reconcile_claim(conn: sqlite3.Connection, symbol: str, lane: Lane,
                    approved_by: str, reason: str, acted_at: int) -> StepResult:
    """Manually resolve a crashed claim without silently repeating its send.

    If its receipt/checkpoint is present, recover its confirmed next cursor;
    otherwise quarantine it as paused. Never equate absent cursor with history end.
    Human review and separate gate reconciliation/reset precede future requests.

    Both outcomes write one audit row in the same transaction as the resulting
    state change (closed on failure): recovered_confirmed records the final
    queue state pending or awaiting_cursor, quarantined records paused. The
    audit row documents the caller's declaration of approved_by/reason; it is
    not an approval mechanism by itself. acted_at is the explicit action
    timestamp as a nonnegative Unix millisecond integer.
    """
    for field, value in (("symbol", symbol), ("approved_by", approved_by),
                         ("reason", reason)):
        _text(value, field)
    _lane(lane)
    _time(acted_at, "acted_at")
    _outside_transaction(conn)
    audit = _ActionAudit("reconcile", approved_by, reason, acted_at)
    row = conn.execute("SELECT symbol,lane,batch_id,page_key,state,sequence,claim_id "
                       "FROM xq_offline_queue WHERE symbol=? AND lane=?",
                       (symbol, lane)).fetchone()
    if row is None or row[4] != "claimed":
        raise ValueError("task has no unresolved claim")
    item = _task(row)
    receipt = conn.execute(
        "SELECT next_cursor FROM xq_reverse_page_receipt "
        "WHERE symbol=? AND lane=? AND batch_id=? AND page_key=?",
        (symbol, lane, item.batch_id, item.page_key)).fetchone()
    if receipt is not None:
        checkpoint = ledger.get_checkpoint(conn, symbol, lane)
        if checkpoint["batch_id"] != item.batch_id or checkpoint["confirmed_page_key"] != item.page_key:
            raise RuntimeError("confirmed checkpoint moved; manual ledger review required")
        cursor = receipt[0]
        if cursor != checkpoint["next_cursor"]:
            raise RuntimeError("receipt/checkpoint cursor mismatch; manual ledger review required")
        _finish(conn, item, "pending" if cursor is not None else "awaiting_cursor", cursor, audit)
        return StepResult("recovered_confirmed", item)
    checkpoint = ledger.get_checkpoint(conn, symbol, lane)
    if (checkpoint["batch_id"] == item.batch_id and
            checkpoint["confirmed_page_key"] == item.page_key):
        raise RuntimeError("checkpoint exists without page receipt; manual ledger review required")
    _finish(conn, item, "paused", None, audit)
    return StepResult("quarantined", item, reason)
