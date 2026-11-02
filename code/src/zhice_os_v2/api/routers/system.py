"""系统 API。"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Response

from zhice_os_v2 import __version__
from zhice_os_v2.api.dependencies import get_db, get_market_data
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(tags=["system"])

# 任务状态上报文件（由定时/手动任务写入；本模块只读，不写 DB、不触发任务）。
DEFAULT_JOB_STATUS_PATH = r"C:\Temp\zhice_job_status.json"
_JOB_STATUS_FIELDS = (
    "status",
    "scheduled_at",
    "started_at",
    "finished_at",
    "source",
    "data_date",
    "rc",
    "steps",
)
_CST = timezone(timedelta(hours=8))

# 每日任务尾部失败告警文件（由 scripts/run_daily_research_ops.py 写；本模块只读）。
DEFAULT_DAILY_ALERT_PATH = r"C:\Temp\zhice_daily_task_alert.json"
_DAILY_ALERT_FIELDS = (
    "event",
    "severity",
    "generated_at",
    "run_id",
    "source",
    "scheduled_at",
    "data_date",
    "status",
    "rc",
    "message",
    "job_status_path",
)



@router.get("/health")
def health():
    """存活探针（liveness）：进程能响应即 ok，不探依赖。"""
    return {"status": "ok", "app": "zhice-os-v2", "version": __version__}


@router.get("/health/ready")
def health_ready():
    """就绪探针（readiness）：真探 DB（SELECT 1）。供容器 HEALTHCHECK/负载均衡使用，
    依赖故障时返回 503，避免"假健康"继续被打流量。"""
    from zhice_os_v2.config.settings import get_settings

    checks: dict[str, str] = {}
    if get_settings().persistence_backend == "sql":
        from sqlalchemy import text

        from zhice_os_v2.infrastructure.db.session import get_session

        s = get_session()
        try:
            s.execute(text("SELECT 1"))
            checks["db"] = "ok"
        except Exception as e:  # noqa: BLE001
            raise HTTPException(
                status_code=503, detail={"status": "not_ready", "db": str(e)[:120]}
            ) from e
        finally:
            s.close()
    else:
        checks["db"] = "skipped(memory)"
    return {"status": "ready", "checks": checks}


@router.get("/system/job-status")
def job_status():
    """只读任务状态（供前端运行态状态条）：读取 C:\\Temp\\zhice_job_status.json。

    - 文件不存在 -> 404，说明“任务尚未上报”（前端显示未上报态，不是服务故障）；
    - 文件损坏/顶层非 object -> 503，避免把坏状态当成功展示；
    - 只读：仅读文件与 stat，不写 DB、不触发任务；路径可用环境变量
      ZHICE_JOB_STATUS_PATH 覆盖（测试/运维用）。
    """
    path = Path(os.environ.get("ZHICE_JOB_STATUS_PATH", DEFAULT_JOB_STATUS_PATH))
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={
                "code": "system.job_status_not_reported",
                "message": "任务尚未上报",
                "path": str(path),
                "hint": "等待定时/手动任务运行后写入 C:\\Temp\\zhice_job_status.json",
            },
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "system.job_status_unreadable",
                "message": "任务状态文件损坏，无法解析",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "system.job_status_invalid",
                "message": "任务状态文件结构非法（顶层需为 object）",
                "path": str(path),
            },
        )
    steps = payload.get("steps")
    result = {field: payload.get(field) for field in _JOB_STATUS_FIELDS}
    result["steps"] = [] if steps is None else steps
    result["reported"] = True
    result["reported_at"] = datetime.fromtimestamp(
        path.stat().st_mtime, _CST
    ).replace(microsecond=0).isoformat()
    result["file"] = {"path": str(path), "bytes": path.stat().st_size, "read_only": True}
    return result


@router.get("/system/daily-alert")
def daily_alert():
    """只读每日任务尾部失败告警：读取 C:\\Temp\\zhice_daily_task_alert.json。

    - 文件不存在 / 无 failures -> 204（前端静默，无告警）；
    - 有 failures -> 200，返回 severity/failures（含 rc/stderr）及 run 上下文；
    - 损坏/顶层非 object -> 503；路径可用 ZHICE_DAILY_ALERT_PATH 覆盖；
    - 只读：不写 DB、不触发任务。
    """
    path = Path(os.environ.get("ZHICE_DAILY_ALERT_PATH", DEFAULT_DAILY_ALERT_PATH))
    if not path.is_file():
        return Response(status_code=204)
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "system.daily_alert_unreadable",
                "message": "每日任务告警文件损坏，无法解析",
                "path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    if not isinstance(payload, dict):
        raise HTTPException(
            status_code=503,
            detail={
                "code": "system.daily_alert_invalid",
                "message": "每日任务告警结构非法（顶层需为 object）",
                "path": str(path),
            },
        )
    failures = payload.get("failures")
    if not isinstance(failures, list) or not failures:
        return Response(status_code=204)
    result = {field: payload.get(field) for field in _DAILY_ALERT_FIELDS}
    result["failures"] = failures
    result["reported"] = True
    result["reported_at"] = datetime.fromtimestamp(
        path.stat().st_mtime, _CST
    ).replace(microsecond=0).isoformat()
    result["file"] = {"path": str(path), "bytes": path.stat().st_size, "read_only": True}
    return result


@router.get("/status")
def status(db: PersistencePort = Depends(get_db), data=Depends(get_market_data)):
    return {
        "version": __version__,
        "strategies": len(db.list_strategies()),
        "strategy_versions": len(db.list_strategy_versions()),
        "approvals": len(db.list_approvals()),
        "positions": len(db.list_open_positions()),
        "replays": len(db.list_replays()),
        "quotes": len(data.list_quotes()),
    }
