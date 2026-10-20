"""Purely offline original-post storage contract tests using temporary SQLite."""

import json
import sqlite3
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_backtest.xq_reverse_store import (
    PostSymbolInput,
    RawPostInput,
    init_schema,
    save_posts,
)


def post(post_id: str = "p123", body: str = "$SH600000 and $SZ000001") -> RawPostInput:
    """Build an original response preserving fields beyond the indexed columns."""
    return RawPostInput(
        post_id=post_id,
        original_url=f"https://xueqiu.com/user/{post_id}",
        created_at_ms=1_600_000_000_123,
        raw_json={
            "id": post_id,
            "text": body,
            "user": {"id": 42, "profile": ["original", {"nested": True}]},
            "metadata": {"unindexed": [1, 2, 3]},
        },
    )


def relation(symbol: str, post_id: str = "p123") -> PostSymbolInput:
    """Build an independently verified relation to original post text."""
    return PostSymbolInput(
        post_id=post_id,
        symbol=symbol,
        evidence_type="post_content",
        evidence_json={"source_field": "text", "excerpt": symbol},
    )


def connect(tmp_path) -> sqlite3.Connection:
    """Use only a pytest-owned temporary database, never a production path."""
    connection = sqlite3.connect(tmp_path / "offline-posts.sqlite3")
    connection.execute("BEGIN IMMEDIATE")
    init_schema(connection)
    return connection


def test_idempotent_original_and_first_timestamp_preserved(tmp_path) -> None:
    """Replaying a batch deduplicates posts and does not overwrite first fetch."""
    with connect(tmp_path) as connection:
        save_posts(connection, [post()], [relation("$SH600000")], "batch-a", 1000)
        save_posts(connection, [post()], [relation("$SH600000")], "batch-a", 2000)
        save_posts(connection, [post()], [relation("$SH600000")], "batch-b", 3000)
        row = connection.execute(
            """SELECT first_fetched_at, last_fetched_at, batch_ids_json, raw_json
               FROM xq_reverse_raw_post WHERE post_id = 'p123'"""
        ).fetchone()
        assert row[0:2] == (1000, 3000)
        assert json.loads(row[2]) == ["batch-a", "batch-b"]
        assert json.loads(row[3])["user"]["profile"][1] == {"nested": True}
        assert connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_post_symbol"
        ).fetchone()[0] == 2


def test_original_json_string_preserved_byte_for_byte(tmp_path) -> None:
    """An original response provided as JSON text remains complete and intact."""
    raw = '{ "id":"p123", "text":"$SH600000", "unknown": {"x": [7, 8]} }'
    original = RawPostInput("p123", "https://xueqiu.com/p123", 456, raw)
    with connect(tmp_path) as connection:
        save_posts(connection, [original], [relation("$SH600000")], "batch-a", 1000)
        stored = connection.execute(
            "SELECT raw_json FROM xq_reverse_raw_post WHERE post_id = 'p123'"
        ).fetchone()[0]
        assert stored == raw


@pytest.mark.parametrize("bad_id", ["", "   ", None, 1])
def test_missing_or_nonstring_post_id_is_rejected(tmp_path, bad_id) -> None:
    """A missing true ID cannot be replaced by target, URL, or symbol."""
    invalid = RawPostInput(bad_id, "https://xueqiu.com/123", 456,
                           {"id": bad_id, "target": "SH600000", "text": "$SH600000"})
    with connect(tmp_path) as connection:
        with pytest.raises(ValueError):
            save_posts(connection, [invalid], [], "batch-a", 1000)
        assert connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post"
        ).fetchone()[0] == 0


def test_target_or_url_cannot_substitute_original_identity(tmp_path) -> None:
    """Post identity must agree with the original item's top-level string id."""
    invalid = RawPostInput("p123", "https://xueqiu.com/p123", 456,
                           {"target": "p123", "text": "$SH600000"})
    with connect(tmp_path) as connection:
        with pytest.raises(ValueError, match="top-level"):
            save_posts(connection, [invalid], [], "batch-a", 1000)


def test_multiple_symbols_have_independent_evidence(tmp_path) -> None:
    """Store one original but two separately supported stock mentions."""
    with connect(tmp_path) as connection:
        save_posts(connection, [post()],
                   [relation("$SH600000"), relation("$SZ000001")], "batch-a", 1000)
        rows = connection.execute(
            "SELECT symbol, evidence_json FROM xq_reverse_post_symbol ORDER BY symbol"
        ).fetchall()
        assert len(rows) == 2
        assert {symbol: json.loads(evidence)["excerpt"] for symbol, evidence in rows} == {
            "$SH600000": "$SH600000", "$SZ000001": "$SZ000001"
        }
        assert connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post"
        ).fetchone()[0] == 1


@pytest.mark.parametrize("evidence", [
    {"source_field": "search_symbol", "excerpt": "$SH600000"},
    {"source_field": "text", "excerpt": "$SZ999999"},
    {"source_field": "text", "excerpt": "$SH600000 from a search result"},
])
def test_unconfirmed_relation_is_rejected(tmp_path, evidence) -> None:
    """A search target or invented snippet cannot confirm a stock relation."""
    with connect(tmp_path) as connection:
        with pytest.raises(ValueError):
            save_posts(connection, [post()], [PostSymbolInput(
                "p123", "$SH600000", "post_content", evidence
            )], "batch-a", 1000)
        assert connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_raw_post"
        ).fetchone()[0] == 0


def test_two_batches_preserve_raw_versions_and_symbol_evidence(tmp_path) -> None:
    """Cross-batch originals and per-stock snippets remain independently traceable."""
    with connect(tmp_path) as connection:
        first = post(body="Yesterday $SH600000 alongside $SZ000001")
        second = post(body="Today $SH600000 then $SZ000001 again")
        first_relations = [relation("$SH600000"), relation("$SZ000001")]
        second_relations = [
            PostSymbolInput("p123", "$SH600000", "post_content",
                            {"source_field": "text", "excerpt": "Today $SH600000"}),
            PostSymbolInput("p123", "$SZ000001", "post_content",
                            {"source_field": "text", "excerpt": "$SZ000001 again"}),
        ]
        save_posts(connection, [first], first_relations, "batch-a", 1000)
        save_posts(connection, [second], second_relations, "batch-b", 2000)
        save_posts(connection, [first], first_relations, "batch-a", 3000)
        original = connection.execute(
            "SELECT first_raw_json, raw_json, first_fetched_at, last_fetched_at "
            "FROM xq_reverse_raw_post WHERE post_id = 'p123'"
        ).fetchone()
        assert json.loads(original[0])["text"] == first.raw_json["text"]
        assert json.loads(original[1])["text"] == second.raw_json["text"]
        assert original[2:] == (1000, 3000)
        versions = connection.execute(
            "SELECT batch_id, raw_json FROM xq_reverse_raw_post_version ORDER BY batch_id"
        ).fetchall()
        assert [(batch, json.loads(raw)["text"]) for batch, raw in versions] == [
            ("batch-a", first.raw_json["text"]),
            ("batch-b", second.raw_json["text"]),
        ]
        evidence = connection.execute(
            """SELECT symbol, batch_id, evidence_json FROM xq_reverse_post_symbol
               ORDER BY batch_id, symbol"""
        ).fetchall()
        assert [(symbol, batch) for symbol, batch, _ in evidence] == [
            ("$SH600000", "batch-a"), ("$SZ000001", "batch-a"),
            ("$SH600000", "batch-b"), ("$SZ000001", "batch-b"),
        ]
        assert json.loads(evidence[0][2])["excerpt"] == "$SH600000"
        assert json.loads(evidence[2][2])["excerpt"] == "Today $SH600000"


def test_same_batch_different_original_cannot_replace_first_version(tmp_path) -> None:
    """An inconsistent batch replay cannot silently rewrite preserved evidence."""
    with connect(tmp_path) as connection:
        save_posts(connection, [post()], [relation("$SH600000")], "batch-a", 1000)
        with pytest.raises(ValueError, match="conflicting originals"):
            save_posts(connection, [post(body="changed $SH600000")], [], "batch-a", 2000)
        stored = connection.execute(
            "SELECT raw_json, last_fetched_at FROM xq_reverse_raw_post"
        ).fetchone()
        assert json.loads(stored[0])["text"] == post().raw_json["text"]
        assert stored[1] == 1000


def test_distinct_same_batch_evidence_is_append_only(tmp_path) -> None:
    """Different original snippets in one batch never silently replace one another."""
    with connect(tmp_path) as connection:
        original = post(body="Earlier $SH600000 and later $SH600000")
        first = PostSymbolInput("p123", "$SH600000", "post_content", {
            "source_field": "text", "excerpt": "Earlier $SH600000",
        })
        second = PostSymbolInput("p123", "$SH600000", "post_content", {
            "source_field": "text", "excerpt": "later $SH600000",
        })
        save_posts(connection, [original], [first, second], "batch-a", 1000)
        save_posts(connection, [original], [first, second], "batch-a", 1100)
        snippets = [json.loads(row[0])["excerpt"] for row in connection.execute(
            "SELECT evidence_json FROM xq_reverse_post_symbol ORDER BY evidence_json"
        ).fetchall()]
        assert snippets == ["Earlier $SH600000", "later $SH600000"]


def test_caller_must_begin_transaction_before_storage(tmp_path) -> None:
    """Storage never silently starts or commits its own outer transaction."""
    connection = sqlite3.connect(tmp_path / "idle.sqlite3")
    try:
        with pytest.raises(RuntimeError, match="caller-managed transaction"):
            init_schema(connection)
        connection.execute("BEGIN IMMEDIATE")
        init_schema(connection)
        connection.commit()
        with pytest.raises(RuntimeError, match="caller-managed transaction"):
            save_posts(connection, [post()], [], "batch-a", 1000)
        assert not connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0] == 0
    finally:
        connection.close()


def test_savepoint_failure_rolls_back_only_batch_and_keeps_outer_transaction(tmp_path) -> None:
    """Reject a late invalid record without losing earlier caller writes."""
    with connect(tmp_path) as connection:
        connection.execute("CREATE TABLE caller_marker (value TEXT NOT NULL)")
        connection.commit()
        connection.execute("BEGIN")
        connection.execute("INSERT INTO caller_marker VALUES ('outer-still-active')")
        invalid = RawPostInput("not-original-id", "https://xueqiu.com/p123", 456,
                               {"id": "real-id", "text": "$SH600000"})
        with pytest.raises(ValueError):
            save_posts(connection, [post(), invalid], [relation("$SH600000")],
                       "batch-failed", 5000)
        assert connection.in_transaction
        assert connection.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0] == 0
        assert connection.execute("SELECT value FROM caller_marker").fetchone()[0] == (
            "outer-still-active"
        )
        save_posts(connection, [post()], [], "batch-good", 6000)
        connection.rollback()
        assert connection.execute("SELECT COUNT(*) FROM xq_reverse_raw_post").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM caller_marker").fetchone()[0] == 0


def test_save_posts_heals_legacy_empty_batch_ids_list(tmp_path) -> None:
    """Write-side self-heal rewrites a corrupted empty batch list; read side still rejects it.

    The read side (xq_acceptance_evidence L114, tightened by WO-038) rejects an
    empty batch_ids_json list as a corrupt state, while this write-side UPDATE
    branch rewrites it as ["<current batch_id>"]; the two sides diverge by known
    design fact and this test pins the write-side current behavior.
    """
    with connect(tmp_path) as connection:
        connection.execute(
            """INSERT INTO xq_reverse_raw_post
               (source, post_id, original_url, created_at_ms, first_fetched_at,
                last_fetched_at, raw_json, first_raw_json, batch_ids_json)
               VALUES ('xueqiu', 'p123', 'https://xueqiu.com/user/p123', 456,
                       100, 100, '{"id": "p123", "text": "legacy"}',
                       '{"id": "p123", "text": "legacy"}', '[]')""")
        save_posts(connection, [post()], [], "batch-b", 2000)
        row = connection.execute(
            """SELECT batch_ids_json, first_fetched_at, last_fetched_at
               FROM xq_reverse_raw_post WHERE post_id = 'p123'"""
        ).fetchone()
        assert json.loads(row[0]) == ["batch-b"]
        assert row[1:] == (100, 2000)


def test_same_batch_refetch_with_changed_body_never_rewrites_first_version(tmp_path) -> None:
    """First-seen per-batch version stays stable - the precondition for N2 option (a).

    A same-(post_id, batch_id) refetch with a changed body is rejected as
    conflicting originals and rolls back its savepoint, so
    xq_reverse_raw_post_version keeps the first-seen raw_json and fetched_at:
    storage guarantees first-seen version stability regardless of a later
    fetched_at.
    """
    with connect(tmp_path) as connection:
        save_posts(connection, [post(body="first $SH600000 body")], [], "batch-a", 1000)
        with pytest.raises(ValueError, match="conflicting originals"):
            save_posts(connection, [post(body="changed $SH600000 body")],
                       [], "batch-a", 2000)
        versions = connection.execute(
            """SELECT raw_json, fetched_at FROM xq_reverse_raw_post_version
               WHERE post_id = 'p123'"""
        ).fetchall()
        assert len(versions) == 1
        assert json.loads(versions[0][0])["text"] == "first $SH600000 body"
        assert versions[0][1] == 1000


def test_identical_replay_advances_recency_but_keeps_first_version_row(tmp_path) -> None:
    """Identical replay advances recency but never touches the first-seen version row."""
    with connect(tmp_path) as connection:
        save_posts(connection, [post(body="stable $SH600000 body")], [], "batch-a", 1000)
        save_posts(connection, [post(body="stable $SH600000 body")], [], "batch-a", 2000)
        row = connection.execute(
            """SELECT first_fetched_at, last_fetched_at FROM xq_reverse_raw_post
               WHERE post_id = 'p123'"""
        ).fetchone()
        assert row == (1000, 2000)
        versions = connection.execute(
            """SELECT raw_json, fetched_at FROM xq_reverse_raw_post_version
               WHERE post_id = 'p123'"""
        ).fetchall()
        assert len(versions) == 1
        assert json.loads(versions[0][0])["text"] == "stable $SH600000 body"
        assert versions[0][1] == 1000


# ---- WO-061 A2 word-boundary pins ---------------------------------------


def test_excerpt_rejects_partial_token_occurrence(tmp_path) -> None:
    """A2 (WO-061): SH600000 must not produce evidence inside SH6000000."""
    body = "discussion of SH6000000 long-run data"
    with connect(tmp_path) as connection:
        with pytest.raises(ValueError):
            save_posts(connection, [post(body=body)],
                       [relation("SH600000")], "batch-a2x", 1000)
        assert connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_post_symbol"
        ).fetchone()[0] == 0


def test_excerpt_accepts_cashtag_and_cjk_adjacent_occurrence(tmp_path) -> None:
    """A2 (WO-061): punctuation-edged and CJK-adjacent tokens stay valid."""
    body = "buy $SH600000$ today and 买SH600000的讨论"
    with connect(tmp_path) as connection:
        save_posts(connection, [post(body=body)],
                   [relation("SH600000")], "batch-a2y", 1000)
        n = connection.execute(
            "SELECT COUNT(*) FROM xq_reverse_post_symbol "
            "WHERE symbol = 'SH600000'"
        ).fetchone()[0]
    assert n == 1
