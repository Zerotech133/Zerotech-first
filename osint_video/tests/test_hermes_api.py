"""No-PG unit tests for Hermes integration API handlers."""

from __future__ import annotations

from types import SimpleNamespace

from integration import hermes_api


def test_submit_links_uses_repository_and_sanitizes_tasks(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    task = SimpleNamespace(
        id=1,
        url_hash="abc",
        status="pending",
        route="AUTO",
        priority=5,
        created_at=None,
    )
    monkeypatch.setattr(
        hermes_api.link_repo,
        "submit_links",
        lambda *args: (
            calls.append(args) or {"accepted": 1, "duplicated": 0, "tasks": [task]}
        ),
    )
    response = hermes_api.submit_links(
        hermes_api.LinkRequest(urls=["https://example.invalid"], route="AUTO")
    )
    assert response["code"] == 0
    assert response["data"]["tasks"][0]["url_hash"] == "abc"
    assert calls[0][1:] == ("hermes", "AUTO", 5)


def test_history_creates_pending_run_only(monkeypatch) -> None:
    calls: list[tuple[object, ...]] = []
    monkeypatch.setattr(
        hermes_api.creator_ops,
        "create_run",
        lambda *args: calls.append(args) or {"run_id": 9, "state": "pending"},
    )
    response = hermes_api.create_history(
        7, hermes_api.CreatorRequest(input_cursor="c1")
    )
    assert response == {
        "code": 0,
        "data": {"run_id": 9, "state": "pending"},
        "message": "ok",
    }
    assert calls == [(7, "history_backfill", "c1")]


def test_scan_rejects_regression_as_conflict(monkeypatch) -> None:
    def fail(*args):
        raise ValueError("CURSOR_REGRESSION: cursor 只能单调前进")

    monkeypatch.setattr(hermes_api.creator_ops, "create_run", fail)
    from fastapi import HTTPException

    try:
        hermes_api.create_scan(7, hermes_api.CreatorRequest())
        raise AssertionError("expected HTTPException")
    except HTTPException as exc:
        assert exc.status_code == 409


def test_get_run_is_read_only(monkeypatch) -> None:
    monkeypatch.setattr(
        hermes_api.creator_ops,
        "get_run",
        lambda run_id: {"run_id": run_id, "state": "pending"},
    )
    assert hermes_api.get_run(4)["data"]["run_id"] == 4
