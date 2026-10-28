"""Purely offline split-unit persistence contract tests using temporary SQLite.

Foreign-key note: neither ``xq_reverse_store`` nor ``xq_split_unit_store``
flips the ``foreign_keys`` pragma; enforcement is caller-managed. These tests
pin both sides of that behavior: with ``PRAGMA foreign_keys=ON`` (the suite
convention) a dangling ``raw_post_id`` is rejected, and without it the insert
is tolerated — both are the documented, intended behavior.
"""

import json
import sqlite3
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from quant_backtest import xq_split_units
from quant_backtest.xq_reverse_store import (
    PostSymbolInput,
    RawPostInput,
    init_schema as init_store_schema,
    save_posts,
)
from quant_backtest.xq_split_unit_store import (
    init_schema as init_unit_schema,
    persist,
)


def post() -> RawPostInput:
    """Build an original response with Chinese content and an author field."""
    return RawPostInput(
        post_id="p123",
        original_url="https://xueqiu.com/p123",
        created_at_ms=1_600_000_000_123,
        raw_json={
            "id": "p123",
            "text": "$SH600000 雪球样本正文",
            "user": {"screen_name": "老田"},
        },
    )


def relation() -> PostSymbolInput:
    """Build an evidenced mention whose excerpt carries Chinese text."""
    return PostSymbolInput(
        post_id="p123",
        symbol="$SH600000",
        evidence_type="post_content",
        evidence_json={
            "source_field": "text",
            "excerpt": "$SH600000 雪球样本正文",
        },
    )


def split_units() -> list[xq_split_units.Unit]:
    """Split the sample post freshly (deterministic unit_ids each call)."""
    return xq_split_units.split_post(post(), [relation()])


def connect(tmp_path, foreign_keys: bool = True) -> sqlite3.Connection:
    """Use only a pytest-owned temporary database, never a production path."""
    connection = sqlite3.connect(tmp_path / "offline-split-units.sqlite3")
    if foreign_keys:
        connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("BEGIN IMMEDIATE")
    init_store_schema(connection)
    init_unit_schema(connection)
    return connection


def stage_post(connection: sqlite3.Connection) -> None:
    """Store the sample original so split-unit foreign keys can hold."""
    save_posts(connection, [post()], [relation()], "batch-a", 1000)


def unit_rows(connection: sqlite3.Connection) -> list[tuple]:
    """Read every split-unit row in raw column order."""
    return connection.execute(
        """SELECT unit_id, source, unit_type, raw_post_id, payload_json
           FROM xq_reverse_split_unit"""
    ).fetchall()


def test_schema_has_exactly_five_contract_columns(tmp_path) -> None:
    """The table keeps the frozen five-column contract with no extra columns."""
    with connect(tmp_path) as connection:
        columns = connection.execute(
            "PRAGMA table_info(xq_reverse_split_unit)"
        ).fetchall()
        assert [(row[1], row[2], row[3], row[5]) for row in columns] == [
            ("unit_id", "TEXT", 1, 1),
            ("source", "TEXT", 1, 0),
            ("unit_type", "TEXT", 1, 0),
            ("raw_post_id", "TEXT", 1, 0),
            ("payload_json", "TEXT", 1, 0),
        ]
        names = {row[1] for row in columns}
        assert names.isdisjoint({"symbol", "created_at_ms", "created_at"})


def test_persist_stores_every_unit_and_payload_roundtrips(tmp_path) -> None:
    """Persisted payloads deserialize back to the exact splitter payloads."""
    units = split_units()
    with connect(tmp_path) as connection:
        stage_post(connection)
        assert persist(connection, units) == (len(units), 0)
        rows = unit_rows(connection)
        assert len(rows) == len(units)
        stored = {row[0]: row for row in rows}
        for unit in units:
            row = stored[unit.unit_id]
            assert row[1:4] == (unit.source, unit.unit_type, unit.raw_post_id)
            assert json.loads(row[4]) == unit.payload


def test_idempotent_repersist_ignores_every_unit(tmp_path) -> None:
    """Re-splitting the same post re-persists with zero new rows."""
    with connect(tmp_path) as connection:
        stage_post(connection)
        assert persist(connection, split_units())[0] > 0
        before = unit_rows(connection)
        assert persist(connection, split_units()) == (0, len(before))
        assert unit_rows(connection) == before


def test_unit_type_outside_enumeration_is_rejected(tmp_path) -> None:
    """A unit_type beyond the frozen enumeration fails closed as ValueError."""
    bad = xq_split_units.Unit(
        unit_id="deadbeef",
        unit_type="event",
        source="xueqiu",
        raw_post_id="p123",
        payload={"length": 3},
    )
    with connect(tmp_path) as connection:
        stage_post(connection)
        with pytest.raises(ValueError):
            persist(connection, [bad])
        assert unit_rows(connection) == []


def test_non_xueqiu_source_is_rejected(tmp_path) -> None:
    """A unit labeled with another source fails closed as ValueError."""
    bad = xq_split_units.Unit(
        unit_id="deadbeef",
        unit_type="post",
        source="tdx",
        raw_post_id="p123",
        payload={},
    )
    with connect(tmp_path) as connection:
        stage_post(connection)
        with pytest.raises(ValueError):
            persist(connection, [bad])
        assert unit_rows(connection) == []


def test_chinese_payload_is_stored_unescaped(tmp_path) -> None:
    """Chinese payload text survives ensure_ascii=False without \\u escapes."""
    with connect(tmp_path) as connection:
        stage_post(connection)
        persist(connection, split_units())
        author_json = connection.execute(
            """SELECT payload_json FROM xq_reverse_split_unit
               WHERE unit_type = 'author'"""
        ).fetchone()[0]
        assert "老田" in author_json
        assert json.loads(author_json) == {"field_name": "user.screen_name",
                                           "value": "老田"}


def test_nan_payload_fails_closed_and_rolls_back(tmp_path) -> None:
    """A non-serializable payload raises ValueError and leaves no rows."""
    bad = xq_split_units.Unit(
        unit_id="deadbeef",
        unit_type="post",
        source="xueqiu",
        raw_post_id="p123",
        payload={"ratio": float("nan")},
    )
    unserializable = xq_split_units.Unit(
        unit_id="deadbeef",
        unit_type="content",
        source="xueqiu",
        raw_post_id="p123",
        payload={"items": {1, 2}},
    )
    with connect(tmp_path) as connection:
        stage_post(connection)
        for unit in (bad, unserializable):
            with pytest.raises(ValueError):
                persist(connection, split_units() + [unit])
            assert unit_rows(connection) == []


def test_dangling_raw_post_id_rejected_with_foreign_keys_on(tmp_path) -> None:
    """With the caller-managed pragma on, a dangling reference is rejected."""
    orphan = xq_split_units.Unit(
        unit_id="deadbeef",
        unit_type="post",
        source="xueqiu",
        raw_post_id="missing",
        payload={},
    )
    with connect(tmp_path) as connection:
        stage_post(connection)
        with pytest.raises(sqlite3.IntegrityError):
            persist(connection, split_units() + [orphan])
        assert unit_rows(connection) == []


def test_dangling_raw_post_id_tolerated_without_pragma(tmp_path) -> None:
    """Without the pragma the same insert is tolerated: caller-managed FK."""
    orphan = xq_split_units.Unit(
        unit_id="deadbeef",
        unit_type="post",
        source="xueqiu",
        raw_post_id="missing",
        payload={},
    )
    with connect(tmp_path, foreign_keys=False) as connection:
        stage_post(connection)
        assert persist(connection, [orphan]) == (1, 0)


def changed_relation() -> PostSymbolInput:
    """Same raw post, corrected evidence: excerpt text differs from pass one."""
    return PostSymbolInput(
        post_id="p123",
        symbol="$SH600000",
        evidence_type="post_content",
        evidence_json={
            "source_field": "text",
            "excerpt": "$SH600000",
        },
    )


def test_repersist_with_changed_relations_never_collides(tmp_path) -> None:
    """Blind spot d (WO-057): the same post re-split with changed mention
    evidence persists the corrected mention as a NEW unit_id; unchanged
    non-mention units stay idempotent-ignored. No collision, no overwrite."""
    with connect(tmp_path) as connection:
        stage_post(connection)
        first = xq_split_units.split_post(post(), [relation()])
        assert persist(connection, first) == (5, 0)
        before = unit_rows(connection)
        second = xq_split_units.split_post(post(), [changed_relation()])
        mention_first = [u for u in first if u.unit_type == "mention"]
        mention_second = [u for u in second if u.unit_type == "mention"]
        # F2 fingerprint: changed evidence -> different mention unit_id.
        assert mention_second[0].unit_id != mention_first[0].unit_id
        # Non-mention identity is untouched by evidence changes.
        non_mention_first = sorted(
            u.unit_id for u in first if u.unit_type != "mention")
        non_mention_second = sorted(
            u.unit_id for u in second if u.unit_type != "mention")
        assert non_mention_first == non_mention_second
        # Second pass inserts only the corrected mention; the other four
        # units are ignored as duplicates (no collision, no rewrite).
        assert persist(connection, second) == (1, 4)
        after = unit_rows(connection)
        assert len(after) == len(before) + 1
        all_ids = [row[0] for row in after]
        assert len(all_ids) == len(set(all_ids))
        [row[4] for row in after]
        corrected = [row[4] for row in after if row[0] == mention_second[0].unit_id]
        assert len(corrected) == 1 and "$SH600000" in corrected[0]
