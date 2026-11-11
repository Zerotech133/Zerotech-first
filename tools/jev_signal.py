# -*- coding: utf-8 -*-
"""JEV 信号分层（conf 弃用后的替代信号层）。

依据 tools/jev_calibrate.py 后验结论（2026-11-10，报告 jev_calibrate_20261110）：
- 原始 conf AUC = 0.414（反相关，越高越可能错）；单调校准器保序、AUC 不变
  → 不可救，正式弃用。下游不再据 conf 做过滤/排序/配色。
- 替代：事件类型分层 + 方向 sign（组合层弱有效：bull 均值 +0.20% / bear −0.41%）。

事件类型方向命中率来自 jev_posterior_20261110.json 的 by_event_type
（19 交易日 / 28502 样本）：
    credit 64.7% · trade 62.9% · risk 58.1% · policy 54.6%
    equity 53.8% · earnings 53.9% · price 48.8% · supply 49.1%
strong = 命中率显著高于 0.5 的类型（组合层有真实判别边）。
"""
from __future__ import annotations

# 强信号事件类型（方向命中率 > 0.53，组合层有真实判别边）
STRONG_TYPES = frozenset({
    "credit", "trade", "risk", "policy", "equity", "earnings",
})

# 已知事件类型的方向命中率（仅作说明/扩展用，决策只看 STRONG_TYPES 集合）
EVENT_TYPE_ACC = {
    "credit": 0.647, "trade": 0.629, "risk": 0.581, "policy": 0.546,
    "equity": 0.538, "earnings": 0.539, "price": 0.488, "supply": 0.491,
}


def event_type_tier(et) -> str:
    """事件类型 → 信号分层：strong（有真实边）/ weak（无或反向，含 neutral/unknown）。"""
    et = str(et or "").strip().lower()
    return "strong" if et in STRONG_TYPES else "weak"


def jev_signal_score(jev) -> float:
    """组合层弱信号分（仅排序/分层用，不承诺单点精度）。
    tier(强=1.0 / 弱=0.4) × 方向 sign(bull +1 / neutral 0 / bear −1)。
    值域 ≈ [−1.0, +1.0]。"""
    if not isinstance(jev, dict):
        return 0.0
    tier = event_type_tier(jev.get("event_type"))
    tw = 1.0 if tier == "strong" else 0.4
    bias = str(jev.get("bias") or "").strip().lower()
    sign = 1.0 if bias == "bull" else (-1.0 if bias == "bear" else 0.0)
    return round(tw * sign, 4)
