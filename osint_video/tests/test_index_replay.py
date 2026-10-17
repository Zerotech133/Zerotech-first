"""Tests for idempotent asynchronous index replay."""

from __future__ import annotations

from types import SimpleNamespace

from store import repository


def test_replay_success_and_failure(monkeypatch):
    claimed = [
        SimpleNamespace(resource_key="r1", target="qdrant"),
        SimpleNamespace(resource_key="r2", target="graph"),
    ]
    updates: list[tuple[str, str, str, str | None]] = []
    calls: list[tuple[str, str]] = []

    monkeypatch.setattr(repository, "claim_index_tasks", lambda limit=100, target=None: claimed)

    def adapter(resource_key: str, target: str) -> None:
        calls.append((resource_key, target))
        if resource_key == "r2":
            raise RuntimeError("graph unavailable")

    def update(resource_key: str, target: str, status: str, error_message: str | None = None):
        updates.append((resource_key, target, status, error_message))
        return SimpleNamespace(resource_key=resource_key, target=target, status=status)

    monkeypatch.setattr(repository, "update_index_task", update)
    result = repository.replay_index_tasks(adapter)

    assert calls == [("r1", "qdrant"), ("r2", "graph")]
    assert updates == [
        ("r1", "qdrant", "succeeded", None),
        ("r2", "graph", "failed", "graph unavailable"),
    ]
    assert [task.status for task in result] == ["succeeded", "failed"]


def test_replay_rejects_non_callable_adapter():
    try:
        repository.replay_index_tasks(None)
    except TypeError as exc:
        assert "callable" in str(exc)
    else:
        raise AssertionError("expected TypeError")
