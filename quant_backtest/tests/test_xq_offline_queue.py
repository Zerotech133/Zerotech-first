"""Synthetic-only queue contracts; do not run while safety cleanup is blocked."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Callable

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xq_offline_queue as queue
import xq_reverse_ledger as ledger
import xq_reverse_store as store
from xq_request_gate import RequestGate


@pytest.fixture
def offline(tmp_path: Path) -> tuple[sqlite3.Connection, RequestGate]:
    """Prepare an isolated queue and a single shared fake-send gate."""
    conn = sqlite3.connect(tmp_path / "offline.sqlite3", isolation_level=None)
    queue.init_schema(conn)
    gate = RequestGate(tmp_path / "gate.sqlite3", max_requests=100,
                       window_seconds=3600, min_interval_seconds=0)
    yield conn, gate
    conn.close()


def page(symbol: str, key: str, identifier: str,
         cursor: str | None = None, *, target: bool = True) -> queue.CompletePage:
    """Make an explicitly attested single-original synthetic page."""
    content = f"原帖正文含{symbol}" if target else "不含查询股票的过路页"
    post = store.RawPostInput(identifier, f"https://example.invalid/post/{identifier}",
                              1700000000000, {"id": identifier, "text": content})
    relations = ((store.PostSymbolInput(identifier, symbol, "post_content",
                  {"source_field": "text", "excerpt": symbol}),) if target else ())
    return queue.CompletePage(symbol, key, True, True, True, (identifier,),
                              (post,), relations, cursor)


def send(response: queue.OfflineResponse,
         sent: list[queue.QueueTask]) -> Callable[[queue.QueueTask], queue.OfflineResponse]:
    """Return a fake single-invocation sender with observable call count."""
    def once(item: queue.QueueTask) -> queue.OfflineResponse:
        sent.append(item)
        return response
    return once


def test_round_robin_lanes_transit_and_independent_checkpoints(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """Confirmed transit pages scan but never assert content coverage."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "new-a", "n1")
    queue.enqueue(conn, "SH600000", "history", "history-a", "h1")
    queue.enqueue(conn, "SZ000001", "new", "new-b", "n1")
    seen: list[queue.QueueTask] = []
    assert queue.run_one(conn, gate, send(queue.OfflineResponse(
        200, page("SH600000", "n1", "one", "n2")), seen), 1700000001000).outcome == "confirmed"
    assert queue.run_one(conn, gate, send(queue.OfflineResponse(
        200, page("SH600000", "h1", "two", "h2", target=False)), seen),
        1700000002000).outcome == "confirmed"
    assert queue.run_one(conn, gate, send(queue.OfflineResponse(
        200, page("SZ000001", "n1", "three")), seen), 1700000003000).outcome == "confirmed"
    assert [(item.symbol, item.lane) for item in seen] == [
        ("SH600000", "new"), ("SH600000", "history"), ("SZ000001", "new")]
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 1
    history = ledger.get_checkpoint(conn, "SH600000", "history")
    assert history["version"] == 1 and history["next_cursor"] == "h2"
    assert history["coverage_started_at"] is None and history["first_status_id"] is None
    assert [item.page_key for item in queue.tasks(conn) if item.state == "pending"] == ["n2", "h2"]


def test_403_stops_all_send_and_preserves_other_pending(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A stopped gate does not claim or send the next symbol."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    queue.enqueue(conn, "SZ000001", "history", "b", "first")
    seen: list[queue.QueueTask] = []
    result = queue.run_one(conn, gate, send(queue.OfflineResponse(403), seen), 1700000001000)
    assert result.outcome == "403" and gate.snapshot().stopped
    assert queue.run_one(conn, gate, send(queue.OfflineResponse(200), seen),
                         1700000002000).outcome == "stopped"
    assert len(seen) == 1
    assert [(item.symbol, item.state) for item in queue.tasks(conn)] == [
        ("SZ000001", "pending"), ("SH600000", "paused")]
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 0


@pytest.mark.parametrize("response,expected", [
    (queue.OfflineResponse(200), "partial_page_failed"),
    (queue.OfflineResponse(200, queue.CompletePage(
        "SH600000", "first", False, True, True, ("bad",),
        (store.RawPostInput("bad", "https://example.invalid/bad", 1,
                            {"id": "bad", "text": "SH600000"}),), (), None)), "partial_page_failed"),
    (queue.OfflineResponse(404), "http_404"),
    (queue.OfflineResponse(200, queue.CompletePage(
        "SH600000", "first", True, True, True, (), (), (), None)), "empty"),
])
def test_unverified_empty_and_error_pages_only_record_attempt(
        offline: tuple[sqlite3.Connection, RequestGate],
        response: queue.OfflineResponse, expected: str) -> None:
    """No unconfirmed outcome may create original posts or advance a scan."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "history", "a", "first")
    result = queue.run_one(conn, gate, lambda item: response, 1700000001000)
    assert result.outcome == expected
    assert queue.tasks(conn)[0].state == "paused"
    assert ledger.get_checkpoint(conn, "SH600000", "history")["version"] == 0
    assert conn.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (0,)
    assert conn.execute("SELECT status FROM xq_reverse_page_attempt").fetchone() == (expected,)


def test_send_exception_trips_gate_and_quarantines_claim(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """Classification/transport uncertainty cannot authorize another send."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    queue.enqueue(conn, "SZ000001", "new", "b", "first")
    calls: list[queue.QueueTask] = []
    def broken(item: queue.QueueTask) -> queue.OfflineResponse:
        calls.append(item)
        raise RuntimeError("synthetic transport fault")
    assert queue.run_one(conn, gate, broken, 1700000001000).outcome == "failed"
    assert gate.snapshot().stopped and len(calls) == 1
    assert queue.run_one(conn, gate, broken, 1700000002000).outcome == "stopped"
    assert len(calls) == 1


def test_resume_and_manual_claim_reconciliation(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A crash claim requires human action; review never implicitly sends."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "history", "a", "first")
    claimed = queue._claim(conn)
    assert claimed is not None
    assert queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200),
                         1700000001000).outcome == "idle"
    assert queue.reconcile_claim(conn, "SH600000", "history", "operator", "checked",
                                 acted_at=1700000002000).outcome == "quarantined"
    assert queue.tasks(conn)[0].state == "paused"
    queue.resume_task(conn, "SH600000", "history", "operator", "approved",
                      acted_at=1700000003000)
    assert queue.tasks(conn)[0].state == "pending"


def test_query_binding_and_descending_evidence_fail_closed(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """Matching query text cannot substitute for explicit binding and order proof."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    original = page("SH600000", "first", "one")
    unbound = queue.CompletePage(
        original.query_symbol, original.page_key, True, False, True,
        original.ordered_post_ids, original.posts, original.relations, None)
    assert queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200, unbound),
                         1700000001000).outcome == "partial_page_failed"
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 0
    queue.resume_task(conn, "SH600000", "new", "operator", "synthetic retry",
                      acted_at=1700000002000)
    newer = store.RawPostInput("newer", "https://example.invalid/newer", 200,
                               {"id": "newer", "text": "SH600000"})
    older = store.RawPostInput("older", "https://example.invalid/older", 100,
                               {"id": "older", "text": "SH600000"})
    reversed_page = queue.CompletePage(
        "SH600000", "first", True, True, True, ("older", "newer"),
        (older, newer), (), None)
    assert queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200, reversed_page),
                         1700000002000).outcome == "partial_page_failed"
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 0


def test_duplicate_replay_only_records_attempt(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A previously confirmed page cannot advance another scan version."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    original = page("SH600000", "first", "one", "second")
    ledger.commit_page(conn, "SH600000", "new", "a", "first", original.posts,
                       original.relations, "second", 0, 1700000001000)
    result = queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200, original),
                           1700000002000)
    assert result.outcome == "duplicate"
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 1
    assert queue.tasks(conn)[0].state == "paused"


def test_store_commit_failure_keeps_waterline_and_attempt(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """Atomic post/receipt/scan rollback remains visible as a failed attempt."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "batch", "first")
    conn.execute("""CREATE TRIGGER reject_original BEFORE INSERT ON xq_reverse_raw_post
        BEGIN SELECT RAISE(ABORT, 'synthetic store fault'); END""")
    result = queue.run_one(conn, gate, lambda item: queue.OfflineResponse(
        200, page("SH600000", "first", "post")), 1700000001000)
    assert result.outcome == "partial_page_failed"
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 0
    assert conn.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (0,)
    assert conn.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (0,)
    assert conn.execute("SELECT status FROM xq_reverse_page_attempt").fetchone() == (
        "partial_page_failed",)


def test_cross_page_reverse_time_boundary_must_be_proven(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A locally descending page cannot rewind the global reverse order."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "history", "a", "first")
    first = page("SH600000", "first", "older", "second")
    assert queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200, first),
                         1700000001000).outcome == "confirmed"
    newer = store.RawPostInput("newer", "https://example.invalid/newer", 1700000000001,
                               {"id": "newer", "text": "SH600000"})
    second = queue.CompletePage("SH600000", "second", True, True, True,
                                ("newer",), (newer,), (), None)
    result = queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200, second),
                           1700000002000)
    assert result.outcome == "partial_page_failed"
    assert ledger.get_checkpoint(conn, "SH600000", "history")["version"] == 1
    assert queue.tasks(conn)[0].state == "paused"


def test_new_batch_requires_fresh_id_and_history_no_invented_end(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """An exhausted cursor never proves an actual historical terminal boundary."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "history", "a", "first")
    queue.run_one(conn, gate, lambda item: queue.OfflineResponse(
        200, page("SH600000", "first", "original")), 1700000001000)
    assert queue.tasks(conn)[0].state == "awaiting_cursor"
    with pytest.raises(ValueError, match="history"):
        queue.enqueue(conn, "SH600000", "history", "b", "invented")
    assert queue.tasks(conn)[0].state == "awaiting_cursor"


def test_resume_records_exactly_one_audit_row(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A manual resume leaves one durable audit row bound to its state change."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    assert queue.run_one(conn, gate, lambda item: queue.OfflineResponse(200),
                         1700000001000).outcome == "partial_page_failed"
    assert queue.tasks(conn)[0].state == "paused"
    queue.resume_task(conn, "SH600000", "new", "operator-张三", "复核通过，允许续跑",
                      acted_at=1700000002000)
    assert queue.tasks(conn)[0].state == "pending"
    rows = queue.action_log(conn, "SH600000", "new")
    assert len(rows) == 1 and rows[0]["action"] == "resume"
    assert rows[0]["approved_by"] == "operator-张三"
    assert rows[0]["reason"] == "复核通过，允许续跑"
    assert rows[0]["prior_state"] == "paused"
    assert rows[0]["resulting_state"] == "pending"
    assert rows[0]["acted_at"] == 1700000002000


@pytest.mark.parametrize("cursor,expected_state", [
    ("second", "pending"),
    (None, "awaiting_cursor"),
])
def test_reconcile_recovered_confirmed_writes_audit(
        offline: tuple[sqlite3.Connection, RequestGate],
        cursor: str | None, expected_state: str) -> None:
    """A receipt-backed claim recovery records its final queue state."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "batch", "first")
    assert queue._claim(conn) is not None
    done = page("SH600000", "first", "one", cursor)
    ledger.commit_page(conn, "SH600000", "new", "batch", "first", done.posts,
                       done.relations, cursor, 0, 1700000001000)
    result = queue.reconcile_claim(conn, "SH600000", "new", "operator", "receipt present",
                                   acted_at=1700000002000)
    assert result.outcome == "recovered_confirmed"
    rows = queue.action_log(conn, "SH600000", "new")
    assert len(rows) == 1 and rows[0]["action"] == "reconcile"
    assert rows[0]["prior_state"] == "claimed"
    assert rows[0]["resulting_state"] == expected_state
    assert rows[0]["approved_by"] == "operator"
    assert rows[0]["reason"] == "receipt present"
    assert rows[0]["acted_at"] == 1700000002000


def test_reconcile_quarantined_writes_audit(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A receipt-less claim quarantine also records who decided it."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "history", "a", "first")
    assert queue._claim(conn) is not None
    result = queue.reconcile_claim(conn, "SH600000", "history", "operator", "no receipt",
                                   acted_at=1700000001000)
    assert result.outcome == "quarantined"
    rows = queue.action_log(conn, "SH600000", "history")
    assert len(rows) == 1 and rows[0]["action"] == "reconcile"
    assert rows[0]["prior_state"] == "claimed"
    assert rows[0]["resulting_state"] == "paused"
    assert rows[0]["approved_by"] == "operator"
    assert rows[0]["reason"] == "no receipt"


def test_pause_task_audits_pending_and_rejects_other_states(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """Manual pause accepts only pending; other states fail closed on failure."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    queue.pause_task(conn, "SH600000", "new", "operator", "manual hold",
                     acted_at=1700000001000)
    assert queue.tasks(conn)[0].state == "paused"
    rows = queue.action_log(conn, "SH600000", "new")
    assert len(rows) == 1 and rows[0]["action"] == "pause"
    assert rows[0]["prior_state"] == "pending"
    assert rows[0]["resulting_state"] == "paused"
    assert rows[0]["approved_by"] == "operator"
    assert rows[0]["reason"] == "manual hold"
    with pytest.raises(ValueError):
        queue.pause_task(conn, "SH600000", "new", "operator", "again",
                         acted_at=1700000002000)
    queue.enqueue(conn, "SZ000001", "new", "b", "first")
    claimed = queue._claim(conn)
    assert claimed is not None and claimed.symbol == "SZ000001"
    with pytest.raises(ValueError):
        queue.pause_task(conn, "SZ000001", "new", "operator", "interrupt",
                         acted_at=1700000003000)
    queue.enqueue(conn, "SH600000", "history", "c", "first")
    assert queue.run_one(conn, gate, lambda item: queue.OfflineResponse(
        200, page("SH600000", "first", "one")), 1700000004000).outcome == "confirmed"
    assert queue.tasks(conn)[2].state == "awaiting_cursor"
    with pytest.raises(ValueError):
        queue.pause_task(conn, "SH600000", "history", "operator", "interrupt",
                         acted_at=1700000005000)
    assert queue.action_log(conn, "SH600000", "new") == rows
    assert queue.action_log(conn, "SZ000001", "new") == []
    assert queue.action_log(conn, "SH600000", "history") == []


def test_invalid_acted_at_fails_closed_without_state_change_or_audit(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A rejected timestamp leaves state untouched and writes zero audit rows."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    queue.pause_task(conn, "SH600000", "new", "operator", "hold",
                     acted_at=1700000001000)
    baseline = conn.execute("SELECT COUNT(*) FROM xq_offline_action_log").fetchone()
    for bad in (-1, True, False):
        with pytest.raises(ValueError):
            queue.resume_task(conn, "SH600000", "new", "operator", "retry", bad)
        with pytest.raises(ValueError):
            queue.pause_task(conn, "SH600000", "new", "operator", "hold", bad)
    assert queue.tasks(conn)[0].state == "paused"
    assert conn.execute("SELECT COUNT(*) FROM xq_offline_action_log").fetchone() == baseline
    queue.enqueue(conn, "SZ000001", "new", "b", "first")
    assert queue._claim(conn) is not None
    with pytest.raises(ValueError):
        queue.reconcile_claim(conn, "SZ000001", "new", "operator", "resolve", -5)
    assert queue.tasks(conn)[1].state == "claimed"
    assert conn.execute("SELECT COUNT(*) FROM xq_offline_action_log").fetchone() == baseline


def test_action_log_is_ordered_scoped_and_read_only(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """Audit reads stay scoped to one symbol/lane and never mutate anything."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    queue.pause_task(conn, "SH600000", "new", "op1", "r1", acted_at=1700000001000)
    queue.resume_task(conn, "SH600000", "new", "op2", "r2", acted_at=1700000002000)
    queue.pause_task(conn, "SH600000", "new", "op3", "r3", acted_at=1700000003000)
    queue.enqueue(conn, "SZ000001", "history", "b", "first")
    queue.pause_task(conn, "SZ000001", "history", "op4", "r4", acted_at=1700000004000)
    rows = queue.action_log(conn, "SH600000", "new")
    assert [row["action"] for row in rows] == ["pause", "resume", "pause"]
    assert [row["approved_by"] for row in rows] == ["op1", "op2", "op3"]
    ids = [int(row["action_id"]) for row in rows]
    assert ids == sorted(ids) and all(a < b for a, b in zip(ids, ids[1:]))
    assert set(rows[0]) == {"action_id", "action", "approved_by", "reason",
                            "prior_state", "resulting_state", "acted_at"}
    other = queue.action_log(conn, "SZ000001", "history")
    assert [row["reason"] for row in other] == ["r4"]
    assert queue.action_log(conn, "SH600000", "history") == []
    assert queue.action_log(conn, "SZ000001", "new") == []
    total = conn.execute("SELECT COUNT(*) FROM xq_offline_action_log").fetchone()
    assert queue.action_log(conn, "SH600000", "new") == rows
    assert queue.action_log(conn, "SZ000001", "history") == other
    assert conn.execute("SELECT COUNT(*) FROM xq_offline_action_log").fetchone() == total
    assert total == (4,)


def test_resume_and_reconcile_require_acted_at(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """The legacy five-argument call must raise instead of silently succeeding."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "history", "a", "first")
    queue.pause_task(conn, "SH600000", "history", "operator", "hold",
                     acted_at=1700000001000)
    with pytest.raises(TypeError):
        queue.resume_task(conn, "SH600000", "history", "operator", "approved")
    with pytest.raises(TypeError):
        queue.reconcile_claim(conn, "SH600000", "history", "operator", "checked")
    assert queue.tasks(conn)[0].state == "paused"
    assert len(queue.action_log(conn, "SH600000", "history")) == 1


def test_http_200_with_failure_reason_records_failed_attempt(
        offline: tuple[sqlite3.Connection, RequestGate]) -> None:
    """A 200 response carrying a failure reason is classified failed, never confirmed."""
    conn, gate = offline
    queue.enqueue(conn, "SH600000", "new", "a", "first")
    result = queue.run_one(
        conn, gate,
        lambda item: queue.OfflineResponse(200, page("SH600000", "first", "one"),
                                           "upstream flagged incomplete content"),
        1700000001000)
    assert result.outcome == "failed"
    assert result.detail == "upstream flagged incomplete content"
    assert not gate.snapshot().stopped
    assert queue.tasks(conn)[0].state == "paused"
    assert ledger.get_checkpoint(conn, "SH600000", "new")["version"] == 0
    assert conn.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (0,)
    assert conn.execute(
        "SELECT status FROM xq_reverse_page_attempt").fetchone() == ("failed",)
