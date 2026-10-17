"""Platform-neutral collector-to-knowledge persistence wiring.

The processor receives already-projected :class:`collectors.VideoPage` DTOs. It
never calls a platform API and never persists a video body; only metadata and
text-derived knowledge are written through an injected repository port.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
from typing import Any, Protocol

from collectors import CollectionFailureError, TextChannel, VideoPage
from store.models import KnowledgeResource, KnowledgeUnit, StrategyEvidence
from store.service import validate_evidence_for_resource


class KnowledgeWritePort(Protocol):
    """Minimal repository contract required by :class:`ItemProcessor`."""

    def upsert_knowledge_resource(self, resource: KnowledgeResource) -> KnowledgeResource:
        """Persist resource metadata and source text."""

    def upsert_knowledge_unit(self, unit: KnowledgeUnit) -> KnowledgeUnit:
        """Persist one immutable knowledge-unit version."""

    def upsert_strategy_evidence(self, evidence: StrategyEvidence) -> StrategyEvidence:
        """Persist one evidence edge."""

    def register_index_tasks(self, resource_key: str, processing_run_key: str | None = None) -> list[Any]:
        """Register PG/Qdrant/graph fan-out tasks."""

    def transaction(self) -> Any:
        """Return a context manager spanning all writes in one transaction."""


@dataclass(frozen=True)
class ItemProcessResult:
    """Stable output of one collector page ingestion."""

    resource_key: str
    unit_keys: tuple[str, ...]
    evidence_keys: tuple[str, ...]
    index_task_count: int
    metadata_only: bool


class _NullTransaction:
    """Compatibility context for simple fake repositories without transactions."""

    def __enter__(self) -> "_NullTransaction":
        return self

    def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> bool:
        return False


class ItemProcessor:
    """Convert one collector page into idempotent durable knowledge records."""

    def __init__(self, repository: KnowledgeWritePort, processing_run_key: str | None = None) -> None:
        if repository is None:
            raise ValueError("repository is required")
        self.repository = repository
        self.processing_run_key = processing_run_key

    def __call__(self, item: Any, target: Any = None, run: Any = None) -> ItemProcessResult:
        """Support creator-worker processor shape while accepting a VideoPage."""
        if not isinstance(item, VideoPage):
            raise TypeError("ItemProcessor expects collectors.VideoPage")
        run_key = self.processing_run_key
        if isinstance(run, dict):
            run_key = str(run.get("run_key") or run.get("run_id") or run_key or "") or None
        return self.process_page(item, processing_run_key=run_key)

    def process_page(self, page: VideoPage, processing_run_key: str | None = None) -> ItemProcessResult:
        """Persist a page; media-unavailable pages are valid metadata-only input.

        CollectionFailureError is intentionally not caught: callers must mark the
        collection run failed. Validation occurs before any repository call so an
        invalid page cannot create partial facts.
        """
        if not isinstance(page, VideoPage):
            raise TypeError("page must be collectors.VideoPage")
        metadata = page.metadata
        resource_key = self._resource_key(page)
        resource = self._resource(page, resource_key)
        units, evidence = self._records(page, resource_key)
        for item in evidence:
            validate_evidence_for_resource(item, resource)
        # Construct every record before writes. CollectionFailureError remains a
        # hard failure and is never converted to metadata-only here. Real PG
        # repositories expose transaction(); simple fakes remain compatible.
        transaction_factory = getattr(self.repository, "transaction", None)
        context = transaction_factory() if callable(transaction_factory) else _NullTransaction()
        with context:
            persisted_resource = self.repository.upsert_knowledge_resource(resource)
            del persisted_resource
            persisted_units: list[KnowledgeUnit] = []
            for unit in units:
                persisted_units.append(self.repository.upsert_knowledge_unit(unit))
            for item in evidence:
                self.repository.upsert_strategy_evidence(item)
            run_key = processing_run_key or self.processing_run_key
            index_tasks = self.repository.register_index_tasks(resource_key, run_key)
        return ItemProcessResult(
            resource_key=resource_key,
            unit_keys=tuple(unit.unit_key for unit in persisted_units),
            evidence_keys=tuple(item.evidence_key for item in evidence),
            index_task_count=len(index_tasks),
            metadata_only=not bool(page.text_segments),
        )

    @staticmethod
    def _resource_key(page: VideoPage) -> str:
        metadata = page.metadata
        identity = metadata.content_id or metadata.source_link
        platform = metadata.platform or "unknown"
        digest = hashlib.sha256(f"{platform}|{identity}".encode("utf-8")).hexdigest()[:32]
        return f"resource:{platform}:{digest}"

    @staticmethod
    def _published_at(value: str | None) -> datetime | None:
        if not value:
            return None
        normalized = value.strip()
        try:
            parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)

    def _resource(self, page: VideoPage, resource_key: str) -> KnowledgeResource:
        metadata = page.metadata
        transcripts: dict[str, str] = {"asr": "", "ocr": "", "subtitle": ""}
        extra: list[str] = []
        for segment in page.text_segments:
            if segment.channel.value in transcripts:
                transcripts[segment.channel.value] += ("\n" if transcripts[segment.channel.value] else "") + segment.text
            else:
                extra.append(segment.text)
        resource_metadata: dict[str, Any] = {
            "platform": metadata.platform,
            "title": metadata.title,
            "description": metadata.description,
            "media_status": metadata.media_status.value,
            "done": page.done,
            "next_cursor": page.next_cursor,
        }
        if extra:
            resource_metadata["other_text"] = extra
        creator = metadata.creator
        blogger_key = creator.creator_id if creator else None
        return KnowledgeResource(
            resource_key=resource_key,
            blogger_key=blogger_key,
            source_url=metadata.source_link,
            platform_content_id=metadata.content_id,
            published_at=self._published_at(metadata.published_at),
            duration_sec=metadata.duration_sec,
            asr_text=transcripts["asr"] or None,
            ocr_text=transcripts["ocr"] or None,
            subtitle_text=transcripts["subtitle"] or None,
            metadata=resource_metadata,
        )

    @staticmethod
    def _records(page: VideoPage, resource_key: str) -> tuple[list[KnowledgeUnit], list[StrategyEvidence]]:
        units: list[KnowledgeUnit] = []
        evidence: list[StrategyEvidence] = []
        for index, segment in enumerate(page.text_segments):
            channel = segment.channel.value
            source_channel = channel if channel in {"asr", "ocr", "subtitle"} else "manual"
            identity = f"{resource_key}|{index}|{channel}|{segment.start_sec}|{segment.end_sec}|{segment.text}"
            digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
            unit_key = f"unit:{digest[:32]}"
            snapshot_hash = hashlib.sha256(segment.text.encode("utf-8")).hexdigest()
            evidence_key = f"evidence:{digest[:32]}"
            edge = StrategyEvidence(
                # StrategyEvidence is a strategy→unit mapping. This wiring uses
                # an explicit source-content strategy namespace, not an external
                # strategy or fabricated claim.
                strategy_key=f"source_content:{resource_key}",
                knowledge_unit_key=unit_key,
                evidence_key=evidence_key,
                evidence_role="supporting",
                quote=segment.text,
                quote_start_sec=segment.start_sec,
                quote_end_sec=segment.end_sec,
                evidence_status="valid",
                snapshot_hash=snapshot_hash,
                confidence=segment.confidence,
            )
            unit = KnowledgeUnit(
                unit_key=unit_key,
                unit_type="transcript",
                title=page.metadata.title,
                content=segment.text,
                source_channel=source_channel,
                source_url=page.metadata.source_link,
                source_video_id=page.metadata.content_id,
                resource_key=resource_key,
                start_sec=segment.start_sec,
                end_sec=segment.end_sec,
                evidence_refs=[evidence_key],
                quality={"channel": channel, "confidence": segment.confidence},
            )
            units.append(unit)
            evidence.append(edge)
        return units, evidence


__all__ = ["ItemProcessResult", "ItemProcessor", "KnowledgeWritePort"]
