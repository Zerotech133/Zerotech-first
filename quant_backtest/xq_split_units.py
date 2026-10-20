"""Purely offline minimal-unit splitter for authentic Xueqiu original posts.

One original post (``xq_reverse_store.RawPostInput``) is split into minimal
information units: post, author, time, mention and content. Every unit keeps
its ``raw_post_id``, so the chain raw_post_id -> split unit -> stock relation
stays reversible through :func:`lineage` and :func:`origin`. A mention unit
records the exact character offsets of its excerpt inside the original content
field; those offsets must satisfy ``field_text[start:end] == excerpt``.

This module is a pure splitter: it never creates tables, never writes to a
database and performs no network I/O. Every invalid input is rejected with
ValueError (closed on failure): no author is invented for a missing field, no
identity is invented for a mismatched id, and no timestamp is invented for an
illegal value.

Event extraction is not wired: there is no event unit type and lineage output
marks ``downstream: not_linked``. Classification, factor, knowledge-graph and
report stages are not linked from here either (not_linked).

Multi-source readiness: every unit carries a ``source`` label and this module
only accepts ``source='xueqiu'``. Future TDX/Tushare supplement units attach
under their own source labels; units from different sources are never merged
into one indistinguishable post record.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

try:
    from . import xq_reverse_store
except ImportError:
    import xq_reverse_store


#: The only source label accepted by this splitter.
SOURCE_XUEQIU: str = "xueqiu"

UNIT_TYPE_POST: str = "post"
UNIT_TYPE_AUTHOR: str = "author"
UNIT_TYPE_TIME: str = "time"
UNIT_TYPE_MENTION: str = "mention"
UNIT_TYPE_CONTENT: str = "content"

#: Unit types this splitter can emit. There is no event type (not wired).
UNIT_TYPES: frozenset[str] = frozenset({
    UNIT_TYPE_POST, UNIT_TYPE_AUTHOR, UNIT_TYPE_TIME, UNIT_TYPE_MENTION,
    UNIT_TYPE_CONTENT,
})

#: Explicit marker key in an author unit when raw_json has no author field.
AUTHOR_MISSING_KEY: str = "author_missing"

#: Lineage marker: downstream stages are not linked from this module.
DOWNSTREAM_NOT_LINKED: str = "not_linked"

#: Author field paths tried in order; the first nonempty string wins.
AUTHOR_FIELD_PATHS: tuple[tuple[str, ...], ...] = (
    ("user", "screen_name"),
    ("user", "name"),
    ("screen_name",),
    ("name",),
    ("author",),
)

#: raw_json keys that indicate a time field; the first present key is recorded.
TIME_FIELD_NAMES: tuple[str, ...] = (
    "created_at_ms", "created_at", "timestamp", "time",
)

#: Nonempty content fields produce one content unit each, in this order.
CONTENT_FIELD_NAMES: tuple[str, ...] = ("text", "title", "description")

#: Hex digits kept from sha256 digests for unit_id and content hashes.
HASH_HEX_LENGTH: int = 32


@dataclass(frozen=True)
class Unit:
    """One minimal information unit split from a single original post.

    Attributes:
        unit_id: Deterministic truncated-sha256 identity (see
            :func:`split_post`); the same input always yields the same value.
        unit_type: One of :data:`UNIT_TYPES`.
        source: Origin label; always ``xueqiu`` for this module.
        raw_post_id: The original post this unit was split from.
        payload: Type-specific facts. Keys per unit type:
            ``post`` -> ``original_url``, ``created_at_ms``;
            ``author`` -> ``field_name`` and ``value``, or
            ``author_missing: True`` when raw_json has no author field;
            ``time`` -> ``created_at_ms`` and ``time_source`` (the raw_json
            time field name, or ``created_at_ms`` when storage provides it);
            ``mention`` -> ``symbol``, ``evidence_type``, ``source_field``,
            ``excerpt``, ``start``, ``end``;
            ``content`` -> ``field_name``, ``length``, ``content_sha256``.
    """

    unit_id: str
    unit_type: str
    source: str
    raw_post_id: str
    payload: dict[str, Any]


def _required_string(value: Any, label: str) -> str:
    """Return a nonempty string, else raise ValueError (closed on failure)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a nonempty string")
    return value


def _timestamp(value: Any, label: str) -> int:
    """Require a nonnegative integer millisecond timestamp, excluding bool."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a nonnegative integer in milliseconds")
    return value


def _unit_id(source: str, raw_post_id: str, unit_type: str, key: str) -> str:
    """Build the deterministic truncated-sha256 identity of one unit.

    The digest covers source, raw_post_id, unit_type and a per-type key
    (a stable business key or the type-local sequence number), so splitting
    the same input twice reproduces the exact same unit_id sequence.
    """
    material = f"{source}|{raw_post_id}|{unit_type}|{key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:HASH_HEX_LENGTH]


def _post_payload(post: Any) -> tuple[str, dict[str, Any]]:
    """Validate one original post and return ``(post_id, parsed original)``.

    Mirrors the storage contract: dict ``raw_json`` must serialize as JSON
    (``json.dumps(..., ensure_ascii=False, allow_nan=False)``, so NaN and
    non-JSON values are rejected exactly as ``xq_reverse_store`` rejects
    them), raw_json keeps its top-level string ``id``, and that id must
    equal ``post_id`` exactly. Any disagreement raises ValueError (closed
    on failure).
    """
    if not isinstance(post, xq_reverse_store.RawPostInput):
        raise TypeError("post must be a RawPostInput")
    post_id = _required_string(post.post_id, "post_id")
    if post.source != SOURCE_XUEQIU:
        raise ValueError("only source='xueqiu' is supported")
    _required_string(post.original_url, "original_url")
    _timestamp(post.created_at_ms, "created_at_ms")
    if isinstance(post.raw_json, str):
        try:
            parsed = json.loads(post.raw_json)
        except (json.JSONDecodeError, ValueError) as error:
            raise ValueError(
                "raw_json must contain a complete JSON object") from error
    elif isinstance(post.raw_json, dict):
        try:
            json.dumps(post.raw_json, ensure_ascii=False, allow_nan=False)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "raw_json must be serializable as JSON") from error
        parsed = post.raw_json
    else:
        raise TypeError("raw_json must be a JSON object or JSON string")
    if not isinstance(parsed, dict) or parsed.get("id") != post_id:
        raise ValueError(
            "post_id must equal the original response's top-level string id")
    if not isinstance(parsed.get("id"), str) or not parsed["id"].strip():
        raise ValueError("original response must have a nonempty string id")
    return post_id, parsed


def _author_fact(original: dict[str, Any]) -> dict[str, Any]:
    """Find one author field by fixed priority, or an explicit missing marker.

    Each dotted path in :data:`AUTHOR_FIELD_PATHS` is resolved against the
    original JSON object; the first path holding a nonempty string wins and
    that exact path is recorded as ``field_name``. When no path hits, nothing
    is invented: the single author unit carries ``author_missing: True``
    instead (closed on failure).
    """
    for path in AUTHOR_FIELD_PATHS:
        node: Any = original
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, str) and node.strip():
            return {"field_name": ".".join(path), "value": node}
    return {AUTHOR_MISSING_KEY: True}


def _time_fact(
    post: xq_reverse_store.RawPostInput, original: dict[str, Any]
) -> dict[str, Any]:
    """Record the trusted storage time plus where the original carries time.

    ``created_at_ms`` always comes from the validated storage input. The
    ``time_source`` names the first raw_json key in :data:`TIME_FIELD_NAMES`
    that is present; when none is present, ``time_source`` is ``created_at_ms``
    to mark that storage provided the time.
    """
    time_source = "created_at_ms"
    for name in TIME_FIELD_NAMES:
        if name in original:
            time_source = name
            break
    return {"created_at_ms": post.created_at_ms, "time_source": time_source}


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
            right = original[left_pos + len(symbol):
                             left_pos + len(symbol) + 1]
            if not (_TOKEN_EDGE_RE.match(left)
                    or _TOKEN_EDGE_RE.match(right)):
                return True
            j = excerpt.find(symbol, j + 1)
        start = original.find(excerpt, start + 1)
    return False

def _mention_fact(
    relation: Any, post_id: str, original: dict[str, Any]
) -> dict[str, Any]:
    """Validate one evidenced stock mention and record its reversible offsets.

    The excerpt must contain the exact symbol and occur verbatim in the named
    original content field; ``start``/``end`` are the character offsets of
    that occurrence so that ``field_text[start:end] == excerpt``. Any evidence
    disagreement raises ValueError (closed on failure).
    """
    if not isinstance(relation, xq_reverse_store.PostSymbolInput):
        raise TypeError("relations must contain PostSymbolInput values")
    if relation.post_id != post_id:
        raise ValueError("relation post_id must equal the split post_id")
    symbol = _required_string(relation.symbol, "symbol")
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
    field_text = original.get(source_field)
    if not isinstance(field_text, str) or excerpt not in field_text:
        raise ValueError("evidence excerpt must occur in original post content")
    if not _symbol_token_in(symbol, excerpt, field_text):
        raise ValueError("evidence excerpt must contain the exact symbol")
    start = field_text.find(excerpt)
    end = start + len(excerpt)
    if field_text[start:end] != excerpt:
        raise ValueError("evidence offsets must re-read the exact excerpt")
    return {
        "symbol": symbol,
        "evidence_type": relation.evidence_type,
        "source_field": source_field,
        "excerpt": excerpt,
        "start": start,
        "end": end,
    }


def _content_fact(
    field_name: str, original: dict[str, Any]
) -> dict[str, Any] | None:
    """Return one content fact for a nonempty text field, else ``None``.

    The fact records the field name, the full text length in characters and a
    truncated sha256 of the full text. A missing or empty field yields no
    content unit; a present but non-string field raises ValueError (closed on
    failure) rather than being silently dropped.
    """
    if field_name not in original:
        return None
    value = original[field_name]
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"content field {field_name} must be a string")
    if not value:
        return None
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:HASH_HEX_LENGTH]
    return {"field_name": field_name, "length": len(value), "content_sha256": digest}


def split_post(
    post: xq_reverse_store.RawPostInput,
    relations: Sequence[xq_reverse_store.PostSymbolInput] = (),
) -> list[Unit]:
    """Split one original post into its minimal information units.

    Units are returned in a fixed, deterministic order: one ``post`` unit,
    exactly one ``author`` unit (the author field or an explicit
    ``author_missing`` marker), exactly one ``time`` unit, one ``mention`` unit
    per input relation in input order, then one ``content`` unit per nonempty
    ``text``/``title``/``description`` field in that order. No event unit is
    ever produced (event extraction not wired).

    Each ``unit_id`` is a truncated sha256 over source, raw_post_id, unit_type
    and a per-type key (business key for post/author/time/content, and for
    mention the type-local sequence number joined with the symbol and excerpt
    fingerprint), so splitting the same input twice yields the identical
    unit_id sequence. Changed mention evidence therefore never collides with
    a prior mention unit_id of the same raw post. Invalid posts or relations
    raise ValueError or TypeError (closed on failure) and nothing is
    fabricated.
    """
    post_id, original = _post_payload(post)
    if isinstance(relations, (str, bytes)) or not isinstance(relations, Sequence):
        raise TypeError("relations must be a sequence of PostSymbolInput values")
    units: list[Unit] = []
    units.append(Unit(
        unit_id=_unit_id(SOURCE_XUEQIU, post_id, UNIT_TYPE_POST, "post"),
        unit_type=UNIT_TYPE_POST,
        source=SOURCE_XUEQIU,
        raw_post_id=post_id,
        payload={
            "original_url": post.original_url,
            "created_at_ms": post.created_at_ms,
        },
    ))
    author_payload = _author_fact(original)
    author_key = (
        author_payload["field_name"]
        if AUTHOR_MISSING_KEY not in author_payload
        else AUTHOR_MISSING_KEY
    )
    units.append(Unit(
        unit_id=_unit_id(SOURCE_XUEQIU, post_id, UNIT_TYPE_AUTHOR, author_key),
        unit_type=UNIT_TYPE_AUTHOR,
        source=SOURCE_XUEQIU,
        raw_post_id=post_id,
        payload=author_payload,
    ))
    units.append(Unit(
        unit_id=_unit_id(SOURCE_XUEQIU, post_id, UNIT_TYPE_TIME, "time"),
        unit_type=UNIT_TYPE_TIME,
        source=SOURCE_XUEQIU,
        raw_post_id=post_id,
        payload=_time_fact(post, original),
    ))
    for index, relation in enumerate(relations):
        fact = _mention_fact(relation, post_id, original)
        units.append(Unit(
            unit_id=_unit_id(
                SOURCE_XUEQIU, post_id, UNIT_TYPE_MENTION,
                "%d\x1f%s\x1f%s" % (index, fact["symbol"], fact["excerpt"]),
            ),
            unit_type=UNIT_TYPE_MENTION,
            source=SOURCE_XUEQIU,
            raw_post_id=post_id,
            payload=fact,
        ))
    for field_name in CONTENT_FIELD_NAMES:
        fact = _content_fact(field_name, original)
        if fact is None:
            continue
        units.append(Unit(
            unit_id=_unit_id(SOURCE_XUEQIU, post_id, UNIT_TYPE_CONTENT, field_name),
            unit_type=UNIT_TYPE_CONTENT,
            source=SOURCE_XUEQIU,
            raw_post_id=post_id,
            payload=fact,
        ))
    return units


def lineage(units: Sequence[Unit]) -> dict[str, dict[str, Any]]:
    """Group split units by raw_post_id into a reversible lineage map.

    Returns ``{raw_post_id: {"unit_ids": [...], "symbols": [...],
    "source": ..., "downstream": "not_linked"}}``. ``unit_ids`` preserves
    split order; ``symbols`` lists distinct mention symbols in first-appearance
    order. ``downstream`` is always :data:`DOWNSTREAM_NOT_LINKED`: downstream
    classification, factor, graph and report stages are not linked here.
    Units of one raw_post_id must share one source; mixing sources raises
    ValueError (closed on failure) instead of producing an indistinguishable
    merged record.
    """
    result: dict[str, dict[str, Any]] = {}
    for unit in units:
        if not isinstance(unit, Unit):
            raise TypeError("units must contain Unit values")
        _required_string(unit.unit_id, "unit_id")
        _required_string(unit.raw_post_id, "raw_post_id")
        _required_string(unit.source, "source")
        if unit.unit_type not in UNIT_TYPES:
            raise ValueError("unknown unit_type in units (closed on failure)")
        entry = result.setdefault(unit.raw_post_id, {
            "unit_ids": [],
            "symbols": [],
            "source": unit.source,
            "downstream": DOWNSTREAM_NOT_LINKED,
        })
        if entry["source"] != unit.source:
            raise ValueError(
                "one raw_post_id cannot mix sources (closed on failure)")
        entry["unit_ids"].append(unit.unit_id)
        if unit.unit_type == UNIT_TYPE_MENTION:
            symbol = unit.payload.get("symbol")
            if not isinstance(symbol, str) or not symbol.strip():
                raise ValueError("mention unit needs a nonempty symbol")
            if symbol not in entry["symbols"]:
                entry["symbols"].append(symbol)
    return result


def origin(units: Sequence[Unit], unit_id: str) -> dict[str, Any]:
    """Return the source chain of one unit for reverse lookup.

    The result is ``unit_id``, ``raw_post_id``, ``unit_type``, ``source`` plus
    the unit payload flattened on top (e.g. ``source_field``/``start``/``end``
    for a mention, ``field_name`` for a content or author unit). An unknown or
    ambiguous ``unit_id`` raises ValueError (closed on failure).
    """
    _required_string(unit_id, "unit_id")
    matches: list[Unit] = []
    for unit in units:
        if not isinstance(unit, Unit):
            raise TypeError("units must contain Unit values")
        if unit.unit_id == unit_id:
            matches.append(unit)
    if not matches:
        raise ValueError("unknown unit_id (closed on failure)")
    if len(matches) > 1:
        raise ValueError("ambiguous unit_id (closed on failure)")
    unit = matches[0]
    return {
        "unit_id": unit.unit_id,
        "raw_post_id": unit.raw_post_id,
        "unit_type": unit.unit_type,
        "source": unit.source,
        **dict(unit.payload),
    }
