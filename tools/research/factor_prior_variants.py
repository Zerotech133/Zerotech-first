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

# ---------------------------------------------------------------------------
# P2：21 日反转核心的动量 sleeve（2026-11-06）
#
# P2 不覆盖 P0/P1。它只替换「动量族」内部构造：用 5/21/60/120 四个精确回看
# 周期因子按预注册权重合成 sleeve，族间仍等权。权重来自 5/21/60/120 周期
# 拆分结果（21d 最强 → 0.60；5d 辅助 → 0.20；60/120 成本约束 → 0.15/0.05），
# 在跑 P2 之前冻结，不按 P2 结果回调。
# ---------------------------------------------------------------------------
P2_MOMENTUM_HORIZONS: dict[str, int] = {
    "b1_mom_5d": 5,
    "b1_mom_21d": 21,
    "b1_mom_60d": 60,
    "b1_mom_120d": 120,
}
P2_MOMENTUM_FACTORS: tuple[str, ...] = tuple(P2_MOMENTUM_HORIZONS)
P2_MOMENTUM_DIRECTION = -1
# 按周期键冻结；与预注册文档中的 JSON 完全一致。
P2_MOMENTUM_HORIZON_WEIGHTS: dict[int, float] = {5: 0.20, 21: 0.60, 60: 0.15, 120: 0.05}
P2_MOMENTUM_WEIGHTS: dict[str, float] = {
    name: P2_MOMENTUM_HORIZON_WEIGHTS[horizon]
    for name, horizon in P2_MOMENTUM_HORIZONS.items()
}
P2_WEIGHTS_CANONICAL = '{"120":0.05,"21":0.6,"5":0.2,"60":0.15}'
P2_WEIGHTS_SHA256 = "67646c0ae425d735fc1b5dfc689d91522a6f394fb5fbf17887ff9625bdf33edb"

# ---------------------------------------------------------------------------
# P3：保留 P1 的 19 因子动量 sleeve，只在 sleeve 内部加 21 日小幅 tilt
# （2026-11-06；预注册文件
#  deliverables/2026-11-06_B1_P3_P1动量sleeve加21日tilt_预注册方案.md）
#
# 关键约束：
# - tilt 只作用于动量 sleeve 内部，族间仍 5 族等权（各 20%）；
# - 主 tilt=0.075，敏感性 0.05/0.10 只报告，不参与升级判定；
# - P3 不覆盖 P0/P1/P2。
# ---------------------------------------------------------------------------
P3_TILT_FACTOR = "b1_mom_21d"
P3_TILT_WEIGHT = 0.075
P3_TILT_CANDIDATES: tuple[float, ...] = (0.05, 0.075, 0.10)
P3_TILT_WEIGHTS_CANONICAL = (
    '{"family_weights":"equal_5_families",'
    '"momentum_sleeve":"p1_19_factors_equal",'
    '"primary_gate":{"ic_min":0.1017,"mdd_min":-0.1002,"sharpe_min":0.424},'
    '"tilt_factor":"b1_mom_21d",'
    '"tilt_scope":"momentum_sleeve_only",'
    '"tilt_weight":0.075}'
)
P3_TILT_WEIGHTS_SHA256 = "82435a0d0c135b819b938cd3165033da7ce3994da55b5fd57cbd62648c83d1b4"


def _non_momentum_names() -> tuple[str, ...]:
    """P1 策略因子中不属于动量族的因子（方向与 P0 相同）。"""
    return tuple(
        name for name in B1_P1_STRATEGY_FACTORS
        if (TUSHARE_FACTOR_MAP.get(name) or {}).get("factor_type") != MOMENTUM_FACTOR_TYPE
    )


B1_P2_STRATEGY_FACTORS: tuple[str, ...] = _non_momentum_names() + P2_MOMENTUM_FACTORS
B1_P2_PRIOR_DIRECTIONS: dict[str, int] = dict(B1_P1_PRIOR_DIRECTIONS)
for _name in P2_MOMENTUM_FACTORS:
    B1_P2_PRIOR_DIRECTIONS[_name] = P2_MOMENTUM_DIRECTION

PRIOR_VERSIONS = ("p0", "p1", "p2", "p3")
PRIOR_LABELS = {
    "p0": "P0 · 资产定价混合先验（研究基线）",
    "p1": "P1 · A 股反转先验（动量族全反向，独立假设）",
    "p2": "P2 · 21 日反转核心（动量 sleeve 5/21/60/120 加权）",
    "p3": "P3 · P1 动量 sleeve + 21 日小幅 tilt",
}


@dataclass(frozen=True)
class PriorVersion:
    version: str
    label: str
    directions: dict[str, int]
    changed_vs_p0: dict[str, dict[str, int]]
    strategy_factors: tuple[str, ...]
    momentum_weights: dict[str, float] | None = None
    tilt_weight: float | None = None
    tilt_factor: str | None = None


def get_prior(version: str = "p0", tilt_weight: float | None = None) -> PriorVersion:
    """返回指定版本的先验；P0 为默认，P1/P2/P3 不覆盖 P0。

    `tilt_weight` 仅对 P3 生效；缺省用预注册主 tilt 0.075，且必须落在
    预注册候选区间 (0.05, 0.075, 0.10) 内。
    """
    if version == "p0":
        return PriorVersion(
            version="p0",
            label=PRIOR_LABELS["p0"],
            directions=dict(B1_PRIOR_DIRECTIONS),
            changed_vs_p0={},
            strategy_factors=B1_P1_STRATEGY_FACTORS,
        )
    if version == "p1":
        return PriorVersion(
            version="p1",
            label=PRIOR_LABELS["p1"],
            directions=dict(B1_P1_PRIOR_DIRECTIONS),
            changed_vs_p0=dict(B1_PRIOR_CHANGED_P1),
            strategy_factors=B1_P1_STRATEGY_FACTORS,
        )
    if version == "p2":
        return PriorVersion(
            version="p2",
            label=PRIOR_LABELS["p2"],
            directions=dict(B1_P2_PRIOR_DIRECTIONS),
            changed_vs_p0=dict(B1_PRIOR_CHANGED_P1),
            strategy_factors=B1_P2_STRATEGY_FACTORS,
            momentum_weights=dict(P2_MOMENTUM_WEIGHTS),
        )
    if version == "p3":
        weight = P3_TILT_WEIGHT if tilt_weight is None else float(tilt_weight)
        if not any(abs(weight - c) < 1e-12 for c in P3_TILT_CANDIDATES):
            raise ValueError(
                f"P3 tilt 权重必须在预注册区间 {P3_TILT_CANDIDATES} 内，收到 {weight!r}"
            )
        return PriorVersion(
            version="p3",
            label=PRIOR_LABELS["p3"],
            directions=dict(B1_P1_PRIOR_DIRECTIONS),
            changed_vs_p0=dict(B1_PRIOR_CHANGED_P1),
            strategy_factors=B1_P1_STRATEGY_FACTORS,
            tilt_weight=weight,
            tilt_factor=P3_TILT_FACTOR,
        )
    raise ValueError(f"未知先验版本: {version!r}；可选 {PRIOR_VERSIONS}")


def p2_momentum_specs() -> dict:
    """P2 的四个周期动量因子 spec；不注册进全局 FACTORS，仅回测时注入。"""
    import factor_lib as fl

    specs: dict = {}
    for name, horizon in P2_MOMENTUM_HORIZONS.items():
        def _compute(panel, _h: int = horizon):
            return panel.optional("adj_close").pct_change(_h)

        specs[name] = fl.FactorSpec(
            name=name,
            family="Tushare-动量",
            kind="continuous",
            direction=P2_MOMENTUM_DIRECTION,
            description=f"P2 动量 sleeve {name}：{horizon} 日反转（adj_close/adj_close.shift({horizon})-1）",
            source="b1_p2_horizon",
            requires=("adj_close",),
            compute=_compute,
        )
    return specs


def p3_tilt_spec() -> object:
    """P3 的 21 日 tilt 因子 spec；不注册进全局 FACTORS，仅回测时注入。"""
    return p2_momentum_specs()[P3_TILT_FACTOR]


def prior_meta(version: str = "p0", tilt_weight: float | None = None) -> dict:
    """给回测 JSON/报告用的版本元数据（不含大矩阵）。"""
    prior = get_prior(version, tilt_weight=tilt_weight)
    return {
        "version": prior.version,
        "label": prior.label,
        "n_directions": len(prior.directions),
        "n_changed_vs_p0": len(prior.changed_vs_p0),
        "changed_vs_p0": prior.changed_vs_p0,
        "unknown": sorted(B1_PRIOR_UNKNOWN),
        "strategy_factors": list(prior.strategy_factors),
        "momentum_weights": prior.momentum_weights,
        "tilt_weight": prior.tilt_weight,
        "tilt_factor": prior.tilt_factor,
        "tilt_candidates": list(P3_TILT_CANDIDATES) if prior.version == "p3" else None,
        "p2_weights_sha256": P2_WEIGHTS_SHA256 if prior.version == "p2" else None,
        "p3_tilt_sha256": P3_TILT_WEIGHTS_SHA256 if prior.version == "p3" else None,
    }


__all__ = [
    "PRIOR_VERSIONS",
    "PRIOR_LABELS",
    "B1_P1_PRIOR_DIRECTIONS",
    "B1_P1_STRATEGY_FACTORS",
    "B1_PRIOR_CHANGED_P1",
    "B1_P2_PRIOR_DIRECTIONS",
    "B1_P2_STRATEGY_FACTORS",
    "P2_MOMENTUM_FACTORS",
    "P2_MOMENTUM_HORIZONS",
    "P2_MOMENTUM_HORIZON_WEIGHTS",
    "P2_MOMENTUM_WEIGHTS",
    "P2_WEIGHTS_CANONICAL",
    "P2_WEIGHTS_SHA256",
    "P3_TILT_FACTOR",
    "P3_TILT_WEIGHT",
    "P3_TILT_CANDIDATES",
    "P3_TILT_WEIGHTS_CANONICAL",
    "P3_TILT_WEIGHTS_SHA256",
    "PriorVersion",
    "get_prior",
    "p2_momentum_specs",
    "p3_tilt_spec",
    "prior_meta",
]
