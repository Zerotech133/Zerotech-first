"""Pure unit tests for the dependency-injected Hermes adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import pytest

from hermes_adapter import Evidence, HermesAdapter, HermesTask, Summary, snapshot_hash


@dataclass
class FakeStore:
    rows: dict[str, Any]

    def create_run(
        self,
        target_id: int,
        run_type: str,
        input_cursor: str | None = None,
    ) -> Mapping[str, Any]:
        return {
            "run_id": 42,
            "target_id": target_id,
            "run_type": run_type,
            "state": "pending",
            "input_cursor": input_cursor,
        }

    def start_run(self, run_id: int) -> Mapping[str, Any] | None:
        self.rows["started"] = run_id
        return {"run_id": run_id, "state": "running"}

    def finish_run(self, run_id: int, **kwargs: Any) -> Mapping[str, Any] | None:
        self.rows["finished"] = (run_id, kwargs)
        return {"run_id": run_id, "state": kwargs["state"]}


class FakeFetcher:
    def fetch(self, task: HermesTask) -> Mapping[str, Any]:
        return {
            "title": "sample",
            "evidence": [
                {
                    "evidence_id": "e1",
                    "source_ref": task.url,
                    "quote": "verified quote",
                }
            ],
        }


class FakeProvider:
    def summarize(
        self,
        task: HermesTask,
        content: Mapping[str, Any],
        evidence: tuple[Evidence, ...],
    ) -> Summary:
        assert content["title"] == "sample"
        assert len(evidence) == 1
        text = "A concise summary"
        return Summary(
            text=text,
            topics=("topic",),
            snapshot_hash=snapshot_hash(text, evidence),
            evidence=evidence,
        )


class FakeSink:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def publish(self, event: Any) -> None:
        self.events.append(event)


def make_task() -> HermesTask:
    return HermesTask("task-1", 7, "https://example.invalid/video")


def test_handle_link_creates_run_and_emits_structured_result() -> None:
    store = FakeStore({})
    sink = FakeSink()

    result = HermesAdapter(FakeFetcher(), FakeProvider(), store, sink).handle_link(
        make_task()
    )

    assert result.run.run_id == 42
    assert result.run.state == "succeeded"
    assert result.summary.evidence[0].source_ref.startswith("https://")
    assert result.index_task.target_id == 7
    assert [event.event_type for event in sink.events] == [
        "task.received",
        "run.started",
        "run.completed",
    ]
    assert "url" not in sink.events[0].payload
    assert len(sink.events[0].payload["url_hash"]) == 64
    assert store.rows["started"] == 42
    assert store.rows["finished"][0] == 42


@pytest.mark.parametrize(
    ("factory", "message"),
    [
        (lambda: HermesTask("", 1, "https://example.invalid"), "task_id"),
        (lambda: HermesTask("t", 0, "https://example.invalid"), "target_id"),
        (lambda: HermesTask("t", 1, "  "), "url"),
        (
            lambda: HermesTask("t", 1, "https://example.invalid", run_type=" "),
            "run_type",
        ),
        (
            lambda: HermesTask(
                "t", 1, "https://example.invalid", metadata={"token": "secret"}
            ),
            "sensitive metadata",
        ),
        (lambda: Evidence("", "source", "quote"), "evidence_id"),
        (lambda: Evidence("e", "", "quote"), "source_ref"),
        (lambda: Evidence("e", "source", " "), "evidence quote"),
        (lambda: Evidence("e", "source", "quote", confidence=-0.01), "confidence"),
        (lambda: Evidence("e", "source", "quote", confidence=1.01), "confidence"),
        (lambda: Summary(" ", snapshot_hash="hash"), "summary text"),
        (lambda: Summary("text"), "snapshot_hash"),
    ],
)
def test_dtos_reject_invalid_values(factory: Any, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        factory()


def test_normalize_sequence_preserves_evidence() -> None:
    evidence = Evidence("e1", "ref", "quote")
    content, normalized = HermesAdapter._normalize_content(
        [evidence, {"evidence_id": "e2", "source_ref": "ref2", "quote": "q2"}]
    )

    assert content == {
        "items": [evidence, {"evidence_id": "e2", "source_ref": "ref2", "quote": "q2"}]
    }
    assert normalized == (
        evidence,
        Evidence("e2", "ref2", "q2"),
    )


@pytest.mark.parametrize("raw", [None, "text", b"bytes", 1])
def test_normalize_rejects_non_mapping_or_sequence(raw: Any) -> None:
    with pytest.raises(TypeError, match="mapping or sequence"):
        HermesAdapter._normalize_content(raw)


def test_invalid_run_id_fails_before_fetching() -> None:
    class InvalidStore(FakeStore):
        def create_run(
            self, target_id: int, run_type: str, input_cursor: str | None = None
        ) -> Mapping[str, Any]:
            return {"run_id": 0}

    class NeverFetcher:
        def fetch(self, task: HermesTask) -> Mapping[str, Any]:
            raise AssertionError("fetcher must not be called")

    with pytest.raises(ValueError, match="invalid run id"):
        HermesAdapter(NeverFetcher(), FakeProvider(), InvalidStore({})).handle_link(
            make_task()
        )


def test_snapshot_hash_is_deterministic_and_changes_with_content() -> None:
    evidence = (Evidence("e", "source", "quote"),)

    assert snapshot_hash("summary", evidence) == snapshot_hash("summary", evidence)
    assert snapshot_hash("summary", evidence) != snapshot_hash("other", evidence)
    assert snapshot_hash("summary", evidence) != snapshot_hash(
        "summary", (Evidence("e", "other", "quote"),)
    )


def test_repeated_handle_link_should_not_duplicate_event_ids() -> None:
    """The adapter contract requires idempotent lifecycle event publication."""
    store = FakeStore({})
    sink = FakeSink()
    adapter = HermesAdapter(FakeFetcher(), FakeProvider(), store, sink)

    adapter.handle_link(make_task())
    adapter.handle_link(make_task())

    event_ids = [event.event_id for event in sink.events]
    assert len(event_ids) == len(set(event_ids)), (
        "duplicate task retries must be idempotent"
    )


def test_events_do_not_expose_sensitive_fields() -> None:
    sink = FakeSink()
    result = HermesAdapter(
        FakeFetcher(), FakeProvider(), FakeStore({}), sink
    ).handle_link(
        HermesTask(
            "task-1",
            7,
            "https://example.invalid/video",
            metadata={"source_label": "private-test"},
        )
    )

    for event in result.events:
        serialized = repr(event.payload).lower()
        assert "raw_path" not in serialized
        assert "cookie" not in serialized
        assert "token" not in serialized
        assert "value_enc" not in serialized
