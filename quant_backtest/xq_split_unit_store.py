"""Offline persistence of split units into the ``xq_reverse_split_unit`` table.

The table schema follows the frozen contract in the migration design (v1
section 2.2): exactly five columns, no symbol column, no timestamp column and
no string-time column. Identity is the deterministic ``unit_id`` produced by
the frozen pure splitter ``xq_split_units`` (option a of the B7a ruling:
splitting is based on the main row's first-seen version, so no ``batch_id``
column exists here).

Callers own the SQLite connection and its outer transaction; this module never
commits or closes it. ``init_schema`` and ``persist`` are safe inside an
existing transaction and each use an internal savepoint for failure
isolation, mirroring ``xq_reverse_store``. Foreign-key enforcement of
``raw_post_id`` is caller-managed: this module never flips the
``foreign_keys`` pragma, matching ``xq_reverse_store``.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable

try:
    from . import xq_split_units
except ImportError:
    import xq_split_units


def _validate(unit: Any) -> xq_split_units.Unit:
    """Reject anything that is not a splitter Unit with contract values."""
    if not isinstance(unit, xq_split_units.Unit):
        raise TypeError("units must contain xq_split_units.Unit values")
    if unit.source != xq_split_units.SOURCE_XUEQIU:
        raise ValueError("only source='xueqiu' is supported")
    if unit.unit_type not in xq_split_units.UNIT_TYPES:
        raise ValueError("unit_type is outside the split-unit enumeration")
    return unit


def _payload_text(payload: dict[str, Any]) -> str:
    """Serialize a payload under the evidence_json contract, fail-closed."""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("unit payload must be serializable as JSON") from error


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the split-unit table without committing ``conn``.

    The DDL reproduces the design 2.2 contract verbatim: five columns only,
    ``unit_id`` as text primary key, the single-source check, the fixed
    unit-type enumeration and a foreign key to the original-post table.
    """
    if not conn.in_transaction:
        raise RuntimeError("init_schema requires a caller-managed transaction")
    conn.execute("SAVEPOINT xq_split_unit_schema")
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS xq_reverse_split_unit (
                unit_id TEXT PRIMARY KEY NOT NULL,
                source TEXT NOT NULL CHECK (source = 'xueqiu'),
                unit_type TEXT NOT NULL CHECK (
                    unit_type IN ('post', 'author', 'time', 'mention', 'content')),
                raw_post_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                FOREIGN KEY (raw_post_id) REFERENCES xq_reverse_raw_post(post_id)
            )"""
        )
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT xq_split_unit_schema")
        conn.execute("RELEASE SAVEPOINT xq_split_unit_schema")
        raise
    conn.execute("RELEASE SAVEPOINT xq_split_unit_schema")


def persist(
    conn: sqlite3.Connection, units: Iterable[xq_split_units.Unit]
) -> tuple[int, int]:
    """Insert split units append-only and return ``(inserted, ignored)``.

    ``units`` is a sequence of :class:`xq_split_units.Unit` values as produced
    by :func:`xq_split_units.split_post`. Each payload is serialized with the
    evidence_json contract (``ensure_ascii=False, sort_keys=True,
    allow_nan=False``); a non-serializable payload raises ValueError and rolls
    back this call's savepoint (closed on failure, nothing silent). Rows are
    inserted with ``INSERT OR IGNORE`` keyed by the deterministic ``unit_id``,
    so re-persisting the same split input is idempotent: the second call
    reports every unit as ignored and the table content stays unchanged.
    """
    if not conn.in_transaction:
        raise RuntimeError("persist requires a caller-managed transaction")
    conn.execute("SAVEPOINT xq_split_unit_save")
    inserted = 0
    ignored = 0
    try:
        for unit in units:
            _validate(unit)
            payload_text = _payload_text(unit.payload)
            cursor = conn.execute(
                """INSERT OR IGNORE INTO xq_reverse_split_unit
                   (unit_id, source, unit_type, raw_post_id, payload_json)
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    unit.unit_id,
                    unit.source,
                    unit.unit_type,
                    unit.raw_post_id,
                    payload_text,
                ),
            )
            if cursor.rowcount == 1:
                inserted += 1
            else:
                ignored += 1
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT xq_split_unit_save")
        conn.execute("RELEASE SAVEPOINT xq_split_unit_save")
        raise
    conn.execute("RELEASE SAVEPOINT xq_split_unit_save")
    return inserted, ignored
