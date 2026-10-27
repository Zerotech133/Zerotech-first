"""FastAPI integration endpoints consumed by the Hermes thin proxy.

Endpoints only enqueue links or create pending creator runs. Workers remain the
sole component allowed to execute provider/collection work.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from knowledge import creator_ops
from queue_link import repository as link_repo
from queue_link.models import Route, isoformat_z

router = APIRouter(prefix="/api/v1/integrations/hermes", tags=["hermes-integration"])


class LinkRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=200)
    created_by: str = Field(default="hermes", min_length=1, max_length=64)
    route: str = Field(default=Route.AUTO, pattern="^(D|B|AUTO)$")
    priority: int = Field(default=5, ge=1, le=9)


class CreatorRequest(BaseModel):
    input_cursor: str | None = Field(default=None, max_length=512)


def _ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "data": data, "message": "ok"}


def _task_data(task: Any) -> dict[str, Any]:
    return {
        "task_id": str(getattr(task, "id", "")),
        "url_hash": getattr(task, "url_hash", None),
        "state": getattr(task, "status", None),
        "route": getattr(task, "route", None),
        "priority": getattr(task, "priority", None),
        "created_at": isoformat_z(getattr(task, "created_at", None)),
    }


@router.post("/links")
def submit_links(request: LinkRequest) -> dict[str, Any]:
    """Submit links idempotently; no media access occurs here."""
    result = link_repo.submit_links(
        request.urls, request.created_by, request.route, request.priority
    )
    payload = dict(result)
    payload["tasks"] = [_task_data(task) for task in result.get("tasks", [])]
    return _ok(payload)


@router.post("/creators/{target_id}/history")
def create_history(target_id: int, request: CreatorRequest) -> dict[str, Any]:
    return _create_run(target_id, "history_backfill", request.input_cursor)


@router.post("/creators/{target_id}/scan")
def create_scan(target_id: int, request: CreatorRequest) -> dict[str, Any]:
    return _create_run(target_id, "daily_scan", request.input_cursor)


def _create_run(
    target_id: int, run_type: str, input_cursor: str | None
) -> dict[str, Any]:
    try:
        result = creator_ops.create_run(target_id, run_type, input_cursor)
    except ValueError as exc:
        message = str(exc)
        status = (
            404
            if message.startswith("NOT_FOUND")
            else (409 if message.startswith("CURSOR_REGRESSION") else 400)
        )
        raise HTTPException(
            status_code=status,
            detail={"code": message.split(":", 1)[0], "message": message},
        ) from exc
    return _ok(result)


@router.get("/runs/{run_id}")
def get_run(run_id: int) -> dict[str, Any]:
    result = creator_ops.get_run(run_id)
    if result is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "运行不存在"}
        )
    return _ok(result)


@router.get("/creators/{target_id}/summary")
def get_summary(
    target_id: int,
    cursor: str | None = None,
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    _require_creator(target_id)
    return _ok(creator_ops.list_summaries(target_id, cursor=cursor, size=size))


@router.get("/creators/{target_id}/timeline")
def get_timeline(
    target_id: int,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    cursor: str | None = None,
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    _require_creator(target_id)
    return _ok(
        creator_ops.list_creator_timeline(
            target_id, from_=from_, to=to, cursor=cursor, size=size
        )
    )


def _require_creator(target_id: int) -> None:
    if creator_ops.get_creator(target_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "博主不存在"}
        )


__all__ = ["router"]
