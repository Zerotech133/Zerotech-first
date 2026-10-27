"""门 N8 reconcile 语义升级调度（休眠默认，不改变现状）。

本模块是「六度选股 R0→R1→R2 生产切流工程」工位 #369 的独占交付物，
与门 N1 universe 圈选登记（`liudu_universe.py`）成对交付。

职责边界（红线）：
- 登记 reconcile 的三模式（A/B/C）并提供调度器 `reconcile()`。
- 默认模式必须返回与现状一致的结果——`status="NOT_VERIFIED"`，绝不输出
  `READY`。这刻意与 `r0_evidence_reconciliation.py` 当前恒
  NOT_VERIFIED/REJECTED、无 READY 出口的行为对齐，避免任何切流误触发。
- 依赖仅 stdlib（typing），不 import pandas/numpy/第三方。
- `RECONCILE_SEMANTICS_ENABLED` 默认 False；语义升级方向须经老田在 N8 门拍板。
- 不触真实数据；`probe` 为调用方/合成提供的可选 dict。

所有激活开关留待老田在 N8 门拍板：A/B/C 选哪个须经老田实质决策；
本模块休眠，不改变现有 NOT_VERIFIED 结论。
"""

from __future__ import annotations

from typing import Dict, Optional


# 三模式常量
MODE_A_PRODUCTION_MULTISOURCE = "A"  # 生产多源融合
MODE_B_R1_SINGLESOURCE = "B"  # R1 单源基线
MODE_C_DUALTRACK = "C"  # 双轨标注

# N8 清单事实常量：R1 单源 32/12 只 vs 生产多源 76/87 只，Jaccard 极低。
JACCARD_R1_VS_PRODUCTION = 0.0437

# 激活开关：已按老田 2026-10-21 路径 A 授权翻为开启（非生产决策，已拍板）。
# 语义方向 = "B"（R1 单源基线）已生效；本模块仍不改变 NOT_VERIFIED 结论、绝不输出 READY。
RECONCILE_SEMANTICS_ENABLED: bool = True

# === N8 决策记录（2026-10-21 老田拍板，见 AskUserQuestion 答复）===
# 选定语义方向 = "B"（R1 单源基线）。理由：Jaccard(R1 vs 生产)=0.0437 极低，
# 直接走 A 生产多源融合差距过大、切流风险高；B 以 R1 单源为基线最稳。
# 安全不变式（翻开关后不变）：reconcile() 仍恒返回 status="NOT_VERIFIED" / ready=False，
# 绝不输出 READY；RECONCILE_SEMANTICS_ENABLED 已据路径 A 翻 True，语义方向 B 生效。
SELECTED_RECONCILE_MODE: str = MODE_B_R1_SINGLESOURCE
N8_DECISION_DATE: str = "2026-10-21"
N8_DECISION_BY: str = "老田"

_VALID_MODES = (
    MODE_A_PRODUCTION_MULTISOURCE,
    MODE_B_R1_SINGLESOURCE,
    MODE_C_DUALTRACK,
)

_NOT_VERIFIED_STATUS = "NOT_VERIFIED"


def reconcile(
    trade_date: str, mode: str, probe: Optional[dict] = None
) -> Dict[str, object]:
    """reconcile 调度器（休眠默认，不改变现状）。

    按 ``mode`` 分发到内部 ``_reconcile_a/_b/_c``，但无论何种模式，默认一律
    返回 ``status="NOT_VERIFIED"``、``ready=False``。这刻意与
    ``r0_evidence_reconciliation.py`` 当前恒 NOT_VERIFIED/REJECTED、无 READY
    出口的行为对齐，避免任何切流误触发。

    ``probe`` 为合成/调用方提供的可选字典；本模块不触真实数据。

    返回 dict 固定包含且不限于：
        trade_date, mode, status, ready, cutover_verdict,
        real_source_verdict, enabled, note。
    """
    if mode not in _VALID_MODES:
        # 未知模式亦不升级为 READY，保持保守。
        return _not_verified(trade_date, mode, reason="未知模式，维持 NOT_VERIFIED")
    if mode == MODE_A_PRODUCTION_MULTISOURCE:
        return _reconcile_a(trade_date, probe)
    if mode == MODE_B_R1_SINGLESOURCE:
        return _reconcile_b(trade_date, probe)
    return _reconcile_c(trade_date, probe)


def _not_verified(trade_date: str, mode: str, reason: str) -> Dict[str, object]:
    return {
        "trade_date": trade_date,
        "mode": mode,
        "status": _NOT_VERIFIED_STATUS,
        "ready": False,
        "cutover_verdict": "BLOCK_CUTOVER",
        "real_source_verdict": "BLOCKED_REAL_DATA",
        "enabled": RECONCILE_SEMANTICS_ENABLED,
        "note": reason,
    }


def _reconcile_a(trade_date: str, probe: Optional[dict]) -> Dict[str, object]:
    # 生产多源融合：登记 Jaccard 事实，但结论维持 NOT_VERIFIED。
    out = _not_verified(
        trade_date,
        MODE_A_PRODUCTION_MULTISOURCE,
        reason="A 模式休眠：生产多源融合语义升级待 N8 门拍板",
    )
    out["jaccard_r1_vs_production"] = JACCARD_R1_VS_PRODUCTION
    out["probe"] = probe
    return out


def _reconcile_b(trade_date: str, probe: Optional[dict]) -> Dict[str, object]:
    # R1 单源基线：结论维持 NOT_VERIFIED。
    out = _not_verified(
        trade_date,
        MODE_B_R1_SINGLESOURCE,
        reason="B 模式休眠：R1 单源基线语义升级待 N8 门拍板",
    )
    out["probe"] = probe
    return out


def _reconcile_c(trade_date: str, probe: Optional[dict]) -> Dict[str, object]:
    # 双轨标注：结论维持 NOT_VERIFIED。
    out = _not_verified(
        trade_date,
        MODE_C_DUALTRACK,
        reason="C 模式休眠：双轨标注语义升级待 N8 门拍板",
    )
    out["probe"] = probe
    return out


def is_reconcile_enabled() -> bool:
    """暴露当前是否允许语义升级（默认 False）。仅为查询用，不改变任何状态。"""
    return RECONCILE_SEMANTICS_ENABLED
