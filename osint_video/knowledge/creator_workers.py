"""Injectable creator history and daily scan workers.

Workers deliberately know nothing about a platform API or media files.  A caller
supplies a fetcher and processor, while :mod:`knowledge.creator_ops` owns the
PostgreSQL run state and waterlines.  A failed item stops only its run; the
cursor remains at the last successful item so a later retry cannot skip it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence

from common import db
from collectors.ports import CollectionAccessError
from knowledge import creator_ops


class PageFetcher(Protocol):
    """Fetch one page after ``cursor`` without performing persistence."""

    def __call__(self, target: Mapping[str, Any], cursor: str | None, run: Mapping[str, Any]) -> Any:
        """Return a page mapping or a sequence of items."""


class ItemProcessor(Protocol):
    """Persist one item and return an optional item cursor."""

    def __call__(self, item: Any, target: Mapping[str, Any], run: Mapping[str, Any]) -> Any:
        """Raise on failure; a returned cursor advances the waterline."""


@dataclass(frozen=True)
class WorkerResult:
    """Stable worker outcome suitable for logs and scheduler metrics."""

    run_id: int
    target_id: int
    state: str
    cursor: str | None
    items_seen: int
    items_succeeded: int
    items_failed: int
    error: str | None = None


def _mapping(value: Any) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _page(value: Any) -> tuple[list[Any], str | None, bool]:
    """Normalize common fetcher return shapes to ``items,next_cursor,done``."""
    if isinstance(value, Mapping):
        raw_items = value.get("items", value.get("data", []))
        items = list(raw_items) if isinstance(raw_items, Sequence) and not isinstance(raw_items, (str, bytes)) else []
        next_cursor = value.get("next_cursor", value.get("cursor"))
        if "done" in value:
            done = bool(value.get("done"))
        elif "has_more" in value:
            done = not bool(value.get("has_more"))
        else:
            # A page with no pagination metadata is a complete one-shot page.
            done = next_cursor is None
        return items, str(next_cursor) if next_cursor is not None else None, done
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return list(value), None, True
    raise TypeError("fetcher must return a mapping or sequence")


def _item_cursor(result: Any, item: Any) -> str | None:
    """Extract a cursor returned by processor or embedded in an item."""
    for value in (result, item):
        if isinstance(value, Mapping):
            cursor = value.get("cursor", value.get("output_cursor", value.get("next_cursor")))
            if cursor is not None:
                return str(cursor)
    if isinstance(result, str):
        return result
    return None


def _run_row(run_id: int) -> Mapping[str, Any] | None:
    rows = db.fetch_all("SELECT * FROM osint_creator_run WHERE run_id=:run_id", {"run_id": run_id})
    return rows[0] if rows else None


def _target_row(target_id: int) -> Mapping[str, Any] | None:
    rows = db.fetch_all("SELECT * FROM osint_target WHERE target_id=:target_id", {"target_id": target_id})
    return rows[0] if rows else None


def _advance_state(target: Mapping[str, Any], cursor: str | None, run_type: str) -> None:
    """Advance only known successful states; leave manual states untouched."""
    current = str(target.get("operation_state") or "")
    next_state: str | None = None
    if run_type == "history_backfill" and current == "history_backfill_running":
        next_state = "profile_summarized"
    elif run_type in {"daily_scan", "incremental_update"}:
        if current == "daily_monitoring":
            next_state = "incremental_update"
        elif current == "incremental_update":
            next_state = "daily_monitoring"
    if next_state is not None:
        creator_ops.advance_target(int(target["target_id"]), next_state, cursor=cursor)


def _execute_run(
    run: Mapping[str, Any],
    fetcher: PageFetcher,
    processor: ItemProcessor,
    *,
    max_items: int,
    finalize_state: bool,
) -> WorkerResult:
    """Execute one already-claimed run with item-level failure isolation."""
    run_id = int(run["run_id"])
    target_id = int(run["target_id"])
    started = creator_ops.start_run(run_id)
    if started is None:
        return WorkerResult(run_id, target_id, "skipped", run.get("output_cursor"), 0, 0, 0)
    target = _target_row(target_id)
    if target is None:
        detail = "target not found"
        creator_ops.finish_run(run_id, state="failed", error_code="TARGET_NOT_FOUND", error_detail=detail)
        return WorkerResult(run_id, target_id, "failed", None, 0, 0, 0, detail)

    cursor = started.get("input_cursor")
    seen = succeeded = failed = 0
    try:
        while seen < max_items:
            page_value = fetcher(target, cursor, started)
            items, page_cursor, done = _page(page_value)
            if not items:
                if page_cursor is not None:
                    cursor = page_cursor
                break
            for item in items:
                if seen >= max_items:
                    break
                seen += 1
                try:
                    result = processor(item, target, started)
                    succeeded += 1
                    cursor = _item_cursor(result, item) or cursor
                except Exception as exc:  # one bad item must not crash scheduler
                    failed += 1
                    detail = f"item {seen} failed: {exc}"
                    finished = creator_ops.finish_run(
                        run_id, state="failed", output_cursor=cursor,
                        items_seen=seen, items_succeeded=succeeded, items_failed=failed,
                        error_code="ITEM_PROCESS_FAILED", error_detail=detail,
                    )
                    return WorkerResult(run_id, target_id, str((finished or {}).get("state", "failed")), cursor, seen, succeeded, failed, detail)
            if page_cursor is not None and succeeded > 0:
                cursor = page_cursor
            if done or len(items) == 0:
                break
        finished = creator_ops.finish_run(
            run_id, state="succeeded", output_cursor=cursor,
            items_seen=seen, items_succeeded=succeeded, items_failed=failed,
        )
        if finalize_state:
            _advance_state(target, cursor, str(started.get("run_type")))
        return WorkerResult(run_id, target_id, str((finished or {}).get("state", "succeeded")), cursor, seen, succeeded, failed)
    except CollectionAccessError as exc:
        detail = f"collector access denied: {exc.code}"
        creator_ops.finish_run(
            run_id, state="failed", output_cursor=cursor,
            items_seen=seen, items_succeeded=succeeded, items_failed=failed,
            error_code=exc.code, error_detail=detail,
        )
        return WorkerResult(run_id, target_id, "failed", cursor, seen, succeeded, failed, detail)
    except Exception as exc:
        detail = str(exc)
        creator_ops.finish_run(
            run_id, state="failed", output_cursor=cursor,
            items_seen=seen, items_succeeded=succeeded, items_failed=failed,
            error_code="FETCH_FAILED", error_detail=detail,
        )
        return WorkerResult(run_id, target_id, "failed", cursor, seen, succeeded, failed, detail)


def run_history_backfill(
    run_id: int, fetcher: PageFetcher, processor: ItemProcessor,
    *, max_items: int = 1000, finalize_state: bool = True,
) -> WorkerResult:
    """Claim and execute one ``history_backfill`` run."""
    if max_items < 1:
        raise ValueError("max_items must be positive")
    run = _run_row(run_id)
    if run is None:
        return WorkerResult(run_id, 0, "missing", None, 0, 0, 0, "run not found")
    if str(run.get("run_type")) != "history_backfill":
        raise ValueError("run is not a history_backfill run")
    return _execute_run(run, fetcher, processor, max_items=max_items, finalize_state=finalize_state)


@dataclass
class DailyScanWorker:
    """Daily scheduler using ``claim_due_targets`` as a database lease."""

    fetcher: PageFetcher
    processor: ItemProcessor
    max_items: int = 1000

    def run_once(self, limit: int = 50) -> list[WorkerResult]:
        """Claim due targets once; a failure on one target does not stop others."""
        if limit < 1:
            raise ValueError("limit must be positive")
        claimed = creator_ops.claim_due_targets(limit)
        results: list[WorkerResult] = []
        for target in claimed:
            target_id = int(target["target_id"])
            claim_token = target.get("claim_token")
            result: WorkerResult
            try:
                run = creator_ops.create_run(target_id, "daily_scan", target.get("last_success_cursor"))
                result = _execute_run(run, self.fetcher, self.processor, max_items=self.max_items, finalize_state=True)
            except Exception as exc:  # isolate one target from the scheduler batch
                result = WorkerResult(0, target_id, "failed", target.get("last_success_cursor"), 0, 0, 0, str(exc))
            finally:
                if claim_token:
                    try:
                        creator_ops.release_target_claim(
                            target_id, str(claim_token),
                            success=result.state == "succeeded" if "result" in locals() else False,
                        )
                    except Exception:
                        pass
            results.append(result)
        return results


@dataclass
class HistoryBackfillWorker:
    """Small adapter for queue/scheduler integration and dependency injection."""

    fetcher: PageFetcher
    processor: ItemProcessor
    max_items: int = 1000

    def run(self, run_id: int) -> WorkerResult:
        """Execute a pre-created history run."""
        return run_history_backfill(run_id, self.fetcher, self.processor, max_items=self.max_items)


__all__ = [
    "PageFetcher", "ItemProcessor", "WorkerResult", "HistoryBackfillWorker",
    "DailyScanWorker", "run_history_backfill",
]
