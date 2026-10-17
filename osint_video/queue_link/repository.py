"""链接队列仓储层：入队去重 / 认领 / 状态流转 / 指数退避重试 / 死信（架构 §2.1）。

所有方法直接操作 PG（经 common.db），异常统一为
:class:`common.db.DatabaseUnavailableError`，由上层（API/worker）决定降级。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from common import db
from queue_link.models import (
    ALLOWED_TRANSITIONS,
    DEFAULT_MAX_RETRY,
    RETRY_BACKOFF_SECONDS,
    ErrorCode,
    LinkStatus,
    LinkTask,
    Route,
    compute_url_hash,
    isoformat_z,
    row_to_task,
    utc_now,
)
from sqlalchemy import text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 入队 / 查询
# ---------------------------------------------------------------------------
_SQL_INSERT_TASK = """
INSERT INTO osint_link_task
    (sph_url, url_hash, status, route, priority, created_by, created_at, updated_at)
VALUES
    (:sph_url, :url_hash, 'pending', :route, :priority, :created_by, now(), now())
ON CONFLICT (url_hash) DO NOTHING
"""

_SQL_SELECT_BY_HASH = "SELECT * FROM osint_link_task WHERE url_hash = :url_hash LIMIT 1"

_SQL_PAGE_BASE = "SELECT * FROM osint_link_task"
_SQL_COUNT_BASE = "SELECT count(*) AS cnt FROM osint_link_task"


def submit_links(
    urls: list[str],
    created_by: str = "feishu",
    route: str = Route.D,
    priority: int = 5,
) -> dict[str, Any]:
    """批量投递链接：url_hash 去重，幂等。

    Returns:
        ``{"accepted": 新建数, "duplicated": 重复数, "tasks": [LinkTask...]}``
        （tasks 含新建与重复命中的既有任务，供调用方回显 video 维度信息）。
    """
    accepted, duplicated = 0, 0
    tasks: list[LinkTask] = []
    for url in urls:
        url = url.strip()
        if not url:
            continue
        url_hash = compute_url_hash(url)
        with db.transaction() as conn:
            # 注意：Connection.execute() 只接受 ClauseElement，裸 SQL 必须用 text() 包装
            result = conn.execute(
                text(_SQL_INSERT_TASK),
                {
                    "sph_url": url,
                    "url_hash": url_hash,
                    "route": route if route in (Route.D, Route.B, Route.AUTO) else Route.D,
                    "priority": priority,
                    "created_by": created_by,
                },
            )
            inserted = int(result.rowcount) > 0
            rows = db.query(conn, _SQL_SELECT_BY_HASH, {"url_hash": url_hash})
        if inserted:
            accepted += 1
        else:
            duplicated += 1
        if rows:
            tasks.append(row_to_task(rows[0]))
    return {"accepted": accepted, "duplicated": duplicated, "tasks": tasks}


def get_by_hash(url_hash: str) -> LinkTask | None:
    """按 url_hash 查任务。"""
    rows = db.fetch_all(_SQL_SELECT_BY_HASH, {"url_hash": url_hash})
    return row_to_task(rows[0]) if rows else None


def get_by_id(task_id: int) -> LinkTask | None:
    """按主键查任务。"""
    rows = db.fetch_all("SELECT * FROM osint_link_task WHERE id = :id", {"id": task_id})
    return row_to_task(rows[0]) if rows else None


def list_tasks(
    status: str | None = None,
    page: int = 1,
    size: int = 20,
    route: str | None = None,
) -> dict[str, Any]:
    """分页查询队列（按优先级、创建时间倒序）。

    Returns:
        ``{"items": [LinkTask...], "total": 匹配总数, "page": 页码, "size": 页大小}``
    """
    page = max(1, page)
    size = min(max(1, size), 100)
    params: dict[str, Any] = {"limit": size, "offset": (page - 1) * size}
    where = ""
    clauses: list[str] = []
    if status:
        clauses.append("status = :status")
        params["status"] = status
    if route:
        clauses.append("route = :route")
        params["route"] = route
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    total_rows = db.fetch_all(f"{_SQL_COUNT_BASE}{where}", params)
    total = int(total_rows[0]["cnt"]) if total_rows else 0
    rows = db.fetch_all(
        f"{_SQL_PAGE_BASE}{where} ORDER BY priority ASC, created_at DESC LIMIT :limit OFFSET :offset",
        params,
    )
    return {
        "items": [row_to_task(r) for r in rows],
        "total": total,
        "page": page,
        "size": size,
    }


# ---------------------------------------------------------------------------
# 认领与流转
# ---------------------------------------------------------------------------
_SQL_CLAIM = """
SELECT * FROM osint_link_task
WHERE status = 'pending'
  AND (next_retry_at IS NULL OR next_retry_at <= now())
ORDER BY priority ASC, next_retry_at NULLS FIRST, id ASC
LIMIT 1
"""

# 注：PoC 单 worker 串行消费，未加 FOR UPDATE SKIP LOCKED；
# T02 多 worker 上线时替换为：
#   ... LIMIT 1 FOR UPDATE SKIP LOCKED（需在事务内执行）
# 代码注释标记，避免并发重复认领。


def claim_next(claiming_status: str = LinkStatus.PARSED) -> LinkTask | None:
    """认领下一个可处理任务：pending → claiming_status 原子流转。

    Args:
        claiming_status: 认领后的状态（worker 语义不同：解析 worker→parsed）。

    Returns:
        认领成功的任务；队列空 / 无到期任务返回 None。
    """
    with db.transaction() as conn:
        rows = db.query(conn, _SQL_CLAIM)
        if not rows:
            return None
        task = row_to_task(rows[0])
        # pending 的合法后继包含 parsed/downloaded/decrypted/extracted 视 worker 而定，
        # 认领即代表对应环节开始处理，这里放宽为直接写入目标态（保持与状态机兼容）
        updated = conn.execute(
            text(
                """
                UPDATE osint_link_task
                SET status = :status, updated_at = now()
                WHERE id = :id AND status = 'pending'
                """
            ),
            {"status": claiming_status, "id": task.id},
        )
        if int(updated.rowcount) != 1:  # 并发下被抢
            return None
    task.status = claiming_status
    return task


def _backoff_deadline(retry_count: int) -> datetime:
    """第 retry_count 次重试的 next_retry_at（5min/30min/2h 指数退避，封顶 2h）。"""
    idx = min(max(retry_count, 1), len(RETRY_BACKOFF_SECONDS)) - 1
    return utc_now() + timedelta(seconds=RETRY_BACKOFF_SECONDS[idx])


def mark_failed(
    task_id: int,
    error_code: str,
    error_msg: str,
    from_status: str | None = None,
) -> LinkTask | None:
    """任务失败：COOKIE_EXPIRED 挂起不计次；其余按指数退避计次，超限转死信。

    Returns:
        更新后的任务；任务不存在 / from_status 不匹配返回 None。
    """
    task = get_by_id(task_id)
    if task is None:
        return None
    if from_status is not None and task.status != from_status:
        logger.warning("mark_failed 状态不匹配：task=%s 当前=%s 期望=%s", task_id, task.status, from_status)
        return None

    cookie_expired = error_code == ErrorCode.COOKIE_EXPIRED
    new_retry = task.retry_count if cookie_expired else task.retry_count + 1
    exceeded = (not cookie_expired) and new_retry > task.max_retry

    if exceeded:
        # 重试超限 → 死信；AUTO 路由改为转 B 路线再战一次（架构状态机）
        if task.route == Route.AUTO:
            db.execute(
                """
                UPDATE osint_link_task
                SET status = 'pending', route = 'B', retry_count = 0,
                    next_retry_at = NULL, error_code = :ec, error_msg = :em, updated_at = now()
                WHERE id = :id
                """,
                {"ec": error_code, "em": f"[转B路线] {error_msg}", "id": task_id},
            )
            logger.info("任务 %s AUTO 路由重试超限，已转 B 路线重新入队", task_id)
        else:
            db.execute(
                """
                UPDATE osint_link_task
                SET status = 'dead', retry_count = :rc,
                    error_code = :ec, error_msg = :em, next_retry_at = NULL, updated_at = now()
                WHERE id = :id
                """,
                {"rc": new_retry, "ec": error_code, "em": error_msg, "id": task_id},
            )
            logger.warning("任务 %s 重试超限进入死信：%s %s", task_id, error_code, error_msg)
    elif cookie_expired:
        # Cookie 过期 → waiting_cookie 挂起，不计次（人工更新后自动恢复）
        db.execute(
            """
            UPDATE osint_link_task
            SET status = 'waiting_cookie', error_code = :ec, error_msg = :em,
                next_retry_at = NULL, updated_at = now()
            WHERE id = :id
            """,
            {"ec": error_code, "em": error_msg, "id": task_id},
        )
        logger.warning("任务 %s Cookie 过期挂起 waiting_cookie", task_id)
    else:
        # 常规失败 → failed + 指数退避
        db.execute(
            """
            UPDATE osint_link_task
            SET status = 'failed', retry_count = :rc, error_code = :ec, error_msg = :em,
                next_retry_at = :nr, updated_at = now()
            WHERE id = :id
            """,
            {
                "rc": new_retry, "ec": error_code, "em": error_msg,
                "nr": _backoff_deadline(new_retry), "id": task_id,
            },
        )
        logger.info("任务 %s 失败（%s），第 %d 次重试已排期", task_id, error_code, new_retry)
    return get_by_id(task_id)


def transition(
    task_id: int,
    new_status: str,
    *,
    video_id: str | None = None,
    parse_result: dict[str, Any] | None = None,
    file_md5: str | None = None,
    raw_path: str | None = None,
) -> LinkTask | None:
    """状态机流转（带合法性校验，非法流转拒绝并返回 None）。

    典型调用：解析成功 ``transition(id, 'parsed', video_id=..., parse_result=...)``；
    下载完成 ``transition(id, 'downloaded', file_md5=..., raw_path=...)``。
    """
    task = get_by_id(task_id)
    if task is None:
        return None
    if new_status not in ALLOWED_TRANSITIONS.get(task.status, frozenset()):
        logger.warning(
            "非法状态流转被拒绝：task=%s %s → %s", task_id, task.status, new_status
        )
        return None

    # ``done`` is a durable completion claim, not merely a state-machine edge.
    # Check the knowledge lifecycle before mutating the queue row so a task can
    # never be marked complete while its authoritative PG facts are incomplete.
    if new_status == LinkStatus.DONE:
        from store.repository import can_mark_link_done

        if not can_mark_link_done(task_id):
            logger.warning(
                "完成门禁拒绝状态流转：task=%s 缺少 Resource/Unit/Evidence/IndexTask",
                task_id,
            )
            return None

    sets = ["status = :status", "updated_at = now()"]
    params: dict[str, Any] = {"status": new_status, "id": task_id}
    if video_id is not None:
        sets.append("video_id = :video_id")
        params["video_id"] = video_id
    if parse_result is not None:
        sets.append("parse_result = CAST(:parse_result AS JSONB)")
        params["parse_result"] = json.dumps(parse_result, ensure_ascii=False)
    if file_md5 is not None:
        sets.append("file_md5 = :file_md5")
        params["file_md5"] = file_md5
    if raw_path is not None:
        sets.append("raw_path = :raw_path")
        params["raw_path"] = raw_path
    db.execute(f"UPDATE osint_link_task SET {', '.join(sets)} WHERE id = :id", params)
    return get_by_id(task_id)


def promote_due_failures(limit: int = 100) -> int:
    """把退避到期（next_retry_at ≤ now）的 failed 任务批量恢复为 pending。

    由调度器周期调用；返回恢复条数。
    注意：PG 不支持 UPDATE ... LIMIT，用子查询限定批量大小。
    """
    return db.execute(
        """
        UPDATE osint_link_task
        SET status = 'pending', next_retry_at = NULL, updated_at = now()
        WHERE id IN (
            SELECT id FROM osint_link_task
            WHERE status = 'failed'
              AND next_retry_at IS NOT NULL
              AND next_retry_at <= now()
            ORDER BY next_retry_at ASC
            LIMIT :limit
        )
        """,
        {"limit": limit},
    )


_RETRY_PROCESSING_TIMEOUT_SECONDS = 300


def _retry_record_query() -> str:
    """Return the common idempotency record projection."""
    return """
        SELECT task_id, action, idempotency_key, request_id, reason, operator,
               response, status, response_status, processing_at, updated_at, created_at
        FROM osint_retry_idempotency
    """


def _format_retry_record(row: dict[str, Any]) -> dict[str, Any]:
    """Convert timestamp fields in an idempotency record to API-safe strings."""
    record = dict(row)
    for field in ("processing_at", "updated_at", "created_at"):
        record[field] = isoformat_z(record.get(field))
    return record


def get_retry_idempotency(task_id: int, action: str, idempotency_key: str) -> dict[str, Any] | None:
    """读取已持久化的重试幂等记录。"""
    rows = db.fetch_all(
        _retry_record_query() + """
        WHERE task_id = :task_id AND action = :action AND idempotency_key = :key
        LIMIT 1
        """,
        {"task_id": task_id, "action": action, "key": idempotency_key},
    )
    return _format_retry_record(rows[0]) if rows else None


def reserve_retry_idempotency(
    task_id: int,
    action: str,
    idempotency_key: str,
    request_id: str | None,
    reason: str,
    operator: str,
    timeout_seconds: int = _RETRY_PROCESSING_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], bool]:
    """Atomically reserve a key, reclaiming only stale processing records.

    Returns the record and ``True`` when this caller owns a fresh reservation,
    including a successful stale-record CAS takeover. Active processing and
    completed records return ``False`` and remain immutable.
    """
    timeout_seconds = max(1, int(timeout_seconds))
    with db.transaction() as conn:
        inserted = conn.execute(
            text(
                """
                INSERT INTO osint_retry_idempotency
                    (task_id, action, idempotency_key, request_id, reason, operator,
                     status, processing_at, updated_at)
                VALUES (:task_id, :action, :key, :request_id, :reason, :operator,
                        'processing', now(), now())
                ON CONFLICT (task_id, action, idempotency_key) DO NOTHING
                """
            ),
            {
                "task_id": task_id,
                "action": action,
                "key": idempotency_key,
                "request_id": request_id,
                "reason": reason,
                "operator": operator,
            },
        ).rowcount
        reclaimed = 0
        if int(inserted) == 0:
            reclaimed = conn.execute(
                text(
                    """
                    UPDATE osint_retry_idempotency
                    SET request_id = :request_id, reason = :reason, operator = :operator,
                        processing_at = now(), updated_at = now(), response = NULL,
                        response_status = NULL, status = 'processing'
                    WHERE task_id = :task_id AND action = :action
                      AND idempotency_key = :key AND status = 'processing'
                      AND processing_at < now() - (:timeout_seconds * INTERVAL '1 second')
                    """
                ),
                {
                    "task_id": task_id,
                    "action": action,
                    "key": idempotency_key,
                    "request_id": request_id,
                    "reason": reason,
                    "operator": operator,
                    "timeout_seconds": timeout_seconds,
                },
            ).rowcount
        rows = db.query(
            conn,
            _retry_record_query() + """
            WHERE task_id = :task_id AND action = :action AND idempotency_key = :key
            LIMIT 1
            """,
            {"task_id": task_id, "action": action, "key": idempotency_key},
        )
    if not rows:
        raise RuntimeError("无法读取刚创建的重试幂等记录")
    return _format_retry_record(rows[0]), int(inserted) == 1 or int(reclaimed) == 1


def complete_retry_idempotency(
    task_id: int,
    action: str,
    idempotency_key: str,
    response: dict[str, Any],
    response_status: int,
) -> None:
    """Persist the first retry response; response contains only public DTO fields."""
    db.execute(
        """
        UPDATE osint_retry_idempotency
        SET response = CAST(:response AS JSONB), response_status = :response_status,
            status = 'completed', updated_at = now()
        WHERE task_id = :task_id AND action = :action AND idempotency_key = :key
        """,
        {
            "task_id": task_id,
            "action": action,
            "key": idempotency_key,
            "response": json.dumps(response, ensure_ascii=False),
            "response_status": response_status,
        },
    )


def resume_task(task_id: int) -> LinkTask | None:
    """人工重试失败/死信任务：清理退避错误并重新置为 pending。"""
    db.execute(
        """
        UPDATE osint_link_task
        SET status = 'pending', error_code = NULL, error_msg = NULL,
            next_retry_at = NULL, updated_at = now()
        WHERE id = :id AND status IN ('failed', 'dead')
        """,
        {"id": task_id},
    )
    return get_by_id(task_id)


def resume_waiting_cookie() -> int:
    """Cookie 更新后：全部 waiting_cookie 任务批量恢复 pending（无需重启）。"""
    return db.execute(
        """
        UPDATE osint_link_task
        SET status = 'pending', error_code = NULL, error_msg = NULL,
            next_retry_at = NULL, updated_at = now()
        WHERE status = 'waiting_cookie'
        """
    )


# ---------------------------------------------------------------------------
# 统计 / 凭证
# ---------------------------------------------------------------------------
_SQL_STATS = """
SELECT status, count(*) AS cnt
FROM osint_link_task
GROUP BY status
"""


def get_stats() -> dict[str, int]:
    """各状态计数（GET /api/v1/stats 数据源；total = 各状态之和）。"""
    rows = db.fetch_all(_SQL_STATS)
    stats = {row["status"]: int(row["cnt"]) for row in rows}
    stats["total"] = sum(v for k, v in stats.items() if k != "total")
    return stats


def list_dead(limit: int = 5) -> list[LinkTask]:
    """最近死信样本（飞书告警卡片用）。"""
    rows = db.fetch_all(
        "SELECT * FROM osint_link_task WHERE status = 'dead' ORDER BY updated_at DESC LIMIT :limit",
        {"limit": limit},
    )
    return [row_to_task(r) for r in rows]


def save_credential(name: str, value_enc: str, updated_by: str | None) -> None:
    """写入/覆盖凭证（upsert；value 必须是 Fernet 密文，加密在 API 层完成）。"""
    db.execute(
        """
        INSERT INTO osint_credential (name, value_enc, status, updated_by, updated_at)
        VALUES (:name, :value_enc, 'active', :updated_by, now())
        ON CONFLICT (name) DO UPDATE
        SET value_enc = EXCLUDED.value_enc, status = 'active',
            updated_by = EXCLUDED.updated_by, updated_at = now()
        """,
        {"name": name, "value_enc": value_enc, "updated_by": updated_by},
    )


def get_credential(name: str) -> dict[str, Any] | None:
    """读取凭证（不返回明文，仅状态/更新人/更新时间——审计与展示用）。"""
    rows = db.fetch_all(
        """
        SELECT name, status, updated_by, updated_at
        FROM osint_credential WHERE name = :name LIMIT 1
        """,
        {"name": name},
    )
    if not rows:
        return None
    row = dict(rows[0])
    row["updated_at"] = isoformat_z(row.get("updated_at"))
    return row
