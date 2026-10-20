"""Purely offline tests of per-stock checkpoint and original-post atomicity."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import xq_reverse_ledger as ledger
import xq_reverse_store as store


@pytest.fixture
def db(tmp_path: Path) -> sqlite3.Connection:
    """Prepare a disposable SQLite connection with both M1 and M2 schemas."""
    connection = sqlite3.connect(tmp_path / "reverse.sqlite3")
    ledger.init_schema(connection)
    connection.execute("BEGIN")
    store.init_schema(connection)
    connection.commit()
    yield connection
    connection.close()


def _post(identifier: str) -> store.RawPostInput:
    return store.RawPostInput(
        post_id=identifier,
        original_url=f"https://xueqiu.com/123/{identifier}",
        created_at_ms=1700000000000,
        raw_json={"id": identifier, "text": "原帖正文含SH600000"},
    )


def _commit(conn: sqlite3.Connection, *, symbol: str = "SH600000",
            lane: str = "new", batch_id: str = "batch-a", page_key: str = "first",
            post_id: str = "post-1", next_cursor: str | None = "second",
            expected_version: int = 0,
            relations: list[store.PostSymbolInput] | None = None) -> dict[str, Any]:
    return ledger.commit_page(
        conn, symbol, lane, batch_id, page_key, [_post(post_id)],
        relations if relations is not None else [], next_cursor,
        expected_version, 1700000001000)


def test_atomically_saves_real_originals_and_only_selected_lane(
        db: sqlite3.Connection) -> None:
    relation = store.PostSymbolInput(
        "post-1", "SH600000", "post_content",
        {"source_field": "text", "excerpt": "SH600000"})
    result = _commit(db, relations=[relation])
    assert result["version"] == 1
    assert result["next_cursor"] == "second"
    assert result["first_status_id"] == "post-1"
    assert result["coverage_started_at"] == 1700000001000
    assert db.execute("SELECT post_id FROM xq_reverse_raw_post").fetchall() == [("post-1",)]
    assert db.execute("SELECT symbol FROM xq_reverse_post_symbol").fetchall() == [("SH600000",)]
    assert ledger.get_checkpoint(db, "SH600000", "history")["version"] == 0
    assert _commit(db, lane="history", post_id="old-1")["version"] == 1
    assert ledger.get_checkpoint(db, "SH600000", "history")["coverage_started_at"] is None
    assert ledger.get_checkpoint(db, "SH600000", "new")["version"] == 1


def test_successive_pages_need_explicit_continuity_and_preserve_coverage(
        db: sqlite3.Connection) -> None:
    _commit(db)
    with pytest.raises(ledger.CheckpointConflictError, match="contiguous"):
        _commit(db, page_key="invented", post_id="post-2", expected_version=1)
    result = _commit(db, page_key="second", post_id="post-2",
                     next_cursor=None, expected_version=1)
    assert result["version"] == 2
    assert result["first_status_id"] is None
    assert result["last_status_id"] is None
    assert result["coverage_started_at"] is None
    assert result["coverage_updated_at"] is None
    with pytest.raises(ledger.CheckpointConflictError, match="contiguous"):
        _commit(db, page_key="third", post_id="post-3", expected_version=2)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (2,)


def test_transit_page_advances_scan_but_not_target_evidence(
        db: sqlite3.Connection) -> None:
    """A valid original mentioning another stock is not target coverage."""
    other = store.RawPostInput(
        "post-other", "https://xueqiu.com/123/post-other", 1700000000000,
        {"id": "post-other", "text": "原帖只谈SZ000001"})
    other_relation = store.PostSymbolInput(
        "post-other", "SZ000001", "post_content",
        {"source_field": "text", "excerpt": "SZ000001"})
    transit = ledger.commit_page(
        db, "SH600000", "history", "batch-a", "first", [other],
        [other_relation], "second", 0, 1700000001000)
    assert transit["version"] == 1
    assert transit["confirmed_page_key"] == "first"
    assert transit["next_cursor"] == "second"
    assert transit["state"] == "confirmed"
    assert transit["first_status_id"] is None
    assert transit["last_status_id"] is None
    assert transit["coverage_started_at"] is None
    assert transit["coverage_updated_at"] is None
    assert db.execute(
        "SELECT first_status_id, last_status_id FROM xq_reverse_page_receipt"
    ).fetchone() == ("post-other", "post-other")
    assert db.execute(
        "SELECT symbol FROM xq_reverse_post_symbol"
    ).fetchone() == ("SZ000001",)
    assert ledger.commit_page(
        db, "SH600000", "history", "batch-a", "first", [other],
        [other_relation], "second", 0, 1700000001000)["replayed"] is True

    target_relation = store.PostSymbolInput(
        "post-target", "SH600000", "post_content",
        {"source_field": "text", "excerpt": "SH600000"})
    covered = ledger.commit_page(
        db, "SH600000", "history", "batch-a", "second", [_post("post-target")],
        [target_relation], "third", 1, 1700000002000)
    assert covered["version"] == 2
    assert covered["first_status_id"] == "post-target"
    assert covered["last_status_id"] == "post-target"
    assert covered["coverage_started_at"] == 1700000002000
    assert covered["coverage_updated_at"] == 1700000002000
    passing = ledger.commit_page(
        db, "SH600000", "history", "batch-a", "third", [other],
        [other_relation], None, 2, 1700000003000)
    assert passing["version"] == 3
    assert passing["confirmed_page_key"] == "third"
    assert passing["last_status_id"] == "post-target"
    assert passing["coverage_updated_at"] == 1700000002000
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (3,)


def test_first_target_evidence_stays_fixed_after_later_target_page(
        db: sqlite3.Connection) -> None:
    """New target evidence updates the last ID, not the first evidence ID."""
    first_relation = store.PostSymbolInput(
        "first-target", "SH600000", "post_content",
        {"source_field": "text", "excerpt": "SH600000"})
    first = ledger.commit_page(
        db, "SH600000", "history", "batch-a", "first",
        [_post("first-target")], [first_relation], "second", 0, 1700000001000)
    second_relation = store.PostSymbolInput(
        "second-target", "SH600000", "post_content",
        {"source_field": "text", "excerpt": "SH600000"})
    second = ledger.commit_page(
        db, "SH600000", "history", "batch-a", "second",
        [_post("second-target")], [second_relation], None, 1, 1700000002000)
    assert first["first_status_id"] == "first-target"
    assert second["first_status_id"] == "first-target"
    assert second["last_status_id"] == "second-target"
    assert second["coverage_started_at"] == 1700000001000
    assert second["coverage_updated_at"] == 1700000002000


def test_old_original_relation_cannot_relabel_unrelated_page_as_covered(
        db: sqlite3.Connection) -> None:
    """Relations from a prior page never prove coverage of the current scan page."""
    target_relation = store.PostSymbolInput(
        "post-1", "SH600000", "post_content",
        {"source_field": "text", "excerpt": "SH600000"})
    _commit(db, lane="history", relations=[target_relation])
    with pytest.raises(ValueError, match="on this page"):
        ledger.commit_page(
            db, "SH600000", "history", "batch-a", "second", [_post("post-2")],
            [target_relation], None, 1, 1700000002000)
    assert ledger.get_checkpoint(db, "SH600000", "history")["version"] == 1
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (1,)


def test_mixed_page_coverage_uses_only_target_evidenced_originals(
        db: sqlite3.Connection) -> None:
    """First and last evidence IDs exclude unrelated originals on one page."""
    posts = [
        store.RawPostInput(
            identifier, f"https://xueqiu.com/123/{identifier}", 1700000000000,
            {"id": identifier, "text": text})
        for identifier, text in (
            ("unrelated-a", "只谈SZ000001"),
            ("target-a", "谈SH600000"),
            ("unrelated-b", "仍只谈SZ000001"),
            ("target-b", "再谈SH600000"),
            ("unrelated-c", "只谈SZ000001"),
        )
    ]
    relations = [store.PostSymbolInput(
        identifier, "SH600000", "post_content",
        {"source_field": "text", "excerpt": "SH600000"})
        for identifier in ("target-a", "target-b")]
    covered = ledger.commit_page(
        db, "SH600000", "new", "batch-a", "first", posts,
        relations, None, 0, 1700000001000)
    assert covered["first_status_id"] == "target-a"
    assert covered["last_status_id"] == "target-b"
    assert db.execute(
        "SELECT first_status_id, last_status_id FROM xq_reverse_page_receipt"
    ).fetchone() == ("unrelated-a", "unrelated-c")


def test_identical_replay_noop_but_divergent_replay_and_stale_cas_fail(
        db: sqlite3.Connection) -> None:
    _commit(db)
    replay = _commit(db)
    assert replay["replayed"] is True
    assert replay["version"] == 1
    with pytest.raises(ledger.CheckpointConflictError, match="already confirmed"):
        _commit(db, post_id="changed")
    with pytest.raises(ledger.CheckpointConflictError, match="stale"):
        _commit(db, page_key="second", post_id="post-2", expected_version=0)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (1,)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (1,)


def test_new_batch_restarts_freshness_while_history_remains_contiguous(
        db: sqlite3.Connection) -> None:
    _commit(db, next_cursor=None)
    fresh = _commit(db, batch_id="batch-b", page_key="fresh-page",
                    post_id="post-2", expected_version=1)
    assert fresh["version"] == 2
    assert fresh["first_status_id"] is None
    assert fresh["last_status_id"] is None
    assert fresh["coverage_started_at"] is None
    assert _commit(db, batch_id="batch-a", next_cursor=None)["replayed"] is True
    assert ledger.get_checkpoint(db, "SH600000", "new")["version"] == 2
    _commit(db, lane="history", post_id="old-1", next_cursor="older")
    with pytest.raises(ledger.CheckpointConflictError, match="contiguous"):
        _commit(db, lane="history", batch_id="batch-b", page_key="fresh-page",
                post_id="old-2", expected_version=1)
    assert _commit(db, lane="history", batch_id="batch-b", page_key="older",
                   post_id="old-2", expected_version=1)["version"] == 2


def test_store_failure_rolls_back_both_post_and_watermark(
        db: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_after_insert(conn: sqlite3.Connection,
                          posts: list[store.RawPostInput],
                          relations: list[store.PostSymbolInput],
                          batch_id: str, fetched_at: int) -> None:
        conn.execute("""INSERT INTO xq_reverse_raw_post
            (source, post_id, original_url, created_at_ms, first_fetched_at,
             last_fetched_at, raw_json, first_raw_json, batch_ids_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     ("xueqiu", "temporary", "https://xueqiu.com/123/temporary",
                      1700000000000, fetched_at, fetched_at,
                      '{"id":"temporary"}', '{"id":"temporary"}',
                      '["batch-a"]'))
        raise OSError("disk write interrupted")

    monkeypatch.setattr(store, "save_posts", fail_after_insert)
    with pytest.raises(OSError, match="interrupted"):
        _commit(db)
    assert ledger.get_checkpoint(db, "SH600000", "new")["version"] == 0
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (0,)
    ledger.record_page_attempt(db, "SH600000", "new", "batch-a", "first",
                               "partial_page_failed", 1700000001000)
    assert ledger.get_checkpoint(db, "SH600000", "new")["version"] == 0


@pytest.mark.parametrize("status", [
    "page_limit", "active_stop", "stopped", "timeout", "empty", "duplicate",
    "403", "http_404", "token_invalid", "partial_page_failed",
])
def test_attempt_outcomes_never_advance_confirmation(
        db: sqlite3.Connection, status: str) -> None:
    ledger.record_page_attempt(db, "SH600000", "history", "batch-a", "page-7",
                               status, 1700000001000)
    assert ledger.get_checkpoint(db, "SH600000", "history")["version"] == 0
    assert db.execute("SELECT status FROM xq_reverse_page_attempt").fetchone() == (status,)


def test_http_404_after_confirmed_page_preserves_both_watermarks(
        db: sqlite3.Connection) -> None:
    _commit(db, lane="history", next_cursor="older")
    _commit(db, lane="new", next_cursor="fresh")
    before_history = ledger.get_checkpoint(db, "SH600000", "history")
    before_new = ledger.get_checkpoint(db, "SH600000", "new")
    ledger.record_page_attempt(db, "SH600000", "history", "batch-a", "older",
                               "http_404", 1700000002000)
    assert ledger.get_checkpoint(db, "SH600000", "history") == before_history
    assert ledger.get_checkpoint(db, "SH600000", "new") == before_new
    assert db.execute("SELECT status FROM xq_reverse_page_attempt").fetchone() == ("http_404",)


def test_invalid_lanes_posts_versions_and_cursors_are_rejected(
        db: sqlite3.Connection) -> None:
    with pytest.raises(ValueError, match="lane"):
        _commit(db, lane="both")
    with pytest.raises(ledger.PageIdentityError):
        ledger.commit_page(db, "SH600000", "new", "batch-a", "first",
                           [{"text": "no original ID"}], [], "second", 0,
                           1700000001000)
    with pytest.raises(ledger.PageIdentityError, match="duplicate"):
        ledger.commit_page(db, "SH600000", "new", "batch-a", "first",
                           [_post("1"), _post("1")], [], "second", 0,
                           1700000001000)
    with pytest.raises(ledger.PageIdentityError, match="identity"):
        ledger.commit_page(db, "SH600000", "new", "batch-a", "first",
                           [store.RawPostInput("invented", "https://xueqiu.com/1/1",
                                               1700000000000, {"id": "actual"})],
                           [], "second", 0, 1700000001000)
    with pytest.raises(ValueError, match="expected_version"):
        _commit(db, expected_version=True)
    with pytest.raises(ValueError, match="next_cursor"):
        _commit(db, next_cursor="")
    assert ledger.get_checkpoint(db, "SH600000", "new")["version"] == 0


def test_real_store_relation_validation_rolls_back_original_and_receipt(
        db: sqlite3.Connection) -> None:
    forged = store.PostSymbolInput("post-1", "SH600000", "post_content",
                                   {"source_field": "text", "excerpt": "not present SH600000"})
    with pytest.raises(ValueError, match="occur in original"):
        _commit(db, relations=[forged])
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (0,)
    assert ledger.get_checkpoint(db, "SH600000", "new")["version"] == 0


def test_checkpoint_failure_rolls_back_real_original_write(
        db: sqlite3.Connection) -> None:
    db.execute("""
        CREATE TRIGGER reject_checkpoint BEFORE INSERT ON xq_reverse_checkpoint
        BEGIN SELECT RAISE(ABORT, 'checkpoint unavailable'); END
    """)
    db.commit()
    with pytest.raises(sqlite3.IntegrityError, match="checkpoint unavailable"):
        _commit(db)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_page_receipt").fetchone() == (0,)
    assert db.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (0,)


def test_two_connections_conflict_without_corrupting_original_posts(
        db: sqlite3.Connection, tmp_path: Path) -> None:
    other = sqlite3.connect(tmp_path / "reverse.sqlite3")
    try:
        _commit(db)
        with pytest.raises(ledger.CheckpointConflictError, match="stale"):
            _commit(other, page_key="second", post_id="post-2", expected_version=0)
        assert other.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone() == (1,)
    finally:
        other.close()


def test_page_attempt_fetched_at_stored_as_integer(db: sqlite3.Connection) -> None:
    """The attempt ledger persists fetched_at as a Unix millisecond INTEGER."""
    fetched_ms = 1700000001000
    ledger.record_page_attempt(db, "SH600000", "new", "batch-a", "first",
                               "timeout", fetched_ms)
    row = db.execute(
        "SELECT typeof(fetched_at), fetched_at FROM xq_reverse_page_attempt"
    ).fetchone()
    assert row == ("integer", fetched_ms)
