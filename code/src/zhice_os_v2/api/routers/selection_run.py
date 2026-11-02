"""P1 受控手动选股 API（默认关闭，不部署；独立路由，不开放 /workflow/pre-market）。

设计目标
--------
给试运行/运维提供一个**受控**的手动选股入口，而不是把 `/workflow/pre-market`
直接暴露给所有登录用户。本路由把「能不能跑」收敛为下面所有门禁都通过才执行：

1. **feature flag 默认 off**：`SELECTION_RUN_ENABLED` 未显式打开时返回 503
   `selection_run.disabled`，不暴露任何可执行面；打开只认 `1/true/yes/on`。
2. **角色门**：仅 `ADMIN` / `TRADER`（复用 `require_roles`；VIEWER/USER 等 403）。
3. **市场交易日校验**：market ∈ {CN,HK,US,CRYPTO}；trade_date 必须是该市场交易日。
   `v2_market_calendar` 有记录时以其 `is_open` 为准；查不到/表不可用时回退
   `MarketProfile.fallback_is_open`（周一~五），异常 fail-closed。
4. **并发锁**：进程内 + 文件锁（`O_CREAT|O_EXCL`），同一时刻只允许一个手动 run；
   跨进程/多 worker 同样生效。锁带 TTL（`SELECTION_RUN_LOCK_TTL_SEC`，默认 6h），
   异常残留锁超过 TTL 可被接管。
5. **审计**：每次 run 追加 JSONL 到 `C:\\Temp\\selection_run_audit.jsonl`
   （`SELECTION_RUN_AUDIT_PATH` 覆盖），含 `source="manual"`、`operator`
   （用户名+ID+角色）、市场/日期/参数/状态/时间戳/错误/结果摘要；拒绝请求也记 rejected。
6. **幂等**：`Idempotency-Key` 请求头或 body `idempotency_key`；相同 key 的重复请求
   返回**同一 run_id**（`idempotent_replay=true`），不会重复执行；同 key 参数不同 →
   409 `selection.idempotency_conflict`。进程内索引之外，还会从审计 JSONL 回放该 key
   的终态记录（跨进程/重启兜底）。
7. **同步执行**：手动操作允许同步等待（调用方自行设超时）；执行期间锁保持，
   完成后写终态审计。失败返回 500 `selection_run.failed`（带 run_id/status）。

默认 runner
-----------
`_default_runner` 复用现有 `WorkflowService.run_pre_market(...)`（盘前选股/候选/
计划链路），只新增受控外壳，不新开交易路径；测试通过 `get_selection_runner`
依赖覆盖为假 runner，不触 DB/不执行真实选股。

挂载说明（重要）
----------------
本任务写范围仅 `selection_run.py` + 测试，**未改 main.py**。生产启用时需在
`src/zhice_os_v2/api/main.py` 增加一行（并在启动前设置 SELECTION_RUN_ENABLED=1）::

    from zhice_os_v2.api.routers import selection_run
    app.include_router(selection_run.router, prefix="/api/v1")

在此之前该路由不会出现在生产 app 的 /docs 中（feature flag 默认 off 也保证不可执行）。
本模块自身不写生产 DB；默认 runner 被调用时才会走现有 workflow 服务（默认 off）。
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Callable

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from zhice_os_v2.api.dependencies import get_current_user, get_db, require_roles
from zhice_os_v2.domain.security.user import Role, User
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/selection", tags=["selection"],
                   dependencies=[Depends(get_current_user)])

FEATURE_FLAG_ENV = "SELECTION_RUN_ENABLED"
AUDIT_PATH_ENV = "SELECTION_RUN_AUDIT_PATH"
LOCK_PATH_ENV = "SELECTION_RUN_LOCK_PATH"
LOCK_TTL_ENV = "SELECTION_RUN_LOCK_TTL_SEC"

DEFAULT_AUDIT_PATH = r"C:\Temp\selection_run_audit.jsonl"
DEFAULT_LOCK_PATH = r"C:\Temp\selection_run.lock"
DEFAULT_LOCK_TTL_SEC = 6 * 3600
AUDIT_SCHEMA = "zhice.selection_run_audit/v1"

MARKETS = ("CN", "HK", "US", "CRYPTO")
TERMINAL_STATUSES = {"succeeded", "failed", "rejected"}

# 进程内运行记录 / 幂等索引；测试通过 _reset_state() 清理。
_RUNS: dict[str, dict[str, Any]] = {}
_IDEMPOTENCY: dict[str, str] = {}
_STATE_LOCK = threading.Lock()
_AUDIT_LOCK = threading.Lock()
_LOCK_FILE_LOCK = threading.Lock()


class SelectionRunRequest(BaseModel):
    market: str = Field(default="CN", description="CN/HK/US/CRYPTO")
    trade_date: str | None = Field(default=None, description="YYYY-MM-DD；缺省=市场时区今天")
    strategy_type: str = Field(default="TREND", min_length=1, max_length=32)
    max_candidates: int = Field(default=10, ge=1, le=100)
    idempotency_key: str | None = Field(default=None, min_length=4, max_length=128)


def _enabled() -> bool:
    return str(os.environ.get(FEATURE_FLAG_ENV, "")).strip().lower() in {
        "1", "true", "yes", "on",
    }


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.astimezone(UTC).isoformat()


def _audit_path() -> Path:
    return Path(os.environ.get(AUDIT_PATH_ENV, DEFAULT_AUDIT_PATH))


def _lock_path() -> Path:
    return Path(os.environ.get(LOCK_PATH_ENV, DEFAULT_LOCK_PATH))


def _lock_ttl_sec() -> int:
    try:
        return max(60, int(os.environ.get(LOCK_TTL_ENV, str(DEFAULT_LOCK_TTL_SEC))))
    except (TypeError, ValueError):
        return DEFAULT_LOCK_TTL_SEC


def _operator(current_user: User) -> tuple[str, str, str]:
    """返回 (operator, operator_id, role)。"""
    username = (getattr(current_user, "username", None) or "").strip()
    user_id = str(getattr(current_user, "user_id", "") or "")
    role = str(getattr(current_user, "role", "") or "")
    return (username or user_id or "unknown", user_id, role)


def _default_trade_date(market: str) -> date:
    from zhice_os_v2.domain.market.registry import get_profile

    return datetime.now(get_profile(market).tz).date()


def _is_trading_day(market: str, trade_date: date) -> bool:
    """市场交易日校验：日历表优先，取不到回退 Profile fallback（异常 fail-closed）。"""
    try:
        from zhice_os_v2.domain.market.registry import get_profile

        profile = get_profile(market)
    except Exception:  # noqa: BLE001 - 未知市场 fail-closed
        return False

    try:
        import sqlalchemy as sa

        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            row = session.execute(sa.text(
                "SELECT is_open FROM v2_market_calendar "
                "WHERE market=:m AND trade_date=:d"
            ), {"m": market, "d": trade_date}).first()
        finally:
            session.close()
        if row is not None:
            return bool(row[0])
    except Exception:  # noqa: BLE001 - 日历不可用回退 weekday 近似
        pass
    try:
        return bool(profile.fallback_is_open(trade_date))
    except Exception:  # noqa: BLE001
        return False


def _append_audit(record: dict[str, Any]) -> bool:
    """追加一行 JSONL 审计；失败只返回 False（不掩盖已发生的 run）。"""
    path = _audit_path()
    line = json.dumps({"schema": AUDIT_SCHEMA, "ts": _iso(_utc_now()), **record},
                      ensure_ascii=False, default=str) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _AUDIT_LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line)
        return True
    except Exception:  # noqa: BLE001
        return False


# --------------------------------------------------------------------------- #
# 并发锁：进程内互斥 + 跨进程 O_EXCL 文件锁（带 TTL 接管）
# --------------------------------------------------------------------------- #
def _read_lock_file(path: Path) -> dict[str, Any] | None:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _lock_age_sec(holder: dict[str, Any] | None, path: Path) -> float:
    if holder:
        try:
            started = datetime.fromisoformat(str(holder.get("started_at")))
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            return max(0.0, (_utc_now() - started).total_seconds())
        except Exception:  # noqa: BLE001
            pass
    try:
        return max(0.0, (_utc_now().timestamp() - path.stat().st_mtime))
    except OSError:
        return 0.0


def _lock_is_stale(holder: dict[str, Any] | None, path: Path) -> bool:
    ttl = _lock_ttl_sec()
    if _lock_age_sec(holder, path) > ttl:
        return True
    if holder:
        run_id = str(holder.get("run_id") or "")
        with _STATE_LOCK:
            rec = _RUNS.get(run_id)
        if rec is not None and rec.get("status") in TERMINAL_STATUSES:
            return True
    return False


def _acquire_lock(record: dict[str, Any]) -> tuple[bool, dict[str, Any] | None]:
    """尝试获取文件锁。返回 (ok, 现有 holder 或新 record)。"""
    path = _lock_path()
    payload = json.dumps(record, ensure_ascii=False)
    with _LOCK_FILE_LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:  # noqa: BLE001
            return False, None
        for _ in range(2):  # 第二轮用于接管 stale 锁
            try:
                fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                holder = _read_lock_file(path)
                if _lock_is_stale(holder, path):
                    try:
                        path.unlink()
                    except OSError:
                        return False, holder
                    continue
                return False, holder
            except OSError:
                return False, None
            try:
                os.write(fd, payload.encode("utf-8"))
            finally:
                os.close(fd)
            return True, record
    return False, None


def _release_lock(run_id: str) -> None:
    path = _lock_path()
    with _LOCK_FILE_LOCK:
        holder = _read_lock_file(path)
        if holder is not None and str(holder.get("run_id") or "") == run_id:
            try:
                path.unlink()
            except OSError:
                pass


def _reset_state() -> None:
    """测试辅助：清空进程内运行记录/幂等索引。"""
    with _STATE_LOCK:
        _RUNS.clear()
        _IDEMPOTENCY.clear()


# --------------------------------------------------------------------------- #
# 默认 runner：复用现有盘前选股服务（只加受控外壳，不新开交易路径）
# --------------------------------------------------------------------------- #
def _default_runner(
    *,
    market: str,
    trade_date: date,
    strategy_type: str,
    max_candidates: int,
    operator: str,
    db: PersistencePort,
) -> dict:
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    return WorkflowService(db).run_pre_market(
        market,
        trade_date,
        max_candidates=max_candidates,
        strategy_type=strategy_type,
    )


def get_selection_runner() -> Callable[..., dict]:
    """FastAPI 依赖：返回 runner；测试用 dependency_overrides 替换为假实现。"""
    return _default_runner


# --------------------------------------------------------------------------- #
# 辅助：结果摘要 / 运行响应 / 运行记录
# --------------------------------------------------------------------------- #
def _summarize(result: Any) -> Any:
    """把 runner 返回值裁剪成可安全 JSON 化的摘要（不返回全量候选明细）。"""
    if not isinstance(result, dict):
        return {"type": type(result).__name__}
    out: dict[str, Any] = {}
    for key in ("market", "trade_date", "regime", "regime_routed_out"):
        if key in result:
            out[key] = result[key]
    for key in ("candidates", "plans", "approvals"):
        value = result.get(key)
        if isinstance(value, list):
            out[key] = len(value)
        elif isinstance(value, (int, float, str, bool)) or value is None:
            out[key] = value
    # 其余值只保留可 JSON 安全的标量，避免把大对象写进审计/响应
    for key, value in result.items():
        if key in out:
            continue
        if isinstance(value, (int, float, str, bool)) or value is None:
            out[key] = value
    return out


def _response(record: dict[str, Any], *, replay: bool = False) -> dict[str, Any]:
    return {
        "run_id": record["run_id"],
        "status": record["status"],
        "source": record["source"],
        "operator": record["operator"],
        "market": record["market"],
        "trade_date": record["trade_date"],
        "strategy_type": record["strategy_type"],
        "max_candidates": record["max_candidates"],
        "started_at": record["started_at"],
        "finished_at": record.get("finished_at"),
        "duration_sec": record.get("duration_sec"),
        "result": record.get("result_summary"),
        "error": record.get("error"),
        "idempotent_replay": replay,
        "audit_written": record.get("audit_written", False),
    }


def _record_run(record: dict[str, Any]) -> None:
    with _STATE_LOCK:
        _RUNS[record["run_id"]] = record
        if record.get("idempotency_key"):
            _IDEMPOTENCY[str(record["idempotency_key"])] = record["run_id"]
        if len(_RUNS) > 500:
            for rid in [k for k, v in _RUNS.items()
                        if v.get("status") in TERMINAL_STATUSES][:100]:
                _RUNS.pop(rid, None)


def _find_idempotent(key: str | None) -> dict[str, Any] | None:
    if not key:
        return None
    with _STATE_LOCK:
        run_id = _IDEMPOTENCY.get(key)
        return dict(_RUNS[run_id]) if run_id and run_id in _RUNS else None


def _audit_replay(key: str | None) -> dict[str, Any] | None:
    """跨进程/重启幂等兜底：从审计 JSONL 找该 key 最近一次终态 run。

    进程内索引在重启/多 worker 下会丢；审计文件是持久化的受控记录，因此对
    终态（succeeded/failed）按 key 回放，避免同一请求重复触发选股。
    """
    if not key:
        return None
    path = _audit_path()
    try:
        raw_lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return None
    for line in reversed(raw_lines):
        try:
            rec = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(rec, dict) or rec.get("idempotency_key") != key:
            continue
        if rec.get("status") not in ("succeeded", "failed"):
            continue
        return {
            "run_id": rec.get("run_id"),
            "status": rec.get("status"),
            "source": rec.get("source") or "manual",
            "operator": rec.get("operator"),
            "market": rec.get("market"),
            "trade_date": rec.get("trade_date"),
            "strategy_type": rec.get("strategy_type"),
            "max_candidates": rec.get("max_candidates"),
            "started_at": rec.get("started_at"),
            "finished_at": rec.get("finished_at"),
            "duration_sec": rec.get("duration_sec"),
            "result_summary": rec.get("result_summary"),
            "error": rec.get("error"),
            "audit_written": True,
        }
    return None


def _replay_or_reject(record: dict[str, Any], params: dict[str, Any]) -> dict[str, Any]:
    """相同幂等键：参数一致则回放；参数不同抛 409。"""
    same = all(record.get(k) == v for k, v in params.items())
    if not same:
        raise HTTPException(status_code=409, detail={
            "code": "selection.idempotency_conflict",
            "run_id": record["run_id"],
            "message": "同一 Idempotency-Key 的参数与首次请求不一致",
        })
    return record


def _new_run_id() -> str:
    return f"sel-{_utc_now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:8]}"


# --------------------------------------------------------------------------- #
# 路由
# --------------------------------------------------------------------------- #
@router.post("/run")
def run_selection(
    body: SelectionRunRequest,
    idempotency_key_header: str | None = Header(default=None, alias="Idempotency-Key"),
    current_user: User = Depends(require_roles(Role.ADMIN, Role.TRADER)),
    db: PersistencePort = Depends(get_db),
    runner: Callable[..., dict] = Depends(get_selection_runner),
) -> dict[str, Any]:
    """受控手动选股（默认关闭；ADMIN/TRADER；交易日/并发/审计/幂等）。"""
    if not _enabled():
        raise HTTPException(status_code=503, detail={
            "code": "selection_run.disabled",
            "message": f"{FEATURE_FLAG_ENV} 未开启；手动选股 API 默认关闭",
        })

    market = (body.market or "").strip().upper()
    if market not in MARKETS:
        raise HTTPException(status_code=400, detail={
            "code": "selection.invalid_market",
            "message": f"market 必须是 {MARKETS} 之一",
        })

    if body.trade_date:
        try:
            trade_date = date.fromisoformat(body.trade_date)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail={
                "code": "selection.invalid_trade_date",
                "message": "trade_date 必须是 YYYY-MM-DD",
            }) from exc
    else:
        trade_date = _default_trade_date(market)

    operator, operator_id, role = _operator(current_user)
    idem_key = (body.idempotency_key or idempotency_key_header or "").strip() or None
    params = {
        "market": market,
        "trade_date": trade_date.isoformat(),
        "strategy_type": body.strategy_type,
        "max_candidates": body.max_candidates,
    }

    # 幂等：已存在同 key 的 run → 回放（进程内优先；重启/多 worker 回退审计 JSONL）
    prior = _find_idempotent(idem_key) or _audit_replay(idem_key)
    if prior is not None:
        prior = _replay_or_reject(prior, params)
        if prior["status"] == "failed":
            raise HTTPException(status_code=500, detail={
                "code": "selection_run.failed",
                **_response(prior, replay=True),
            })
        return _response(prior, replay=True)

    # 交易日校验（在拿锁之前，避免无意义占用）
    if not _is_trading_day(market, trade_date):
        rejected = {
            "run_id": _new_run_id(), "status": "rejected", "source": "manual",
            "operator": operator, "operator_id": operator_id, "role": role,
            "market": market, "trade_date": trade_date.isoformat(),
            "strategy_type": body.strategy_type, "max_candidates": body.max_candidates,
            "idempotency_key": idem_key, "reason": "non_trading_day",
            "started_at": _iso(_utc_now()), "finished_at": _iso(_utc_now()),
        }
        _append_audit(rejected)
        raise HTTPException(status_code=400, detail={
            "code": "selection.non_trading_day",
            "message": f"{market} {trade_date.isoformat()} 不是交易日，拒绝手动选股",
            "run_id": rejected["run_id"],
        })

    # 并发锁：同一时刻仅一个手动 run
    preview = {
        "run_id": _new_run_id(), "source": "manual", "operator": operator,
        "operator_id": operator_id, "role": role, "market": market,
        "trade_date": trade_date.isoformat(), "strategy_type": body.strategy_type,
        "max_candidates": body.max_candidates, "idempotency_key": idem_key,
        "started_at": _iso(_utc_now()), "status": "running", "pid": os.getpid(),
    }
    acquired, holder = _acquire_lock(preview)
    if not acquired:
        holder = holder or {}
        raise HTTPException(status_code=409, detail={
            "code": "selection.run_in_progress",
            "message": "已有手动选股 run 正在执行（并发锁）",
            "run_id": holder.get("run_id"),
            "operator": holder.get("operator"),
            "started_at": holder.get("started_at"),
        })

    run_id = preview["run_id"]
    record: dict[str, Any] = dict(preview)
    record.update({"status": "running", "audit_written": False})
    _record_run(record)
    record["audit_written"] = _append_audit(record)

    started = _utc_now()
    try:
        result = runner(
            market=market,
            trade_date=trade_date,
            strategy_type=body.strategy_type,
            max_candidates=body.max_candidates,
            operator=operator,
            db=db,
        )
        record["status"] = "succeeded"
        record["result_summary"] = _summarize(result)
    except Exception as exc:  # noqa: BLE001 - 受控 API 记录失败并返回 500
        record["status"] = "failed"
        record["error"] = f"{type(exc).__name__}: {exc}"[:500]
    finally:
        finished = _utc_now()
        record["finished_at"] = _iso(finished)
        record["duration_sec"] = round((finished - started).total_seconds(), 3)
        _release_lock(run_id)
        _record_run(record)
        record["audit_written"] = _append_audit(record)

    if record["status"] == "failed":
        raise HTTPException(status_code=500, detail={
            "code": "selection_run.failed",
            **_response(record),
        })
    return _response(record)


@router.get("/run/{run_id}")
def get_selection_run(
    run_id: str,
    current_user: User = Depends(require_roles(Role.ADMIN, Role.TRADER)),
) -> dict[str, Any]:
    """查询手动选股 run 状态（同一受控角色；仅内存记录，重启丢失）。"""
    with _STATE_LOCK:
        record = dict(_RUNS[run_id]) if run_id in _RUNS else None
    if record is None:
        raise HTTPException(status_code=404, detail={
            "code": "selection.run_not_found",
            "message": "run_id 不存在或服务重启后已丢失（审计 JSONL 仍保留）",
        })
    return _response(record)
