"""风控参数（settings.risk_params）读取、校验与回退。

数据源：v2_app_state(key='settings')，与 circuit_breaker.is_tripped 同范式——
每次调用实时读库，跨进程一致：API 进程 PUT /settings/risk-params 后，scheduler
进程下一轮读取即见新值（无需重启；已在跑的盘中循环下一轮生效）。

回退语义（保守，两类调用方语义不同）：
  - 风控规则阈值（max_total/max_single/max_daily_loss）：读取失败/任一值非法 →
    整体回退 DEFAULT_RISK_PARAMS（= api/routers/settings.py _DEFAULT_STATE，
    两处必须同步修改），规则照跑（默认值守门），不因读取失败而跳过规则。
  - 止损覆盖（stop_loss_pct）：读取失败/未配置 → 返回 None，调用方回退
    exit_policies.py 的策略级硬编码（0.04/0.05/0.06/0.07，"现行值"）。

单位换算：设置页/DB 存百分数（8.0 = 8%）；exit_policies 用小数（0.08）。

默认值 vs 库中值（2026-09-08 生产库实际配置，属用户配置非默认）：
  参数                      代码默认(本文件)   库中现值
  max_single_position_pct       20.0            25.0
  max_total_position_pct        80.0            75.0
  max_daily_loss_pct             2.0             3.0
  stop_loss_pct                  6.0             8.0
库中有记录时以库中值为准；代码默认仅在"无任何持久化设置且读取失败"时兜底。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

_STATE_KEY = "settings"
_KEYS = ("max_single_position_pct", "max_total_position_pct",
         "max_daily_loss_pct", "stop_loss_pct")


@dataclass(frozen=True)
class RiskParams:
    """四参数（均为百分数 0-100）。"""

    max_single_position_pct: float   # 单标的市值占子账户权益上限%（SINGLE_SECURITY_EXPOSURE）
    max_total_position_pct: float    # 总持仓占子账户权益上限%（PORTFOLIO_TOTAL_EXPOSURE）
    max_daily_loss_pct: float        # 单日亏损熔断%（相对子账户初始资金，DAILY_LOSS）
    stop_loss_pct: float             # 全局止损线%（覆盖 exit_policies 策略级硬编码）


# 与 api/routers/settings.py _DEFAULT_STATE["risk_params"] 保持一致（同步修改）。
DEFAULT_RISK_PARAMS = RiskParams(
    max_single_position_pct=20.0,
    max_total_position_pct=80.0,
    max_daily_loss_pct=2.0,
    stop_loss_pct=6.0,
)


def _coerce(raw) -> float | None:
    """数值校验：可转 float 且 0 < v <= 100（与设置页 PUT 的 ge=0/le=100 对齐；
    0 无意义——等于关闭规则，按非法回退）。NaN/Inf 与非法返回 None。"""
    try:
        v = float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if not (v > 0) or not (v <= 100):  # NaN 与任何数比较均为 False → 双重 not 拦截
        return None
    return v


def _load_raw_risk_params() -> dict | None:
    """读原始 settings.risk_params dict。

    非 sql 后端（单测隔离，防连真库）/ DB 读取失败 / 结构不是 dict → None
    （调用方按各自语义回退）。
    """
    try:
        from zhice_os_v2.config.settings import get_settings
        if get_settings().persistence_backend != "sql":
            return None
        from zhice_os_v2.api.routers.app_state_store import _load_state
        raw = _load_state(_STATE_KEY, {}).get("risk_params")
        return raw if isinstance(raw, dict) else None
    except Exception:  # noqa: BLE001 - 读取失败必须回退，不得中断风控/止损链
        return None


def load_risk_params() -> RiskParams:
    """风控规则阈值：任一参数缺失/非法 → 整体回退代码默认（规则照跑）。"""
    raw = _load_raw_risk_params()
    if raw is None:
        return DEFAULT_RISK_PARAMS
    vals = {k: _coerce(raw.get(k)) for k in _KEYS}
    if any(v is None for v in vals.values()):
        return DEFAULT_RISK_PARAMS
    return RiskParams(**vals)  # type: ignore[arg-type]


def settings_stop_loss_override() -> Decimal | None:
    """止损覆盖值（小数）：设置页百分数 8.0 → 0.08。

    读取失败/未配置/值非法 → None，调用方回退策略级硬编码（现行 0.04~0.07）。
    """
    raw = _load_raw_risk_params()
    if raw is None:
        return None
    v = _coerce(raw.get("stop_loss_pct"))
    if v is None:
        return None
    # 百分数 → 小数（8.0 → 0.08）。Decimal 化避免 float 精度尾差。
    return Decimal(str(v)) / Decimal("100")


def effective_stop_loss_pct(fallback: Decimal) -> Decimal:
    """止损接线入口：设置可用用设置值（小数），否则回退 fallback（策略硬编码）。"""
    override = settings_stop_loss_override()
    return override if override is not None and override > 0 else fallback
