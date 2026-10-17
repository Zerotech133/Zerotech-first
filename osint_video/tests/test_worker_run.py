"""Tests for unified worker entry safety and dependency injection."""
from __future__ import annotations

from types import SimpleNamespace

from workers import run


def test_missing_dependency_fails_without_call(monkeypatch):
    called = []
    monkeypatch.setattr("workers.run.DailyScanWorker.run_once", lambda *args, **kwargs: called.append(True))
    result = run("daily-scan")
    assert result.state == "failed"
    assert called == []


def test_history_requires_run_id():
    result = run("history-backfill", fetcher=lambda *a: [], processor=lambda *a: None)
    assert result.state == "failed"
    assert "run_id" in (result.error or "")


def test_index_replay_injected_adapter(monkeypatch):
    monkeypatch.setattr("workers.run.store_repository.replay_index_tasks", lambda adapter, limit, target: [SimpleNamespace(status="succeeded")])
    result = run("index-replay", adapter=lambda resource, target: None)
    assert result.state == "succeeded"
    assert len(result.results) == 1


def test_unsupported_mode_fails():
    result = run("real-platform")
    assert result.state == "failed"
    assert "unsupported" in (result.error or "")
