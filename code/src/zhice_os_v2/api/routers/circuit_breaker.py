"""自动化熔断 API（V1 circuit-breaker 对齐）。

- 动作权限矩阵：每个自动化动作有 AUTO / CONFIRM / BLOCKED 三级权限
- 熔断器：手动触发后所有 AUTO 动作冻结为 BLOCKED（急停），可整体复位
- 持久化：v2_app_state（key='circuit_breaker'），启动时加载，重启不复位
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from zhice_os_v2.api.dependencies import get_current_user, require_roles
from zhice_os_v2.api.routers.app_state_store import _load_state, _save_state
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/circuit-breaker", tags=["circuit-breaker"], dependencies=[Depends(get_current_user)])

_STATE_KEY = "circuit_breaker"

_DEFAULT_STATE: dict[str, Any] = {
    "active": False,
    "triggered_at": None,
    "reason": None,
    # action -> 用户覆盖的权限（未覆盖则用 default_permission）
    "actions": {},
    "log": [],
}

# V1 动作权限矩阵：默认级别
ACTIONS = [
    {"action": "pre_market_scan", "label": "盘前选股", "default_permission": "AUTO"},
    {"action": "create_plan", "label": "创建交易计划", "default_permission": "AUTO"},
    {"action": "open_position", "label": "建仓执行", "default_permission": "CONFIRM"},
    {"action": "add_position", "label": "加仓", "default_permission": "CONFIRM"},
    {"action": "close_position", "label": "平仓执行", "default_permission": "CONFIRM"},
    {"action": "stop_loss", "label": "止损止盈", "default_permission": "AUTO"},
    {"action": "daily_report", "label": "日报生成", "default_permission": "AUTO"},
]

_ACTION_NAMES = {a["action"] for a in ACTIONS}
_LOG_LIMIT = 50

Permission = Literal["AUTO", "CONFIRM", "BLOCKED"]


class TriggerBody(BaseModel):
    reason: str = Field(min_length=1)


class PermissionBody(BaseModel):
    permission: Permission


# 启动时从 DB 加载（重启后保持熔断状态）
_state: dict = _load_state(_STATE_KEY, _DEFAULT_STATE)


def _persist() -> None:
    _save_state(_STATE_KEY, _state)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _add_log(event: str, reason: str | None = None) -> None:
    _state["log"].insert(0, {"time": _now(), "event": event, "reason": reason})
    del _state["log"][_LOG_LIMIT:]


def _effective_permission(configured: str) -> str:
    """熔断激活时 AUTO 降级为 BLOCKED（CONFIRM/BLOCKED 不变）。"""
    if _state["active"] and configured == "AUTO":
        return "BLOCKED"
    return configured


def is_tripped() -> bool:
    """急停是否激活（从持久化状态实时读取，跨 worker/重启一致）。

    执行链（workflow 自动建仓/平仓）在自动执行前应查询：激活则拒绝，
    否则「急停」形同虚设——此前熔断状态根本不被执行链消费。
    """
    return bool(_load_state(_STATE_KEY, _DEFAULT_STATE).get("active"))


def _matrix() -> list[dict]:
    actions = []
    for meta in ACTIONS:
        configured = _state["actions"].get(meta["action"], meta["default_permission"])
        actions.append(
            {
                "action": meta["action"],
                "label": meta["label"],
                "default_permission": meta["default_permission"],
                "configured_permission": configured,
                "effective_permission": _effective_permission(configured),
            }
        )
    return actions


def _status() -> dict:
    return {
        "active": _state["active"],
        "triggered_at": _state["triggered_at"],
        "reason": _state["reason"],
        "actions": _matrix(),
        "log": _state["log"][:20],
    }


@router.get("/status")
def status():
    """熔断器状态 + 动作权限矩阵（熔断激活时 AUTO 显示为 BLOCKED）。附最近触发日志。"""
    return _status()


@router.post("/trigger")
def trigger(body: TriggerBody, _: User = Depends(require_roles(Role.ADMIN, Role.TRADER))):
    """手动触发熔断：所有 AUTO 动作冻结。记日志并持久化。仅 ADMIN/TRADER 可操作。"""
    _state["active"] = True
    _state["triggered_at"] = _now()
    _state["reason"] = body.reason
    _add_log("TRIGGER", body.reason)
    _persist()
    return _status()


@router.post("/reset")
def reset(_: User = Depends(require_roles(Role.ADMIN, Role.TRADER))):
    """整体复位熔断器。记日志并持久化。仅 ADMIN/TRADER 可操作。"""
    if _state["active"]:
        _add_log("RESET", _state["reason"])
    _state["active"] = False
    _state["triggered_at"] = None
    _state["reason"] = None
    _persist()
    return _status()


@router.post("/actions/{action}/permission")
def set_permission(action: str, body: PermissionBody, _: User = Depends(require_roles(Role.ADMIN, Role.TRADER))):
    """单个动作权限调整（V1 粒度）。记日志并持久化。仅 ADMIN/TRADER 可操作。"""
    if action not in _ACTION_NAMES:
        raise HTTPException(status_code=404, detail=f"unknown action: {action}")
    _state["actions"][action] = body.permission
    _add_log(f"PERMISSION {action} -> {body.permission}")
    _persist()
    return _status()
