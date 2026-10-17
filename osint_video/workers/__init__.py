"""Unified worker entry points.

``run`` remains callable for the historical ``from workers import run`` API.
Compatibility attributes on that callable preserve dotted monkeypatch paths
(``workers.run.DailyScanWorker``) without making the package attribute a module.
"""

from workers import run as _run_module
from workers.composition import CompositionError, WorkerComposition, compose_worker_dependencies

WorkerRunResult = _run_module.WorkerRunResult
run = _run_module.run
# Keep legacy monkeypatch/import paths working while retaining a callable API.
run.DailyScanWorker = _run_module.DailyScanWorker
run.HistoryBackfillWorker = _run_module.HistoryBackfillWorker
run.store_repository = _run_module.store_repository

__all__ = [
    "CompositionError",
    "WorkerComposition",
    "WorkerRunResult",
    "compose_worker_dependencies",
    "run",
]
