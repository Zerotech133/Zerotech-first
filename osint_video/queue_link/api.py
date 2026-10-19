"""链接队列 FastAPI 服务（架构 §2.1 对外 API，独立 8300 端口）。

接口（响应统一 ``{code, data, message}``，code=0 成功）：
- ``POST /api/v1/links``                          批量投递（url_hash 幂等去重）
- ``GET  /api/v1/links?status=&page=&size=``      队列分页查询
- ``POST /api/v1/credentials/yuanbao_cookie``     Cookie 热更新（挂起任务自动恢复）
- ``GET  /api/v1/stats``                          各状态计数（日报数据源）
- ``GET  /healthz``                               存活探测（含 PG 健康）

降级：PG 不可用时业务接口返回 503（服务本身不 crash）；飞书通知失败仅记日志。
"""

from __future__ import annotations

import logging
from typing import Any

try:
    from cryptography.fernet import Fernet
except ImportError:  # optional at import time; cookie update reports configuration error
    Fernet = None  # type: ignore[assignment,misc]
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from common.db import DatabaseUnavailableError
from common.feishu import FeishuNotifier
from common.logging_setup import audit, setup_logging
from config.settings import get_settings
from queue_link import repository as repo
from knowledge import repository as knowledge_repo
from queue_link.models import (
    ALL_STATUSES,
    LinkTask,
    isoformat_z,
)

setup_logging()
logger = logging.getLogger(__name__)
settings = get_settings()
notifier = FeishuNotifier()

app = FastAPI(
    title="osint_video 链接队列",
    version="0.1.0",
    description="视频号 OSINT 管线：链接投递 / 状态机 / 凭证热更新（架构 §2.1）",
)

# 知识与博主运营路由保持独立模块，统一挂载到管理 API。
from knowledge.api import router as knowledge_router  # noqa: E402
from integration.hermes_api import router as hermes_router  # noqa: E402
from queue_link.import_api import router as import_router, ingest_router  # noqa: E402
app.include_router(knowledge_router)
app.include_router(hermes_router)
app.include_router(import_router)
app.include_router(ingest_router)


# ---------------------------------------------------------------------------
# 统一响应封装
# ---------------------------------------------------------------------------
def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应 ``{code:0, data, message}``。"""
    return {"code": 0, "data": data, "message": message}


@app.exception_handler(DatabaseUnavailableError)
async def _db_unavailable_handler(_: Request, exc: DatabaseUnavailableError) -> JSONResponse:
    """PG 不可用 → 统一 503 降级（不 crash 服务）。"""
    logger.error("PG 不可用，API 降级 503：%s", exc)
    return JSONResponse(
        status_code=503,
        content={"code": 503, "data": None, "message": f"数据库不可用：{exc}"},
    )


# ---------------------------------------------------------------------------
# 请求/响应模型
# ---------------------------------------------------------------------------
class SubmitLinksRequest(BaseModel):
    """POST /api/v1/links 请求体。"""

    urls: list[str] = Field(min_length=1, max_length=200, description="sph 分享链接列表")
    created_by: str = Field(default="feishu", max_length=64, description="投递来源标识")
    route: str = Field(default="D", pattern="^(D|B|AUTO)$", description="采集路由")
    priority: int = Field(default=5, ge=1, le=9, description="数字小先采")


class CookieUpdateRequest(BaseModel):
    """POST /api/v1/credentials/yuanbao_cookie 请求体。"""

    cookie: str = Field(min_length=8, description="浏览器 F12 复制的完整 Cookie 串")
    updated_by: str = Field(default="unknown", max_length=64, description="操作人")


class RetryTaskRequest(BaseModel):
    """人工重试请求；reason 用于审计并拒绝空白理由。"""

    reason: str = Field(min_length=1, max_length=500)


def task_to_dict(task: LinkTask) -> dict[str, Any]:
    """Map an internal LinkTask to the frozen, allow-listed public DTO."""
    return {
        "task_id": str(task.id),
        "url_hash": task.url_hash,
        "state": task.status,
        "route": task.route,
        "priority": task.priority,
        "attempt": task.retry_count,
        "next_retry_at": isoformat_z(task.next_retry_at),
        "error_code": task.error_code,
        "video_id": task.video_id,
        "created_at": isoformat_z(task.created_at),
        "updated_at": isoformat_z(task.updated_at),
    }


# ---------------------------------------------------------------------------
# 业务接口
# ---------------------------------------------------------------------------
@app.post("/api/v1/links")
def submit_links(req: SubmitLinksRequest) -> dict[str, Any]:
    """批量投递 sph 链接：url_hash 去重幂等，返回 {accepted, duplicated}。"""
    result = repo.submit_links(
        urls=req.urls, created_by=req.created_by, route=req.route, priority=req.priority
    )
    payload = {
        "accepted": result["accepted"],
        "duplicated": result["duplicated"],
        "tasks": [task_to_dict(t) for t in result["tasks"]],
    }
    audit(
        "submit_link",
        operator=req.created_by,
        requested=len(req.urls),
        accepted=result["accepted"],
        duplicated=result["duplicated"],
        route=req.route,
    )
    return ok(payload, "投递完成（url_hash 去重）")


@app.get("/api/v1/links/{task_id}")
def get_link(task_id: int) -> dict[str, Any]:
    task = repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    return ok(task_to_dict(task))


@app.get("/api/v1/links")
def list_links(status: str | None = None, route: str | None = None, cursor: str | None = None, page: int = 1, size: int = 20) -> dict[str, Any]:
    """队列分页查询：``?status=pending&page=1&size=20``。"""
    if status is not None and status not in ALL_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"非法 status：{status}；合法值 {sorted(ALL_STATUSES)}",
        )
    if route is not None and route not in {"D", "B", "AUTO", "A"}:
        raise HTTPException(status_code=400, detail="非法 route")
    if cursor:
        try:
            page = max(1, int(cursor))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="非法 cursor") from exc
    result = repo.list_tasks(status=status, page=page, size=size, route=route)
    items = [task_to_dict(t) for t in result["items"]]
    next_cursor = str(page + 1) if len(result["items"]) == result["size"] else None
    return ok({"items": items, "next_cursor": next_cursor, "as_of": knowledge_repo._as_of()})


@app.post("/api/v1/links/{task_id}/retry", response_model=None)
def retry_link(
    task_id: int,
    req: RetryTaskRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict[str, Any] | JSONResponse:
    """Manually retry failed/dead tasks with durable idempotency replay.

    The first result (success or gate rejection) is persisted in PostgreSQL and
    replayed verbatim for the same ``task_id/action/Idempotency-Key`` tuple.
    Only the allow-listed public response is persisted; no cookie, token, or
    filesystem path is accepted by this endpoint.
    """
    reason = req.reason.strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason 不能为空")
    key = (idempotency_key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
    if len(key) > 256:
        raise HTTPException(status_code=400, detail="Idempotency-Key 过长")
    operator = request.headers.get("X-Operator-ID", "").strip()[:128] or "admin-ui"
    request_id = request.headers.get("X-Request-ID", "").strip()[:256] or None
    task = repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    record, is_new = repo.reserve_retry_idempotency(
        task_id, "retry", key, request_id, reason, operator
    )
    if not is_new:
        # A completed record is an exact replay, including its original status.
        if record.get("status") == "completed" and record.get("response") is not None:
            return JSONResponse(
                status_code=int(record.get("response_status") or 200),
                content=record["response"],
            )
        # Concurrent first request has not committed its result yet.
        raise HTTPException(status_code=409, detail="相同幂等键的请求正在处理")

    def reject(status_code: int, message: str) -> None:
        payload = {"detail": message}
        repo.complete_retry_idempotency(task_id, "retry", key, payload, status_code)
        raise HTTPException(status_code=status_code, detail=message)

    if task.status not in {"failed", "dead"}:
        reject(409, f"当前状态 {task.status} 不允许重试")
    result = repo.resume_task(task_id)
    if result is None:
        reject(409, "任务未能重新入队")
    response = ok(task_to_dict(result), "任务已重新入队")
    repo.complete_retry_idempotency(task_id, "retry", key, response, 200)
    audit(
        "retry_link",
        action="retry",
        object_type="link_task",
        task_id=task_id,
        reason=reason,
        idempotency_key=key,
        operator=operator,
        request_id=request_id,
    )
    return response


@app.post("/api/v1/credentials/yuanbao_cookie")
def update_yuanbao_cookie(req: CookieUpdateRequest) -> dict[str, Any]:
    """元宝 Cookie 热更新：Fernet 加密入库 + waiting_cookie 任务自动恢复 + 飞书通知。"""
    if Fernet is None or not settings.fernet_key:
        raise HTTPException(
            status_code=400,
            detail="服务端未配置 FERNET_KEY，无法加密存储 Cookie（见 .env.example 生成说明）",
        )
    try:
        cipher = Fernet(settings.fernet_key.encode("utf-8"))
        value_enc = cipher.encrypt(req.cookie.encode("utf-8")).decode("ascii")
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail=f"FERNET_KEY 非法：{exc}") from exc

    repo.save_credential("yuanbao_cookie", value_enc, req.updated_by)
    resumed = repo.resume_waiting_cookie()
    notifier.alert_cookie_updated(req.updated_by, resumed)  # 失败仅记日志，不阻塞
    audit("update_cookie", operator=req.updated_by, name="yuanbao_cookie", resumed=resumed)
    logger.info("元宝 Cookie 已更新（操作人=%s，恢复挂起任务 %d 条）", req.updated_by, resumed)
    return ok(
        {"name": "yuanbao_cookie", "status": "active", "resumed_waiting": resumed},
        "Cookie 已更新，挂起任务已自动恢复",
    )


@app.get("/api/v1/dashboard/summary")
def dashboard_summary(window: str = "24h") -> dict[str, Any]:
    return ok(knowledge_repo.dashboard_summary(window))


@app.get("/api/v1/changes")
def changes(cursor: str | None = None, scope: str = "dashboard") -> dict[str, Any]:
    if scope not in {"dashboard", "links", "knowledge", "jobs"}:
        raise HTTPException(status_code=400, detail="非法 scope")
    return ok(knowledge_repo.changes(cursor, scope))


@app.get("/api/v1/creators")
def creators(nickname: str | None = None, topic: str | None = None, entity: str | None = None, finder_id: str | None = None, enabled: bool | None = None, monitor_level: int | None = None, cursor: str | None = None, size: int = 20) -> dict[str, Any]:
    try:
        data = knowledge_repo.list_creators(nickname, topic, entity, finder_id, enabled, monitor_level, cursor, size)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ok(data)


@app.get("/api/v1/timeline")
def timeline(target_id: str | None = None, topic: str | None = None, entity: str | None = None, event_type: str | None = None, from_: str | None = Query(default=None, alias="from"), to: str | None = None, cursor: str | None = None, size: int = 20) -> dict[str, Any]:
    return ok(knowledge_repo.list_timeline(target_id, topic, entity, event_type, from_, to, cursor, size))


@app.get("/api/v1/jobs")
def jobs(stage: str | None = None, state: str | None = None, cursor: str | None = None, size: int = 20) -> dict[str, Any]:
    return ok(knowledge_repo.list_jobs(stage, state, cursor, size))


@app.get("/api/v1/stats")
def stats() -> dict[str, Any]:
    """各状态计数（飞书日报数据源）。"""
    result = repo.get_stats()
    dead_count = result.get("dead", 0)
    if dead_count >= 10:  # 死信超阈值即时告警（阈值 PoC 期取 10）
        samples = [f"#{t.id} {t.error_code} {t.error_msg or ''}" for t in repo.list_dead(5)]
        notifier.alert_dead_letter(dead_count, samples)
    return ok(result)


@app.get("/api/v1/healthz")
def management_healthz() -> dict[str, Any]:
    """Management health endpoint with bounded optional dependency probes."""
    from common.db import health as pg_health
    import urllib.request
    import urllib.error
    pg_ok = pg_health()
    qdrant_state = "unknown"
    try:
        request = urllib.request.Request(settings.qdrant_url.rstrip("/") + "/healthz", method="GET")
        with urllib.request.urlopen(request, timeout=0.8) as response:
            qdrant_state = "healthy" if 200 <= response.status < 300 else "degraded"
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        qdrant_state = "unavailable"
    return ok({"api": "healthy", "pg": "healthy" if pg_ok else "unavailable", "qdrant": qdrant_state, "worker": "unknown", "as_of": knowledge_repo._as_of()})


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    """存活探测：服务存活 + PG 连接健康 + 关键配置就绪情况。"""
    from common.db import health as pg_health  # 局部导入避免模块加载期触发连接

    return ok(
        {
            "service": "queue_link",
            "pg_ok": pg_health(),
            "fernet_ready": bool(settings.fernet_key),
            "feishu_enabled": notifier.enabled,
            # I6 收口（设计稿 A-12）：hub 计量通道就绪情况，与 webhook 配置是两个事实
            "feishu_hub_ready": notifier.hub_ready,
        }
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8300)
