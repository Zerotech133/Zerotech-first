"""Unified safe entry point for scheduled worker modes.

This module is an orchestration boundary only.  It never invents platform
responses: each mode requires an injected callable, and missing dependencies
return a failed result without touching the database.
"""
from __future__ import annotations

from dataclasses import dataclass
import argparse
import json
from typing import Any, Callable

from knowledge.creator_workers import DailyScanWorker, HistoryBackfillWorker, WorkerResult
from store import repository as store_repository


@dataclass(frozen=True)
class WorkerRunResult:
    """Uniform result returned by all worker modes."""

    mode: str
    state: str
    results: tuple[Any, ...] = ()
    error: str | None = None


def _missing(mode: str, message: str) -> WorkerRunResult:
    return WorkerRunResult(mode=mode, state="failed", error=message)


def run(
    mode: str,
    *,
    fetcher: Callable[..., Any] | None = None,
    processor: Callable[..., Any] | None = None,
    adapter: Callable[..., Any] | None = None,
    run_id: int | None = None,
    limit: int = 50,
    max_items: int = 1000,
    target: str | None = None,
) -> WorkerRunResult:
    """Run one supported worker mode with injected external dependencies.

    Supported modes are ``daily-scan``, ``history-backfill`` and
    ``index-replay``.  Missing fetchers/processors/adapters fail safely and do
    not claim a run or fabricate a successful result.
    """
    normalized = str(mode or "").strip().lower().replace("_", "-")
    if normalized == "daily-scan":
        if not callable(fetcher) or not callable(processor):
            return _missing(normalized, "daily-scan requires callable fetcher and processor")
        if limit < 1 or max_items < 1:
            return _missing(normalized, "limit and max_items must be positive")
        try:
            results = DailyScanWorker(fetcher, processor, max_items=max_items).run_once(limit=limit)
        except Exception as exc:  # scheduler boundary must remain observable
            return _missing(normalized, str(exc))
        state = "succeeded" if all(item.state == "succeeded" for item in results) else ("idle" if not results else "failed")
        return WorkerRunResult(normalized, state, tuple(results))

    if normalized == "history-backfill":
        if not callable(fetcher) or not callable(processor):
            return _missing(normalized, "history-backfill requires callable fetcher and processor")
        if run_id is None:
            return _missing(normalized, "history-backfill requires run_id")
        if max_items < 1:
            return _missing(normalized, "max_items must be positive")
        try:
            result = HistoryBackfillWorker(fetcher, processor, max_items=max_items).run(int(run_id))
        except Exception as exc:
            return _missing(normalized, str(exc))
        return WorkerRunResult(normalized, result.state, (result,), result.error)

    if normalized == "index-replay":
        if not callable(adapter):
            return _missing(normalized, "index-replay requires callable adapter")
        if limit < 1:
            return _missing(normalized, "limit must be positive")
        try:
            results = store_repository.replay_index_tasks(adapter, limit=limit, target=target)
        except Exception as exc:
            return _missing(normalized, str(exc))
        state = "succeeded" if all(getattr(item, "status", "") == "succeeded" for item in results) else ("idle" if not results else "failed")
        return WorkerRunResult(normalized, state, tuple(results))

    return _missing(normalized, f"unsupported worker mode: {mode}")


def main(argv: list[str] | None = None) -> int:
    """CLI validation entry point; real dependencies must be wired by a host service."""
    parser = argparse.ArgumentParser(description="osint_video worker dispatcher")
    parser.add_argument("--mode", required=True, choices=("daily-scan", "history-backfill", "index-replay"))
    parser.add_argument("--run-id", type=int)
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--max-items", type=int, default=1000)
    parser.add_argument("--target")
    args = parser.parse_args(argv)
    result = run(args.mode, run_id=args.run_id, limit=args.limit, max_items=args.max_items, target=args.target)
    print(json.dumps({"mode": result.mode, "state": result.state, "error": result.error}, ensure_ascii=False))
    return 0 if result.state in {"succeeded", "idle"} else 2


__all__ = ["WorkerRunResult", "run", "main"]

if __name__ == "__main__":
    raise SystemExit(main())
