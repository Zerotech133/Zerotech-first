"""Knowledge query router for the management API.

The router intentionally contains read-only endpoints only.  Mutating and
collector-facing endpoints remain in their existing modules.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from knowledge import repository
from knowledge import creator_ops
from knowledge import story_query
from knowledge.browser_fetcher import (
    BrowserFetcherProtocol,
    BrowserSessionHealth,
    BrowserSessionStatus,
)
from fastapi import HTTPException

router = APIRouter(tags=["knowledge"])

# The collector worker may register its already-created fetcher at startup.
# Until then, the management API must report unknown rather than infer login.
_browser_fetcher: BrowserFetcherProtocol | None = None


def register_browser_fetcher(fetcher: BrowserFetcherProtocol | None) -> None:
    """Register an existing browser fetcher for the read-only health endpoint."""
    global _browser_fetcher
    _browser_fetcher = fetcher


def current_browser_session_health() -> BrowserSessionHealth:
    """Return coarse browser session health without reading credentials."""
    if _browser_fetcher is None:
        return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)
    try:
        return _browser_fetcher.session_health()
    except Exception:  # noqa: BLE001
        return BrowserSessionHealth(status=BrowserSessionStatus.UNKNOWN)


@router.get("/api/v1/session/health")
def session_health() -> dict[str, Any]:
    """Expose only browser authorization state and check timestamp."""
    health = current_browser_session_health()
    checked_at = health.checked_at.astimezone().isoformat().replace("+00:00", "Z")
    return {
        "code": 0,
        "data": {"status": health.status.value, "checked_at": checked_at},
        "message": "ok",
    }


def _creator_http_error(exc: ValueError) -> HTTPException:
    """Map domain contract errors to stable HTTP status and error code."""
    message = str(exc)
    conflict_prefixes = (
        "CURSOR_REGRESSION",
        "CREATOR_STATE_TRANSITION_INVALID",
        "IDEMPOTENCY_CONFLICT",
    )
    status = (
        409
        if message.startswith(conflict_prefixes)
        else (404 if message.startswith("NOT_FOUND") else 400)
    )
    code = (
        message.split(":", 1)[0]
        if ":" in message
        else ("CONFLICT" if status == 409 else "INVALID_ARGUMENT")
    )
    return HTTPException(status_code=status, detail={"code": code, "message": message})


@router.post("/api/v1/creators/{target_id}/runs")
def create_creator_run(
    target_id: int, run_type: str, input_cursor: str | None = None
) -> dict[str, Any]:
    try:
        data = creator_ops.create_run(target_id, run_type, input_cursor)
    except ValueError as exc:
        raise _creator_http_error(exc) from exc
    return {"code": 0, "data": data, "message": "ok"}


@router.post("/api/v1/creator-runs/{run_id}/start")
def start_creator_run(run_id: int) -> dict[str, Any]:
    data = creator_ops.start_run(run_id)
    if data is None:
        raise HTTPException(status_code=409, detail="运行不存在或已启动")
    return {"code": 0, "data": data, "message": "ok"}


@router.post("/api/v1/creator-runs/{run_id}/finish")
def finish_creator_run(
    run_id: int,
    state: str,
    output_cursor: str | None = None,
    items_seen: int = 0,
    items_succeeded: int = 0,
    items_failed: int = 0,
    error_code: str | None = None,
    error_detail: str | None = None,
    summary_version: int | None = None,
) -> dict[str, Any]:
    try:
        data = creator_ops.finish_run(
            run_id,
            state=state,
            output_cursor=output_cursor,
            items_seen=items_seen,
            items_succeeded=items_succeeded,
            items_failed=items_failed,
            error_code=error_code,
            error_detail=error_detail,
            summary_version=summary_version,
        )
    except ValueError as exc:
        raise _creator_http_error(exc) from exc
    if data is None:
        raise HTTPException(
            status_code=409, detail="运行不存在、未处于 running 状态或已结束"
        )
    return {"code": 0, "data": data, "message": "ok"}


@router.post("/api/v1/creators/{target_id}/state")
def advance_creator_state(
    target_id: int,
    operation_state: str,
    cursor: str | None = None,
    error: str | None = None,
    summary_version: int | None = None,
    previous_state: str | None = None,
) -> dict[str, Any]:
    try:
        data = creator_ops.advance_target(
            target_id,
            operation_state,
            cursor=cursor,
            error=error,
            summary_version=summary_version,
            previous_state=previous_state,
        )
    except ValueError as exc:
        raise _creator_http_error(exc) from exc
    if data is None:
        raise HTTPException(status_code=404, detail="博主不存在")
    return {"code": 0, "data": data, "message": "ok"}


@router.get("/api/v1/creators/due-scans")
def due_creator_scans(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, Any]:
    return {
        "code": 0,
        "data": {
            "items": creator_ops.due_targets(limit),
            "as_of": __import__("datetime")
            .datetime.now(__import__("datetime").timezone.utc)
            .isoformat(),
        },
        "message": "ok",
    }


@router.get("/api/v1/creators/{target_id}")
def creator_detail(target_id: int) -> dict[str, Any]:
    data = creator_ops.get_creator(target_id)
    if data is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "博主不存在"}
        )
    return {"code": 0, "data": data, "message": "ok"}


@router.get("/api/v1/creators/{target_id}/runs")
def creator_runs(
    target_id: int,
    state: str | None = None,
    run_type: str | None = None,
    cursor: str | None = None,
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    if creator_ops.get_creator(target_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "博主不存在"}
        )
    try:
        data = creator_ops.list_runs(
            target_id, state=state, run_type=run_type, cursor=cursor, size=size
        )
    except ValueError as exc:
        raise _creator_http_error(exc) from exc
    return {"code": 0, "data": data, "message": "ok"}


@router.get("/api/v1/creators/{target_id}/summaries")
def creator_summaries(
    target_id: int,
    cursor: str | None = None,
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    if creator_ops.get_creator(target_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "博主不存在"}
        )
    try:
        data = creator_ops.list_summaries(target_id, cursor=cursor, size=size)
    except ValueError as exc:
        raise _creator_http_error(exc) from exc
    return {"code": 0, "data": data, "message": "ok"}


@router.get("/api/v1/creators/{target_id}/timeline")
def creator_timeline(
    target_id: int,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    cursor: str | None = None,
    size: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    if creator_ops.get_creator(target_id) is None:
        raise HTTPException(
            status_code=404, detail={"code": "NOT_FOUND", "message": "博主不存在"}
        )
    try:
        data = creator_ops.list_creator_timeline(
            target_id, from_=from_, to=to, cursor=cursor, size=size
        )
    except ValueError as exc:
        raise _creator_http_error(exc) from exc
    return {"code": 0, "data": data, "message": "ok"}


@router.get("/api/v1/knowledge/units/{unit_key}")
def knowledge_unit_detail(
    unit_key: str, version: int | None = Query(default=None, ge=1)
) -> dict[str, Any]:
    """Return versioned unit details and sanitized evidence."""
    result = repository.get_knowledge_unit_detail(unit_key, version)
    if result is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="知识单元不存在")
    return {"code": 0, "data": result, "message": "ok"}


@router.get("/api/v1/evidence/{evidence_key}")
def evidence_detail(evidence_key: str) -> dict[str, Any]:
    """Return one joined EvidenceChain item."""
    result = repository.get_evidence(evidence_key)
    if result is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="证据不存在")
    return {"code": 0, "data": result, "message": "ok"}


@router.get("/api/v1/strategies/{strategy_key}/evidence-chain")
def strategy_evidence_chain(
    strategy_key: str, version: int | None = Query(default=None, ge=1)
) -> dict[str, Any]:
    """Return strategy evidence chain with version/time warnings."""
    result = repository.get_strategy_evidence_chain(strategy_key, version)
    if result is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="策略证据不存在")
    return {"code": 0, "data": result, "message": "ok"}


@router.get("/api/v1/resources/publication-stats")
def resource_publication_stats(blogger_key: str | None = None) -> dict[str, Any]:
    """Return publication counts and dates, never media paths or bodies."""
    return {
        "code": 0,
        "data": repository.resource_publication_stats(blogger_key),
        "message": "ok",
    }


@router.get("/api/v1/knowledge/units")
def list_knowledge_units(
    q: str | None = None,
    topic: str | None = None,
    entity: str | None = None,
    target_id: str | None = None,
    status: str | None = None,
    unit_type: str | None = None,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = None,
    cursor: str | None = None,
    size: int = 20,
) -> dict[str, Any]:
    """Return a bounded, sanitized knowledge-unit page from PostgreSQL."""
    return {
        "code": 0,
        "data": repository.list_units(
            q, topic, entity, target_id, status, unit_type, from_, to, cursor, size
        ),
        "message": "ok",
    }


# ------------------------------------------------------------------
# 事件层（story）只读端点 —— F2「采了没人吃」的消费出口。
# 下游 kg_brief / event_graph 直接读这里，不再自行聚合散点事实。
# ------------------------------------------------------------------


@router.get("/api/v1/stories")
def list_stories(
    hours: int = Query(default=168, ge=1, le=8760),
    min_sources: int | None = Query(default=None, ge=1),
    stage: str | None = None,
    review_status: str | None = None,
    size: int = Query(default=20, ge=1, le=200),
) -> dict[str, Any]:
    """Return hot events ranked by heat (independent-source weighted)."""
    return {
        "code": 0,
        "data": story_query.list_stories(
            hours=hours,
            min_sources=min_sources,
            stage=stage,
            review_status=review_status,
            limit=size,
        ),
        "message": "ok",
    }


@router.get("/api/v1/stories/{story_id}")
def story_detail(story_id: int) -> dict[str, Any]:
    """Return one event with its sanitized member facts."""
    result = story_query.get_story_detail(story_id)
    if result is None:
        raise HTTPException(status_code=404, detail="事件不存在")
    return {"code": 0, "data": result, "message": "ok"}


@router.get("/api/v1/brief/daily")
def daily_brief(
    hours: int = Query(default=24, ge=1, le=720),
    min_sources: int = Query(default=2, ge=1),
    size: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    """Return the daily brief: only events covered by >= min_sources outlets."""
    return {
        "code": 0,
        "data": story_query.daily_brief(
            hours=hours, min_sources=min_sources, limit=size
        ),
        "message": "ok",
    }
