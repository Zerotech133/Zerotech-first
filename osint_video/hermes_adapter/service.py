"""Minimal, injectable Hermes adapter orchestration."""
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_METADATA_KEYS = frozenset({
    "authorization", "auth", "cookie", "password", "passwd", "secret",
    "token", "access_token", "refresh_token", "api_key", "apikey", "path",
})

from hermes_adapter import dto
from hermes_adapter.ports import AIProvider, ContentFetcher, CreatorRunStore, EventSink


class HermesAdapter:
    """Receive a link task, create a creator run, summarize supplied content.

    The service has no network, media downloader, or Hermes SDK dependency.  A
    caller injects a fetcher, provider, run store, and optional event sink.
    """

    def __init__(
        self,
        fetcher: ContentFetcher,
        provider: AIProvider,
        run_store: CreatorRunStore,
        event_sink: EventSink | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._provider = provider
        self._run_store = run_store
        self._event_sink = event_sink
        self._published_event_ids: set[str] = set()

    def handle_link(self, task: dto.HermesTask) -> dto.HermesResult:
        """Process one task and return summary/evidence/index DTOs."""
        # Do not emit raw links: event sinks may persist or forward payloads.
        received = self._event(task, "task.received", None, {"url_hash": hashlib.sha256(task.url.strip().encode("utf-8")).hexdigest()})
        run_row = self._run_store.create_run(task.target_id, task.run_type)
        run_id = int(run_row.get("run_id", run_row.get("id", 0)))
        if run_id < 1:
            raise ValueError("run store returned an invalid run id")
        run = dto.CreatorRun(
            run_id=run_id,
            target_id=int(run_row.get("target_id", task.target_id)),
            run_type=str(run_row.get("run_type", task.run_type)),
            state=str(run_row.get("state", "pending")),
            input_cursor=run_row.get("input_cursor"),
        )
        started = self._run_store.start_run(run.run_id)
        if started is not None:
            run = dto.CreatorRun(
                run_id=run.run_id,
                target_id=run.target_id,
                run_type=run.run_type,
                state=str(started.get("state", "running")),
                input_cursor=started.get("input_cursor", run.input_cursor),
            )
        running = self._event(task, "run.started", run.run_id, {})
        try:
            raw = self._fetcher.fetch(task)
            content, evidence = self._normalize_content(raw)
            summary = self._provider.summarize(task, content, evidence)
            index_task = dto.IndexTask(
                task_id=f"index-{task.task_id}-{summary.snapshot_hash[:12]}",
                target_id=task.target_id,
                run_id=run.run_id,
                summary_snapshot_hash=summary.snapshot_hash,
                payload={"evidence_ids": [item.evidence_id for item in summary.evidence]},
            )
        except Exception as exc:
            # Keep creator-run state durable even when an injected dependency fails.
            self._run_store.finish_run(
                run.run_id,
                state="failed",
                items_seen=0,
                items_succeeded=0,
                items_failed=1,
                error_code="HERMES_ADAPTER_FAILED",
                error_detail=str(exc),
            )
            self._event(task, "run.failed", run.run_id, {"error": str(exc)})
            raise
        finished_row = self._run_store.finish_run(
            run.run_id,
            state="succeeded",
            items_seen=len(summary.evidence),
            items_succeeded=len(summary.evidence),
            items_failed=0,
            summary_version=None,
        )
        if finished_row is not None:
            run = dto.CreatorRun(run.run_id, run.target_id, run.run_type, str(finished_row.get("state", "succeeded")), run.input_cursor)
        completed = self._event(task, "run.completed", run.run_id, {"index_task_id": index_task.task_id})
        return dto.HermesResult(task, run, summary, index_task, (received, running, completed))

    def _event(self, task: dto.HermesTask, event_type: str, run_id: int | None, payload: Mapping[str, Any]) -> dto.TaskEvent:
        event = dto.TaskEvent(uuid.uuid4().hex, task.task_id, event_type, run_id, payload=dict(payload))
        if self._event_sink is not None:
            self._event_sink.publish(event)
        return event

    @staticmethod
    def _normalize_content(raw: Any) -> tuple[dict[str, Any], tuple[dto.Evidence, ...]]:
        if isinstance(raw, Mapping):
            content = dict(raw)
            raw_evidence = content.pop("evidence", ())
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
            content = {"items": list(raw)}
            raw_evidence = raw
        else:
            raise TypeError("fetcher must return a mapping or sequence")
        evidence: list[dto.Evidence] = []
        for index, item in enumerate(raw_evidence or ()):
            if isinstance(item, dto.Evidence):
                evidence.append(item)
            elif isinstance(item, Mapping):
                evidence.append(dto.Evidence(
                    evidence_id=str(item.get("evidence_id", f"evidence-{index}")),
                    source_ref=str(item.get("source_ref", "")),
                    quote=str(item.get("quote", "")),
                    locator=item.get("locator"),
                    confidence=item.get("confidence"),
                    metadata=item.get("metadata", {}),
                ))
        return content, tuple(evidence)


def snapshot_hash(text: str, evidence: Sequence[dto.Evidence]) -> str:
    """Compute deterministic content hash for providers that need one."""
    material = text + "\n" + "\n".join(item.source_ref + item.quote for item in evidence)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()
