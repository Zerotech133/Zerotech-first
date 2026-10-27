"""Integration-style tests for collector-to-knowledge wiring with a fake port."""

from __future__ import annotations

import pytest

from collectors import (
    CollectionFailureError,
    MediaAvailability,
    TextChannel,
    TextSegment,
    VideoMetadata,
    VideoPage,
)
from knowledge.item_processor import ItemProcessor


class FakeRepository:
    def __init__(self) -> None:
        self.resources = {}
        self.units = {}
        self.evidence = {}
        self.index_calls = []
        self.fail_resource = None

    def upsert_knowledge_resource(self, resource):
        if self.fail_resource is not None:
            raise self.fail_resource
        self.resources[resource.resource_key] = resource
        return resource

    def upsert_knowledge_unit(self, unit):
        self.units[(unit.unit_key, unit.unit_version)] = unit
        return unit

    def upsert_strategy_evidence(self, evidence):
        self.evidence[evidence.evidence_key] = evidence
        return evidence

    def register_index_tasks(self, resource_key, processing_run_key=None):
        self.index_calls.append((resource_key, processing_run_key))
        return ["pg", "qdrant", "graph"]


def page(media_status=MediaAvailability.AVAILABLE, segments=None):
    return VideoPage(
        metadata=VideoMetadata(
            source_link="https://example.test/video/one",
            platform="test",
            content_id="one",
            title="Demo",
            published_at="2025-01-01T00:00:00Z",
            duration_sec=8,
            media_status=media_status,
        ),
        text_segments=tuple(
            segments
            if segments is not None
            else (
                TextSegment("原始转写", TextChannel.ASR, 0, 2, 0.9),
                TextSegment("屏幕文字", TextChannel.OCR, 2, 4, 0.8),
                TextSegment("字幕", TextChannel.SUBTITLE, 4, 6, 0.95),
            )
        ),
    )


def test_process_is_idempotent_and_registers_indexes() -> None:
    repo = FakeRepository()
    processor = ItemProcessor(repo, processing_run_key="run-1")
    first = processor.process_page(page())
    second = processor.process_page(page())
    assert first == second
    assert len(repo.resources) == 1
    assert len(repo.units) == 3
    assert len(repo.evidence) == 3
    assert repo.index_calls == [
        (first.resource_key, "run-1"),
        (first.resource_key, "run-1"),
    ]
    resource = next(iter(repo.resources.values()))
    assert resource.asr_text == "原始转写"
    assert resource.ocr_text == "屏幕文字"
    assert resource.subtitle_text == "字幕"
    assert all(
        item.strategy_key == f"source_content:{first.resource_key}"
        for item in repo.evidence.values()
    )
    assert all(
        unit.evidence_refs == [f"evidence:{unit.unit_key.split(':', 1)[1]}"]
        for unit in repo.units.values()
    )


def test_media_unavailable_still_persists_metadata_only() -> None:
    repo = FakeRepository()
    result = ItemProcessor(repo).process_page(
        VideoPage(metadata=page(MediaAvailability.UNAVAILABLE).metadata)
    )
    assert result.metadata_only is True
    assert len(repo.resources) == 1
    assert not repo.units
    assert len(repo.index_calls) == 1


def test_collection_failure_is_not_degraded_or_indexed() -> None:
    repo = FakeRepository()
    repo.fail_resource = CollectionFailureError("COLLECTION_FAILED", "denied")
    with pytest.raises(CollectionFailureError):
        ItemProcessor(repo).process_page(page())
    assert not repo.resources
    assert not repo.units
    assert not repo.evidence
    assert not repo.index_calls


def test_invalid_quote_bounds_are_rejected_before_any_write() -> None:
    repo = FakeRepository()
    invalid = page(segments=(TextSegment("越界文本", TextChannel.ASR, 7, 9, 0.9),))
    with pytest.raises(ValueError, match="must not exceed resource duration"):
        ItemProcessor(repo).process_page(invalid)
    assert not repo.resources
    assert not repo.units
    assert not repo.evidence
    assert not repo.index_calls


def test_transaction_rolls_back_all_writes_when_index_registration_fails() -> None:
    class AtomicFakeRepository(FakeRepository):
        def __init__(self):
            super().__init__()
            self._snapshot = None

        def transaction(self):
            return self

        def __enter__(self):
            self._snapshot = (
                dict(self.resources),
                dict(self.units),
                dict(self.evidence),
                list(self.index_calls),
            )
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            if exc_type is not None:
                self.resources, self.units, self.evidence, self.index_calls = (
                    self._snapshot
                )
            return False

        def register_index_tasks(self, resource_key, processing_run_key=None):
            raise RuntimeError("index registration failed")

    repo = AtomicFakeRepository()
    with pytest.raises(RuntimeError, match="index registration failed"):
        ItemProcessor(repo).process_page(page())
    assert not repo.resources
    assert not repo.units
    assert not repo.evidence
    assert not repo.index_calls
