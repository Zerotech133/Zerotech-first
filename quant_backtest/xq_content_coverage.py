"""Restricted, read-only original-post content-time model by symbol and lane.

A receipt identifies its posts when ``post_count == 1`` directly, or through
the persisted page-member roster (``xq_reverse_page_member``) when
``post_count > 1``. The roster is co-written by the ledger's ``commit_page``
inside the same transaction as the receipt; the reader trusts only that
co-written roster. A multi-post receipt without member rows predates the
roster contract and blocks instead of fabricating membership. Every
attribution, evidence or content-time disagreement is closed on failure:
the entire symbol/lane query raises rather than emitting a partial min/max.
"""

from __future__ import annotations

import json
import sqlite3
import re
from dataclasses import dataclass
from typing import Any


class CoverageAttributionBlockedError(RuntimeError):
    """A confirmed page cannot be safely attributed to its original posts."""


@dataclass(frozen=True)
class ContentCoverage:
    """Content times and evidence, separate from fetch-confirmation times.

    All timestamp values are Unix milliseconds. A missing content boundary is
    ``None``; it never inherits a checkpoint's ``coverage_*`` fetch timestamp.
    Equal-time ties use lexicographically smallest post ID for reproducibility.
    """

    symbol: str
    lane: str
    earliest_created_at_ms: int | None
    earliest_post_id: str | None
    latest_created_at_ms: int | None
    latest_post_id: str | None
    evidenced_post_count: int
    coverage_started_at: int | None
    coverage_updated_at: int | None


def _required_string(value: Any, label: str) -> str:
    """Require an exact nonempty identifier, without normalization."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _verified_evidence(
    raw_json: str,
    post_id: str,
    symbol: str,
    evidence_type: str,
    evidence_json: str,
) -> None:
    """Recheck evidence against the original from this very post/batch."""
    try:
        original = json.loads(raw_json)
        evidence = json.loads(evidence_json)
    except (TypeError, ValueError) as error:
        raise CoverageAttributionBlockedError(
            "original or evidence JSON is invalid"
        ) from error
    if not isinstance(original, dict) or original.get("id") != post_id:
        raise CoverageAttributionBlockedError(
            "batch original post identity does not match"
        )
    if evidence_type != "post_content" or not isinstance(evidence, dict):
        raise CoverageAttributionBlockedError(
            "relation lacks original-content evidence"
        )
    field = evidence.get("source_field")
    excerpt = evidence.get("excerpt")
    if field not in ("text", "title", "description"):
        raise CoverageAttributionBlockedError(
            "relation does not identify a content field"
        )
    content = original.get(field)
    if (
        not isinstance(excerpt, str)
        or not excerpt.strip()
        or not isinstance(content, str)
        or excerpt not in content
    ):
        raise CoverageAttributionBlockedError(
            "relation is unsupported by same-batch original"
        )
    if not _symbol_token_in(symbol, excerpt, content):
        raise CoverageAttributionBlockedError(
            "relation is unsupported by same-batch original"
        )


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


def _receipt_post_ids(
    conn: sqlite3.Connection,
    symbol: str,
    lane: str,
    batch_id: str,
    page_key: str,
    count: int,
    first_id: str,
    last_id: str,
) -> list[str]:
    """Resolve one confirmed receipt to its exactly attributable post IDs.

    A single-post receipt is provable from the receipt row itself; a
    co-written member roster, when present, must agree with it. A
    multi-post receipt is provable only through its persisted member
    roster: the row count, contiguous 1..n ordinals, boundaries,
    uniqueness and emptiness of the roster are all rechecked here. Any
    disagreement is closed on failure.
    """
    member_rows = conn.execute(
        """SELECT post_id, ordinal FROM xq_reverse_page_member
            WHERE symbol = ? AND lane = ? AND batch_id = ? AND page_key = ?
            ORDER BY ordinal""",
        (symbol, lane, batch_id, page_key),
    ).fetchall()
    for position, (_, ordinal) in enumerate(member_rows, start=1):
        if type(ordinal) is not int or ordinal != position:
            raise CoverageAttributionBlockedError(
                "persisted page member ordinals are not contiguous 1..n for "
                f"symbol={symbol!r}, lane={lane!r}, "
                f"batch={batch_id!r}, page={page_key!r}"
            )
    if count == 1 and first_id == last_id:
        if member_rows:
            member_ids = [row[0] for row in member_rows]
            if (
                len(member_ids) != 1
                or not isinstance(member_ids[0], str)
                or not member_ids[0].strip()
                or member_ids[0] != first_id
            ):
                raise CoverageAttributionBlockedError(
                    "persisted page members disagree with the single-post "
                    f"receipt for symbol={symbol!r}, lane={lane!r}, "
                    f"batch={batch_id!r}, page={page_key!r}"
                )
        return [first_id]
    if not member_rows:
        # A multi-post receipt predating the roster contract carries no
        # trustworthy membership; backfilling one would be fabrication.
        raise CoverageAttributionBlockedError(
            "multi-post receipt has no persisted page members for "
            f"symbol={symbol!r}, lane={lane!r}, "
            f"batch={batch_id!r}, page={page_key!r}"
        )
    member_ids = [row[0] for row in member_rows]
    if (
        len(member_ids) != count
        or any(
            not isinstance(post_id, str) or not post_id.strip()
            for post_id in member_ids
        )
        or len(set(member_ids)) != len(member_ids)
        or member_ids[0] != first_id
        or member_ids[-1] != last_id
    ):
        raise CoverageAttributionBlockedError(
            "persisted page members disagree with the receipt for "
            f"symbol={symbol!r}, lane={lane!r}, "
            f"batch={batch_id!r}, page={page_key!r}"
        )
    return member_ids


def read_content_coverage(
    conn: sqlite3.Connection,
    symbol: str,
    lane: str,
) -> ContentCoverage:
    """Read confirmed, exactly attributable posts for one stock and lane.

    This function only issues SELECT statements on the caller-owned connection;
    it neither creates schema nor commits, rolls back, or closes a transaction.
    Single-post receipts are attributed from the receipt itself; multi-post
    receipts are attributed from the page-member roster the ledger co-wrote
    with the receipt. Every member post then passes the same per-post
    verification as before: a same-batch original version must exist, the
    source and content time must be valid, every relation must be reverified
    against that original, and one post ID must never carry conflicting
    confirmed content times across batches once it is evidenced for this
    symbol/lane. Failed attempts and transit pages without verified target
    evidence cannot increase coverage. Read a stable caller-owned SQLite
    snapshot when concurrent writes matter.

    Raises:
        ValueError: The requested stock or lane is invalid.
        CoverageAttributionBlockedError: Confirmed membership or stored
            evidence cannot be established from the available records.
        sqlite3.Error: The required tables are absent or SQLite cannot read.
    """
    symbol = _required_string(symbol, "symbol")
    if lane not in ("new", "history"):
        raise ValueError("lane must be 'new' or 'history'")

    checkpoint = conn.execute(
        """SELECT coverage_started_at, coverage_updated_at
             FROM xq_reverse_checkpoint WHERE symbol = ? AND lane = ?""",
        (symbol, lane),
    ).fetchone()
    receipts = conn.execute(
        """SELECT batch_id, page_key, post_count, first_status_id, last_status_id
             FROM xq_reverse_page_receipt
             WHERE symbol = ? AND lane = ?""",
        (symbol, lane),
    ).fetchall()
    # This restricted reader must fail in its entirety when any confirmed
    # receipt lacks exact membership; a partial min/max is not coverage.
    attributed: list[tuple[str, list[str]]] = []
    for batch_id, page_key, count, first_id, last_id in receipts:
        if (
            not isinstance(batch_id, str)
            or not batch_id.strip()
            or not isinstance(page_key, str)
            or not page_key.strip()
            or type(count) is not int
            or count < 1
            or not isinstance(first_id, str)
            or not first_id.strip()
            or not isinstance(last_id, str)
            or not last_id.strip()
        ):
            raise CoverageAttributionBlockedError(
                "confirmed page membership is not provable for "
                f"symbol={symbol!r}, lane={lane!r}, "
                f"batch={batch_id!r}, page={page_key!r}"
            )
        attributed.append(
            (
                batch_id,
                _receipt_post_ids(
                    conn, symbol, lane, batch_id, page_key, count, first_id, last_id
                ),
            )
        )

    evidenced: dict[str, int] = {}
    for batch_id, post_ids in attributed:
        for post_id in post_ids:
            version = conn.execute(
                """SELECT v.created_at_ms, v.raw_json, p.source
                     FROM xq_reverse_raw_post_version AS v
                     JOIN xq_reverse_raw_post AS p ON p.post_id = v.post_id
                     WHERE v.post_id = ? AND v.batch_id = ?""",
                (post_id, batch_id),
            ).fetchone()
            if version is None:
                raise CoverageAttributionBlockedError(
                    "confirmed post has no same-batch original version"
                )
            created_at_ms, raw_json, source = version
            if (
                source != "xueqiu"
                or type(created_at_ms) is not int
                or created_at_ms < 0
            ):
                raise CoverageAttributionBlockedError(
                    "original source or content time is invalid"
                )
            relations = conn.execute(
                """SELECT evidence_type, evidence_json
                     FROM xq_reverse_post_symbol
                     WHERE post_id = ? AND batch_id = ? AND symbol = ?""",
                (post_id, batch_id, symbol),
            ).fetchall()
            for evidence_type, evidence_json in relations:
                _verified_evidence(
                    raw_json, post_id, symbol, evidence_type, evidence_json
                )
            if relations:
                # Reobserved post IDs are one piece of content evidence, not
                # multiple posts. Distinct batch versions must agree on its time.
                previous = evidenced.setdefault(post_id, created_at_ms)
                if previous != created_at_ms:
                    raise CoverageAttributionBlockedError(
                        "same post has conflicting confirmed content times"
                    )

    earliest = min(
        ((time, post_id) for post_id, time in evidenced.items()), default=None
    )
    latest = min(
        ((-time, post_id) for post_id, time in evidenced.items()), default=None
    )
    return ContentCoverage(
        symbol=symbol,
        lane=lane,
        earliest_created_at_ms=earliest[0] if earliest else None,
        earliest_post_id=earliest[1] if earliest else None,
        latest_created_at_ms=-latest[0] if latest else None,
        latest_post_id=latest[1] if latest else None,
        evidenced_post_count=len(evidenced),
        coverage_started_at=checkpoint[0] if checkpoint else None,
        coverage_updated_at=checkpoint[1] if checkpoint else None,
    )
