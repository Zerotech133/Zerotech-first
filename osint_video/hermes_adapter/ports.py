"""Dependency-injection ports for Hermes integration."""
from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from hermes_adapter.dto import Evidence, HermesTask, Summary


class ContentFetcher(Protocol):
    """Fetch already-available textual content and provenance for a task."""

    def fetch(self, task: HermesTask) -> Mapping[str, Any] | Sequence[Mapping[str, Any]]:
        """Return provider input; implementations must not fabricate media."""


class AIProvider(Protocol):
    """AI summarization abstraction, intentionally independent of any SDK."""

    def summarize(self, task: HermesTask, content: Mapping[str, Any], evidence: tuple[Evidence, ...]) -> Summary:
        """Generate a validated structured summary."""


class CreatorRunStore(Protocol):
    """Persistence boundary for creator runs."""

    def create_run(self, target_id: int, run_type: str, input_cursor: str | None = None) -> Mapping[str, Any]:
        """Create or return an idempotent pending run."""

    def start_run(self, run_id: int) -> Mapping[str, Any] | None:
        """Transition pending run to running."""

    def finish_run(self, run_id: int, **kwargs: Any) -> Mapping[str, Any] | None:
        """Finish a running run."""


class EventSink(Protocol):
    """Optional event sink used by Hermes integration tests or production bridge."""

    def publish(self, event: Any) -> None:
        """Publish one adapter event."""
