"""Structured knowledge persistence models.

Video files are temporary extraction inputs.  These lightweight domain models
represent the durable records that remain after extraction and allow callers to
build idempotent ingestion jobs without depending on media paths.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import math
from typing import Any


def _stable_json(value: Any) -> str:
    """Serialize nested attributes deterministically for observation identity."""
    import json

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _required(value: str, field_name: str) -> str:
    """Return a trimmed required string or raise ``ValueError``."""
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be empty")
    return normalized


def _utc(value: datetime | None) -> datetime:
    """Normalize optional timestamps to timezone-aware UTC values."""
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


SOURCE_CHANNELS: frozenset[str] = frozenset({"asr", "ocr", "subtitle", "manual"})
REVIEW_STATUSES: frozenset[str] = frozenset({"unreviewed", "reviewed", "rejected"})
RESOURCE_STATUSES: frozenset[str] = frozenset(
    {"candidate", "reviewed", "published", "deprecated", "quarantined"}
)
EVIDENCE_STATUSES: frozenset[str] = frozenset(
    {"draft", "valid", "rejected", "superseded"}
)
INDEX_STATUSES: frozenset[str] = frozenset(
    {"pending", "running", "succeeded", "failed"}
)
TRUTH_STATUSES: frozenset[str] = frozenset(
    {"unknown", "correct", "incorrect", "contested"}
)
EVIDENCE_ROLES: frozenset[str] = frozenset({"supporting", "contradicting", "context"})


@dataclass(slots=True)
class KnowledgeUnit:
    """A durable, structured knowledge item extracted from a temporary input."""

    unit_key: str
    unit_version: int = 1
    unit_type: str = "fact"
    title: str | None = None
    content: str = ""
    source_channel: str = "manual"
    source_video_id: str | None = None
    source_url: str | None = None
    source_link: str | None = None
    start_sec: float | None = None
    end_sec: float | None = None
    content_hash: str | None = None
    review_status: str = "unreviewed"
    blogger_key: str | None = None
    resource_key: str | None = None
    facts: dict[str, Any] = field(default_factory=dict)
    entities: list[Any] = field(default_factory=list)
    topics: list[Any] = field(default_factory=list)
    evidence_refs: list[Any] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)
    status: str = "active"
    truth_status: str = "unknown"
    extension: dict[str, Any] = field(default_factory=dict)
    unit_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate stable identifiers and normalize mutable defaults."""
        self.unit_key = _required(self.unit_key, "unit_key")
        self.unit_version = int(self.unit_version)
        if self.unit_version < 1:
            raise ValueError("unit_version must be >= 1")
        self.unit_type = _required(self.unit_type, "unit_type")
        self.content = str(self.content or "")
        self.source_channel = _required(self.source_channel, "source_channel").lower()
        if self.source_channel not in SOURCE_CHANNELS:
            raise ValueError(f"source_channel must be one of {sorted(SOURCE_CHANNELS)}")
        self.review_status = _required(self.review_status, "review_status").lower()
        if self.review_status not in REVIEW_STATUSES:
            raise ValueError(f"review_status must be one of {sorted(REVIEW_STATUSES)}")
        for name in ("start_sec", "end_sec"):
            value = getattr(self, name)
            if value is not None and (
                not math.isfinite(float(value)) or float(value) < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")
        if (
            self.start_sec is not None
            and self.end_sec is not None
            and float(self.end_sec) <= float(self.start_sec)
        ):
            raise ValueError("end_sec must be strictly greater than start_sec")
        if self.content_hash is None:
            self.content_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        else:
            self.content_hash = _required(self.content_hash, "content_hash")
        self.source_link = self.source_link or self.source_url
        self.truth_status = _required(self.truth_status, "truth_status").lower()
        if self.truth_status not in TRUTH_STATUSES:
            raise ValueError(f"truth_status must be one of {sorted(TRUTH_STATUSES)}")
        self.facts = dict(self.facts or {})
        self.extension = dict(self.extension or {})
        self.entities = list(self.entities or [])
        self.topics = list(self.topics or [])
        self.evidence_refs = list(self.evidence_refs or [])
        self.quality = dict(self.quality or {})


@dataclass(slots=True)
class BloggerHistory:
    """A point-in-time blogger profile/history observation."""

    blogger_key: str
    history_key: str
    observed_at: datetime
    event_type: str = "snapshot"
    summary: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    source_video_id: str | None = None
    source_unit_id: int | None = None
    history_id: int | None = None
    observation_hash: str | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate idempotency keys and normalize timestamps/attributes."""
        self.blogger_key = _required(self.blogger_key, "blogger_key")
        self.history_key = _required(self.history_key, "history_key")
        self.event_type = _required(self.event_type, "event_type")
        self.observed_at = _utc(self.observed_at)
        self.attributes = dict(self.attributes or {})
        if self.observation_hash is None:
            payload = f"{self.observed_at.isoformat()}|{self.event_type}|{self.summary or ''}|{self.source_video_id or ''}|{self.source_unit_id or ''}|{_stable_json(self.attributes)}"
            self.observation_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        self.observation_hash = _required(self.observation_hash, "observation_hash")


@dataclass(slots=True)
class StrategyEvidence:
    """An idempotent link between a strategy and a knowledge unit."""

    strategy_key: str
    knowledge_unit_key: str
    evidence_key: str
    knowledge_unit_version: int = 1
    evidence_role: str = "supporting"
    quote: str | None = None
    quote_start_sec: float | None = None
    quote_end_sec: float | None = None
    evidence_status: str = "draft"
    snapshot_hash: str | None = None
    relevance: float | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    evidence_id: int | None = None
    created_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate keys and bounded confidence metrics."""
        self.strategy_key = _required(self.strategy_key, "strategy_key")
        self.knowledge_unit_key = _required(
            self.knowledge_unit_key, "knowledge_unit_key"
        )
        self.evidence_key = _required(self.evidence_key, "evidence_key")
        self.knowledge_unit_version = int(self.knowledge_unit_version)
        if self.knowledge_unit_version < 1:
            raise ValueError("knowledge_unit_version must be >= 1")
        self.evidence_role = _required(self.evidence_role, "evidence_role").lower()
        if self.evidence_role not in EVIDENCE_ROLES:
            raise ValueError(f"evidence_role must be one of {sorted(EVIDENCE_ROLES)}")
        self.evidence_status = _required(
            self.evidence_status, "evidence_status"
        ).lower()
        if self.evidence_status not in EVIDENCE_STATUSES:
            raise ValueError(
                f"evidence_status must be one of {sorted(EVIDENCE_STATUSES)}"
            )
        for name in ("quote_start_sec", "quote_end_sec"):
            value = getattr(self, name)
            if value is not None and (
                not math.isfinite(float(value)) or float(value) < 0
            ):
                raise ValueError(f"{name} must be a finite non-negative number")
        if (
            self.quote_start_sec is not None
            and self.quote_end_sec is not None
            and float(self.quote_end_sec) <= float(self.quote_start_sec)
        ):
            raise ValueError(
                "quote_end_sec must be strictly greater than quote_start_sec"
            )
        if self.snapshot_hash is not None:
            self.snapshot_hash = _required(self.snapshot_hash, "snapshot_hash")
        if self.evidence_status == "valid" and not _required(self.quote or "", "quote"):
            raise ValueError("valid evidence must include a quote")
        self.metadata = dict(self.metadata or {})
        for name in ("relevance", "confidence"):
            value = getattr(self, name)
            if value is not None and not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(slots=True)
class KnowledgeResource:
    """Durable metadata for a source resource; media path is temporary only."""

    resource_key: str
    blogger_key: str | None = None
    source_url: str | None = None
    platform_content_id: str | None = None
    published_at: datetime | None = None
    duration_sec: float | None = None
    status: str = "candidate"
    temporary_media_path: str | None = None
    media_expires_at: datetime | None = None
    asr_text: str | None = None
    ocr_text: str | None = None
    subtitle_text: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    resource_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        self.resource_key = _required(self.resource_key, "resource_key")
        self.status = _required(self.status, "status").lower()
        if self.status not in RESOURCE_STATUSES:
            raise ValueError(f"status must be one of {sorted(RESOURCE_STATUSES)}")
        if self.duration_sec is not None and (
            not math.isfinite(float(self.duration_sec)) or float(self.duration_sec) < 0
        ):
            raise ValueError("duration_sec must be a finite non-negative number")
        for name in ("asr_text", "ocr_text", "subtitle_text"):
            value = getattr(self, name)
            if value is not None:
                setattr(self, name, str(value))
        self.metadata = dict(self.metadata or {})
        if self.published_at is not None:
            self.published_at = _utc(self.published_at)
        if self.media_expires_at is not None:
            self.media_expires_at = _utc(self.media_expires_at)


@dataclass(slots=True)
class IndexTask:
    """Asynchronous fan-out index state for PG, Qdrant and graph targets."""

    resource_key: str
    target: str
    status: str = "pending"
    processing_run_key: str | None = None
    error_message: str | None = None
    attempt_count: int = 0
    task_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def __post_init__(self) -> None:
        self.resource_key = _required(self.resource_key, "resource_key")
        self.target = _required(self.target, "target").lower()
        if self.target not in {"pg", "qdrant", "graph"}:
            raise ValueError("target must be pg, qdrant or graph")
        if self.status not in INDEX_STATUSES:
            raise ValueError(f"status must be one of {sorted(INDEX_STATUSES)}")
        if int(self.attempt_count) < 0:
            raise ValueError("attempt_count must be non-negative")


def row_to_knowledge_resource(row: dict[str, Any]) -> KnowledgeResource:
    """Convert a resource row mapping."""
    return KnowledgeResource(
        resource_id=row.get("resource_id"),
        resource_key=row["resource_key"],
        blogger_key=row.get("blogger_key"),
        source_url=row.get("source_url"),
        platform_content_id=row.get("platform_content_id"),
        published_at=_row_time(row, "published_at"),
        duration_sec=row.get("duration_sec"),
        status=row.get("status", "candidate"),
        temporary_media_path=row.get("temporary_media_path"),
        media_expires_at=_row_time(row, "media_expires_at"),
        asr_text=row.get("asr_text"),
        ocr_text=row.get("ocr_text"),
        subtitle_text=row.get("subtitle_text"),
        metadata=row.get("metadata") or {},
        created_at=_row_time(row, "created_at"),
        updated_at=_row_time(row, "updated_at"),
    )


def row_to_index_task(row: dict[str, Any]) -> IndexTask:
    """Convert an index task row mapping."""
    return IndexTask(
        task_id=row.get("task_id"),
        resource_key=row["resource_key"],
        target=row["target"],
        status=row.get("status", "pending"),
        processing_run_key=row.get("processing_run_key"),
        error_message=row.get("error_message"),
        attempt_count=int(row.get("attempt_count", 0) or 0),
        created_at=_row_time(row, "created_at"),
        updated_at=_row_time(row, "updated_at"),
    )


def _row_time(row: dict[str, Any], key: str) -> datetime | None:
    value = row.get(key)
    return _utc(value) if isinstance(value, datetime) else value


def row_to_knowledge_unit(row: dict[str, Any]) -> KnowledgeUnit:
    """Convert a database row mapping to :class:`KnowledgeUnit`."""
    return KnowledgeUnit(
        unit_id=row.get("unit_id"),
        unit_key=row["unit_key"],
        unit_version=int(row.get("unit_version", 1) or 1),
        unit_type=row.get("unit_type", "fact"),
        title=row.get("title"),
        content=row.get("content", ""),
        source_channel=row.get("source_channel", "manual"),
        source_video_id=row.get("source_video_id"),
        source_url=row.get("source_url"),
        source_link=row.get("source_link"),
        start_sec=row.get("start_sec"),
        end_sec=row.get("end_sec"),
        content_hash=row.get("content_hash"),
        review_status=row.get("review_status", "unreviewed"),
        blogger_key=row.get("blogger_key"),
        resource_key=row.get("resource_key"),
        facts=row.get("facts") or {},
        entities=row.get("entities") or [],
        topics=row.get("topics") or [],
        evidence_refs=row.get("evidence_refs") or [],
        quality=row.get("quality") or {},
        status=row.get("status", "active"),
        truth_status=row.get("truth_status", "unknown"),
        extension=row.get("extension") or {},
        created_at=_row_time(row, "created_at"),
        updated_at=_row_time(row, "updated_at"),
    )


def row_to_blogger_history(row: dict[str, Any]) -> BloggerHistory:
    """Convert a database row mapping to :class:`BloggerHistory`."""
    return BloggerHistory(
        history_id=row.get("history_id"),
        blogger_key=row["blogger_key"],
        history_key=row["history_key"],
        observed_at=_utc(row["observed_at"]),
        event_type=row.get("event_type", "snapshot"),
        summary=row.get("summary"),
        attributes=row.get("attributes") or {},
        source_video_id=row.get("source_video_id"),
        source_unit_id=row.get("source_unit_id"),
        observation_hash=row.get("observation_hash"),
        created_at=_row_time(row, "created_at"),
    )


def row_to_strategy_evidence(row: dict[str, Any]) -> StrategyEvidence:
    """Convert a database row mapping to :class:`StrategyEvidence`."""
    return StrategyEvidence(
        evidence_id=row.get("evidence_id"),
        strategy_key=row["strategy_key"],
        knowledge_unit_key=row["knowledge_unit_key"],
        evidence_key=row["evidence_key"],
        knowledge_unit_version=int(row.get("knowledge_unit_version", 1) or 1),
        evidence_role=row.get("evidence_role", "supporting"),
        quote=row.get("quote"),
        quote_start_sec=row.get("quote_start_sec"),
        quote_end_sec=row.get("quote_end_sec"),
        evidence_status=row.get("evidence_status", "draft"),
        snapshot_hash=row.get("snapshot_hash"),
        relevance=row.get("relevance"),
        confidence=row.get("confidence"),
        metadata=row.get("metadata") or {},
        created_at=_row_time(row, "created_at"),
    )


__all__ = [
    "SOURCE_CHANNELS",
    "REVIEW_STATUSES",
    "RESOURCE_STATUSES",
    "EVIDENCE_STATUSES",
    "INDEX_STATUSES",
    "TRUTH_STATUSES",
    "EVIDENCE_ROLES",
    "KnowledgeUnit",
    "KnowledgeResource",
    "IndexTask",
    "BloggerHistory",
    "StrategyEvidence",
    "row_to_knowledge_unit",
    "row_to_knowledge_resource",
    "row_to_index_task",
    "row_to_blogger_history",
    "row_to_strategy_evidence",
]
