"""B1 先验方向的版本化定义：P0（当前研究基线）与 P1（A 股反转先验）。

P0 保留 `factor_lib_tushare.B1_PRIOR_DIRECTIONS` 原样，本模块只读不改；
P1 是**独立假设**：把 Tushare-动量族的 20 个因子全部按 A 股反转先验设为
-1（含原本已为 -1 的 return_5d/return_21d），其他族方向、unknown 集合、
81 个策略因子集合保持不变。

这样 P0/P1 可并列回测、各自落盘，不会互相覆盖。P1 的方向固定在本模块，
不允许根据回测结果回头改；如果未来要改，应新建 P2，而不是改 P1。
"""
from __future__ import annotations

from dataclasses import dataclass

from factor_lib_tushare import (
    B1_PRIOR_DIRECTIONS,
    B1_PRIOR_UNKNOWN,
    B1_STRATEGY_FACTORS,
    TUSHARE_FACTOR_MAP,
)

MOMENTUM_FACTOR_TYPE = "Momentum"
P1_MOMENTUM_DIRECTION = -1


def _momentum_names() -> tuple[str, ...]:
    return tuple(
        name for name in B1_PRIOR_DIRECTIONS
        if (TUSHARE_FACTOR_MAP.get(name) or {}).get("factor_type") == MOMENTUM_FACTOR_TYPE
    )


def build_p1_directions() -> dict[str, int]:
    """P1：动量族整体按反转先验取 -1；其余因子沿用 P0。"""
    directions = dict(B1_PRIOR_DIRECTIONS)
    for name in _momentum_names():
        directions[name] = P1_MOMENTUM_DIRECTION
    return directions


B1_P1_PRIOR_DIRECTIONS: dict[str, int] = build_p1_directions()
B1_P1_STRATEGY_FACTORS: tuple[str, ...] = tuple(
    name for name in B1_STRATEGY_FACTORS if name not in B1_PRIOR_UNKNOWN
)
B1_PRIOR_CHANGED_P1: dict[str, dict[str, int]] = {
    name: {"p0": int(B1_PRIOR_DIRECTIONS[name]), "p1": int(B1_P1_PRIOR_DIRECTIONS[name])}
    for name in B1_PRIOR_DIRECTIONS
    if int(B1_PRIOR_DIRECTIONS[name]) != int(B1_P1_PRIOR_DIRECTIONS[name])
}

PRIOR_VERSIONS = ("p0", "p1")
PRIOR_LABELS = {
    "p0": "P0 · 资产定价混合先验（研究基线）",
    "p1": "P1 · A 股反转先验（动量族全反向，独立假设）",
}


@dataclass(frozen=True)
class PriorVersion:
    version: str
    label: str
    directions: dict[str, int]
    changed_vs_p0: dict[str, dict[str, int]]


def get_prior(version: str = "p0") -> PriorVersion:
    """返回指定版本的先验；P0 为默认，P1 不覆盖 P0。"""
    if version == "p0":
        return PriorVersion(
            version="p0",
            label=PRIOR_LABELS["p0"],
            directions=dict(B1_PRIOR_DIRECTIONS),
            changed_vs_p0={},
        )
    if version == "p1":
        return PriorVersion(
            version="p1",
            label=PRIOR_LABELS["p1"],
            directions=dict(B1_P1_PRIOR_DIRECTIONS),
            changed_vs_p0=dict(B1_PRIOR_CHANGED_P1),
        )
    raise ValueError(f"未知先验版本: {version!r}；可选 {PRIOR_VERSIONS}")


def prior_meta(version: str = "p0") -> dict:
    """给回测 JSON/报告用的版本元数据（不含大矩阵）。"""
    prior = get_prior(version)
    return {
        "version": prior.version,
        "label": prior.label,
        "n_directions": len(prior.directions),
        "n_changed_vs_p0": len(prior.changed_vs_p0),
        "changed_vs_p0": prior.changed_vs_p0,
        "unknown": sorted(B1_PRIOR_UNKNOWN),
        "strategy_factors": list(B1_P1_STRATEGY_FACTORS),
    }


__all__ = [
    "PRIOR_VERSIONS",
    "PRIOR_LABELS",
    "B1_P1_PRIOR_DIRECTIONS",
    "B1_P1_STRATEGY_FACTORS",
    "B1_PRIOR_CHANGED_P1",
    "PriorVersion",
    "get_prior",
    "prior_meta",
]
