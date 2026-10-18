"""系统 API。"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from zhice_os_v2 import __version__
from zhice_os_v2.api.dependencies import get_db, get_market_data
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(tags=["system"])


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
