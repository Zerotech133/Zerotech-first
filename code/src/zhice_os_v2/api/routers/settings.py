"""设置 API（V1 settings 对齐）。持久化到 v2_app_state（key='settings'），DB 失败回退内存。"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from zhice_os_v2.api.dependencies import get_current_user, require_roles
from zhice_os_v2.api.routers.app_state_store import _load_state, _save_state
from zhice_os_v2.domain.security.user import Role, User

router = APIRouter(prefix="/settings", tags=["settings"], dependencies=[Depends(get_current_user)])

TradeMode = Literal["manual", "semi_auto", "full_auto"]

_STATE_KEY = "settings"

_DEFAULT_STATE: dict = {
    "trade_mode": "semi_auto",  # manual | semi_auto | full_auto
    "risk_params": {
        "max_single_position_pct": 20.0,   # 单标的仓位上限 %
        "max_total_position_pct": 80.0,    # 组合总仓位上限 %
        "max_daily_loss_pct": 2.0,         # 单日最大亏损熔断 %（相对初始资金）
        "stop_loss_pct": 6.0,              # 单笔止损线 %
    },
}

# 启动时从 DB 加载（重启后保留用户设置）
_state: dict = _load_state(_STATE_KEY, _DEFAULT_STATE)


def _persist() -> None:
    _save_state(_STATE_KEY, _state)


class TradeModeBody(BaseModel):
    mode: TradeMode


class RiskParamsBody(BaseModel):
    # gt=0（Minor #4 语义对齐）：0 会关闭规则，而读取层（risk_params._coerce）把 0
    # 视为非法整体回退默认——存 0 实际得到默认值守门而非关闭，语义矛盾。统一在入口
    # 拒绝；存量 0 值读取层行为不变（回退默认）。文案键 errors.json settings.risk_params_gt_zero。
    max_single_position_pct: float = Field(gt=0, le=100)
    max_total_position_pct: float = Field(gt=0, le=100)
    max_daily_loss_pct: float = Field(gt=0, le=100)
    stop_loss_pct: float = Field(gt=0, le=100)


@router.get("")
def get_settings():
    return _state


@router.put("/trade-mode")
def set_trade_mode(body: TradeModeBody, _: User = Depends(require_roles(Role.ADMIN, Role.TRADER))):
    """设置交易模式（持久化）。仅 ADMIN/TRADER 可操作。返回更新后 state。"""
    _state["trade_mode"] = body.mode
    _persist()
    return _state


@router.put("/risk-params")
def set_risk_params(body: RiskParamsBody, _: User = Depends(require_roles(Role.ADMIN, Role.TRADER))):
    """更新风控参数（各 0-100 合理区间，越界 422；持久化）。仅 ADMIN/TRADER 可操作。返回更新后 state。"""
    _state["risk_params"] = {
        "max_single_position_pct": body.max_single_position_pct,
        "max_total_position_pct": body.max_total_position_pct,
        "max_daily_loss_pct": body.max_daily_loss_pct,
        "stop_loss_pct": body.stop_loss_pct,
    }
    _persist()
    return _state
