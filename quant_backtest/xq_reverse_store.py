"""Offline, transaction-composable storage of authentic Xueqiu posts.

Only a post's own top-level ``id`` is accepted as its identity. A search target,
URL, legacy table name, or inferred stock code is never an identity or evidence.
Callers own the SQLite connection and its outer transaction; this module never
commits or closes it. ``init_schema`` and ``save_posts`` are safe in an existing
transaction and each use an internal savepoint for failure isolation.
"""

from __future__ import annotations

import json
import sqlite3
import re
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class RawPostInput:
    """One complete original response item, with its trusted top-level post ID.

    ``raw_json`` must be the complete JSON object returned for this post, as a
    dict or an unmodified JSON string. Its top-level ``id`` must be the same
    nonempty string as ``post_id``. Timestamps are Unix milliseconds.
    """

    post_id: str
    original_url: str
    created_at_ms: int
    raw_json: dict[str, Any] | str
    source: str = "xueqiu"


@dataclass(frozen=True)
class PostSymbolInput:
    """An explicit stock mention independently evidenced in an original post.

    ``evidence_type`` is ``post_content``. ``evidence_json`` contains
    ``source_field`` (one of text/title/description) and ``excerpt`` (the exact
    text fragment from that field containing the exact ``symbol``). Search
    parameters, the fetch target, and URLs are never accepted as evidence.
    """

    post_id: str
    symbol: str
    evidence_type: str
    evidence_json: dict[str, Any]


def _nonempty(value: str, label: str) -> str:
    """Validate an explicit, nonblank string without inventing identifiers."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _timestamp(value: int, label: str) -> int:
    """Require an integer millisecond timestamp, excluding booleans."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer in milliseconds")
    return value


def _post_payload(post: RawPostInput) -> tuple[str, dict[str, Any]]:
    """Preserve complete raw JSON while checking the original ID exactly."""
    if not isinstance(post, RawPostInput):
        raise TypeError("posts must contain RawPostInput values")
    post_id = _nonempty(post.post_id, "post_id")
    if post.source != "xueqiu":
        raise ValueError("only source='xueqiu' is supported")
    _nonempty(post.original_url, "original_url")
    _timestamp(post.created_at_ms, "created_at_ms")
    if isinstance(post.raw_json, str):
        raw_text = post.raw_json
        try:
            parsed = json.loads(raw_text)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError("raw_json must contain a complete JSON object") from error
    elif isinstance(post.raw_json, dict):
        try:
            raw_text = json.dumps(post.raw_json, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError("raw_json must be serializable as JSON") from error
        parsed = post.raw_json
    else:
        raise TypeError("raw_json must be a JSON object or JSON string")
    if not isinstance(parsed, dict) or parsed.get("id") != post_id:
        raise ValueError(
            "post_id must equal the original response's top-level string id"
        )
    if not isinstance(parsed.get("id"), str) or not parsed["id"].strip():
        raise ValueError("original response must have a nonempty string id")
    return raw_text, parsed


_TOKEN_EDGE_RE = re.compile(r"[A-Za-z0-9]")


def _symbol_token_in(symbol: str, excerpt: str, original: str) -> bool:
    """WO-061 A2 口径：完整 token 词边界判定（原文语境）。

    excerpt 是原文的子串；symbol 在 excerpt 中的每次出现都映射回
    original 的绝对位置，并要求命中位置前后任一侧不紧贴 ASCII 字母/
    数字（SH600000 不得借 SH6000000 的子串位置产生证据）。中文等
    非 ASCII 字符紧邻不算边界破坏。任一合规出现即存在；否则整体不
    存在（closed on failure）。
    """
    start = original.find(excerpt)
    while start >= 0:
        j = excerpt.find(symbol)
        while j >= 0:
            left_pos = start + j
            left = original[left_pos - 1] if left_pos > 0 else ""
            right = original[left_pos + len(symbol) : left_pos + len(symbol) + 1]
            if not (_TOKEN_EDGE_RE.match(left) or _TOKEN_EDGE_RE.match(right)):
                return True
            j = excerpt.find(symbol, j + 1)
        start = original.find(excerpt, start + 1)
    return False


def _relation_payload(relation: PostSymbolInput, original: dict[str, Any]) -> str:
    """Check a per-stock mention against original content, not query metadata."""
    if not isinstance(relation, PostSymbolInput):
        raise TypeError("relations must contain PostSymbolInput values")
    _nonempty(relation.post_id, "relation post_id")
    symbol = _nonempty(relation.symbol, "symbol")
    if relation.evidence_type != "post_content":
        raise ValueError("evidence_type must be post_content")
    evidence = relation.evidence_json
    if not isinstance(evidence, dict):
        raise ValueError("evidence_json must be a JSON object")
    source_field = evidence.get("source_field")
    excerpt = evidence.get("excerpt")
    if source_field not in ("text", "title", "description"):
        raise ValueError("evidence must identify an original content field")
    if not isinstance(excerpt, str) or not excerpt.strip():
        raise ValueError("evidence excerpt must contain the exact symbol")
    original_content = original.get(source_field)
    if not isinstance(original_content, str) or excerpt not in original_content:
        raise ValueError("evidence excerpt must occur in original post content")
    if not _symbol_token_in(symbol, excerpt, original_content):
        raise ValueError("evidence excerpt must contain the exact symbol")
    try:
        return json.dumps(evidence, ensure_ascii=False, sort_keys=True, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise ValueError("evidence_json must be serializable as JSON") from error


def init_schema(conn: sqlite3.Connection) -> None:
    """Create the two isolated original-post tables without committing ``conn``."""
    if not conn.in_transaction:
        raise RuntimeError("init_schema requires a caller-managed transaction")
    conn.execute("SAVEPOINT xq_reverse_schema")
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS xq_reverse_raw_post (
                source TEXT NOT NULL CHECK (source = 'xueqiu'),
                post_id TEXT PRIMARY KEY NOT NULL CHECK (length(trim(post_id)) > 0),
                original_url TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                first_fetched_at INTEGER NOT NULL,
                last_fetched_at INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
                first_raw_json TEXT NOT NULL,
                batch_ids_json TEXT NOT NULL
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS xq_reverse_raw_post_version (
                post_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                original_url TEXT NOT NULL,
                created_at_ms INTEGER NOT NULL,
                fetched_at INTEGER NOT NULL,
                raw_json TEXT NOT NULL,
                PRIMARY KEY (post_id, batch_id),
                FOREIGN KEY (post_id) REFERENCES xq_reverse_raw_post(post_id)
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS xq_reverse_post_symbol (
                post_id TEXT NOT NULL,
                symbol TEXT NOT NULL CHECK (length(trim(symbol)) > 0),
                evidence_type TEXT NOT NULL CHECK (evidence_type = 'post_content'),
                evidence_json TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                PRIMARY KEY (post_id, symbol, batch_id, evidence_type, evidence_json),
                FOREIGN KEY (post_id, batch_id)
                    REFERENCES xq_reverse_raw_post_version(post_id, batch_id)
            )"""
        )
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT xq_reverse_schema")
        conn.execute("RELEASE SAVEPOINT xq_reverse_schema")
        raise
    conn.execute("RELEASE SAVEPOINT xq_reverse_schema")


def save_posts(
    conn: sqlite3.Connection,
    posts: Iterable[RawPostInput],
    relations: Iterable[PostSymbolInput],
    batch_id: str,
    fetched_at: int,
) -> None:
    """Atomically stage post originals and individually evidenced stock relations.

    Both iterables contain the dataclass input types declared above. ``batch_id``
    is a nonempty batch identifier and ``fetched_at`` is Unix milliseconds.
    Existing posts retain their first-fetch timestamp and accrue distinct batch
    IDs. Each batch retains its exact original response and each independently
    evidenced relation is append-only; relations require an original in the same
    batch. All changes remain in the caller's outer transaction; failures roll
    back only this call's savepoint.
    """
    batch_id = _nonempty(batch_id, "batch_id")
    fetched_at = _timestamp(fetched_at, "fetched_at")
    if not conn.in_transaction:
        raise RuntimeError("save_posts requires a caller-managed transaction")
    conn.execute("SAVEPOINT xq_reverse_save")
    try:
        for post in posts:
            raw_text, _ = _post_payload(post)
            version = conn.execute(
                """SELECT raw_json, original_url, created_at_ms
                   FROM xq_reverse_raw_post_version
                   WHERE post_id = ? AND batch_id = ?""",
                (post.post_id, batch_id),
            ).fetchone()
            if version is not None and version != (
                raw_text,
                post.original_url,
                post.created_at_ms,
            ):
                raise ValueError(
                    "one post and batch cannot contain conflicting originals"
                )
            stored = conn.execute(
                """SELECT last_fetched_at, batch_ids_json
                   FROM xq_reverse_raw_post WHERE post_id = ?""",
                (post.post_id,),
            ).fetchone()
            if stored is None:
                conn.execute(
                    """INSERT INTO xq_reverse_raw_post
                       (source, post_id, original_url, created_at_ms,
                        first_fetched_at, last_fetched_at, raw_json,
                        first_raw_json, batch_ids_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        post.source,
                        post.post_id,
                        post.original_url,
                        post.created_at_ms,
                        fetched_at,
                        fetched_at,
                        raw_text,
                        raw_text,
                        json.dumps([batch_id], ensure_ascii=False),
                    ),
                )
            else:
                last_fetched_at, batch_json = stored
                batches = json.loads(batch_json)
                if not isinstance(batches, list):
                    raise ValueError("stored batch_ids_json must be a list")
                if batch_id not in batches:
                    batches.append(batch_id)
                if version is None and fetched_at >= last_fetched_at:
                    conn.execute(
                        """UPDATE xq_reverse_raw_post SET original_url = ?,
                           created_at_ms = ?, raw_json = ? WHERE post_id = ?""",
                        (post.original_url, post.created_at_ms, raw_text, post.post_id),
                    )
                conn.execute(
                    """UPDATE xq_reverse_raw_post SET last_fetched_at = ?,
                       batch_ids_json = ? WHERE post_id = ?""",
                    (
                        max(fetched_at, last_fetched_at),
                        json.dumps(batches, ensure_ascii=False),
                        post.post_id,
                    ),
                )
            conn.execute(
                """INSERT INTO xq_reverse_raw_post_version
                   (post_id, batch_id, original_url, created_at_ms, fetched_at, raw_json)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(post_id, batch_id) DO NOTHING""",
                (
                    post.post_id,
                    batch_id,
                    post.original_url,
                    post.created_at_ms,
                    fetched_at,
                    raw_text,
                ),
            )
        for relation in relations:
            if not isinstance(relation, PostSymbolInput):
                raise TypeError("relations must contain PostSymbolInput values")
            version = conn.execute(
                """SELECT raw_json FROM xq_reverse_raw_post_version
                   WHERE post_id = ? AND batch_id = ?""",
                (relation.post_id, batch_id),
            ).fetchone()
            if version is None:
                raise ValueError("relation requires an original post in the same batch")
            original = json.loads(version[0])
            evidence_json = _relation_payload(relation, original)
            conn.execute(
                """INSERT INTO xq_reverse_post_symbol
                   (post_id, symbol, evidence_type, evidence_json, batch_id)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(post_id, symbol, batch_id, evidence_type, evidence_json)
                   DO NOTHING""",
                (
                    relation.post_id,
                    relation.symbol,
                    relation.evidence_type,
                    evidence_json,
                    batch_id,
                ),
            )
    except BaseException:
        conn.execute("ROLLBACK TO SAVEPOINT xq_reverse_save")
        conn.execute("RELEASE SAVEPOINT xq_reverse_save")
        raise
    conn.execute("RELEASE SAVEPOINT xq_reverse_save")
