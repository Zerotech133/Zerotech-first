"""Focused retry idempotency tests without requiring a live PostgreSQL instance."""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from queue_link import api, repository
from queue_link.api import RetryTaskRequest


def _request(headers: dict[str, str] | None = None) -> Request:
    """Build a minimal Starlette request with the supplied headers."""
    encoded = [
        (key.lower().encode(), value.encode()) for key, value in (headers or {}).items()
    ]
    return Request({"type": "http", "method": "POST", "path": "/", "headers": encoded})


def _task(status: str = "failed") -> SimpleNamespace:
    """Return the minimum task shape required by retry_link."""
    return SimpleNamespace(status=status)


def test_completed_record_replays_original_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed key returns its stored payload and status without re-running retry."""
    payload = {"code": 0, "data": {"task_id": "7"}, "message": "任务已重新入队"}
    monkeypatch.setattr(api.repo, "get_by_id", lambda _: _task("pending"))
    monkeypatch.setattr(
        api.repo,
        "reserve_retry_idempotency",
        lambda *args: (
            {"status": "completed", "response": payload, "response_status": 200},
            False,
        ),
    )
    monkeypatch.setattr(
        api.repo, "resume_task", lambda _: pytest.fail("must not retry twice")
    )

    result = api.retry_link(
        7,
        RetryTaskRequest(reason="repeat"),
        _request({"Idempotency-Key": "same-key"}),
        "same-key",
    )

    assert result.status_code == 200
    assert json.loads(result.body) == payload


def test_active_processing_record_remains_409(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fresh processing reservation is not stolen by a concurrent request."""
    monkeypatch.setattr(api.repo, "get_by_id", lambda _: _task("failed"))
    monkeypatch.setattr(
        api.repo,
        "reserve_retry_idempotency",
        lambda *args: (
            {"status": "processing", "processing_at": "2099-01-01T00:00:00Z"},
            False,
        ),
    )

    with pytest.raises(HTTPException) as caught:
        api.retry_link(
            7,
            RetryTaskRequest(reason="concurrent"),
            _request({"Idempotency-Key": "same-key"}),
            "same-key",
        )

    assert caught.value.status_code == 409
    assert "正在处理" in str(caught.value.detail)


def test_stale_processing_record_is_reclaimed_with_cas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale processing row is atomically reclaimed and returned as owned."""
    executed: list[str] = []

    class Result:
        rowcount = 0

    class Connection:
        def execute(self, statement, params):
            sql = str(statement)
            executed.append(sql)
            result = Result()
            if "UPDATE osint_retry_idempotency" in sql:
                result.rowcount = 1
            return result

    @contextmanager
    def transaction():
        yield Connection()

    monkeypatch.setattr(repository.db, "transaction", transaction)
    monkeypatch.setattr(
        repository.db,
        "query",
        lambda conn, sql, params=None: [
            {
                "task_id": 7,
                "action": "retry",
                "idempotency_key": "same-key",
                "request_id": "new-request",
                "reason": "recover",
                "operator": "admin-ui",
                "response": None,
                "status": "processing",
                "response_status": None,
                "processing_at": None,
                "updated_at": None,
                "created_at": None,
            }
        ],
    )

    record, owned = repository.reserve_retry_idempotency(
        7, "retry", "same-key", "new-request", "recover", "admin-ui", timeout_seconds=60
    )

    assert owned is True
    assert record["status"] == "processing"
    assert any("processing_at < now()" in sql for sql in executed)
    assert any("timeout_seconds" in sql or "INTERVAL" in sql for sql in executed)
