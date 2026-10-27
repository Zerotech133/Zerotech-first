# -*- coding: utf-8 -*-
"""Regime 阵容选择器（M4-P3）：StrategyAgent 的 Regime→profile 落账层。

设计（docs/audit/2026-10-17_M4规划-投研与模拟执行线.md P3 刀）：
  - 影子决策快照增补 dk_* 纯增量字段（旧行零破坏，catch-up 判例）；
  - Regime→profile 选择依据随行落账（可回放）；
  - blocked_regimes 命中显式 blocked=true + reason；
  - fail-open：双刀/Regime 任何异常不影响影子主链（无人干预产线，
    dk_error 留痕即回）。

口径：
  - 信号来自 engine/quant_double_knife.py（strict/relaxed 双阵容）；
  - REC_SCORE（影子打分）与 dk_signal 是两套独立口径——本层只桥接
    记录不互相改写（score 桥接判例：并存留痕，不做强约束）。
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Regime→profile 阵容表：trend_down 全阵容拦截（与 PROFILE_CONFIG 的
# blocked_regimes 同向双保险）；rotation 波动市用 relaxed 放宽确认；
# 其余默认 strict 稳健阵容。
REGIME_ROSTER: Dict[str, Dict] = {
    "trend_up": {"profile": "strict", "blocked": False},
    "range": {"profile": "strict", "blocked": False},
    "rotation": {"profile": "relaxed", "blocked": False},
    "trend_down": {
        "profile": "strict",
        "blocked": True,
        "reason": "trend_down 双阵容拦截（blocked_regimes）",
    },
}
UNKNOWN_REGIME = {
    "profile": "strict",
    "blocked": False,
    "reason": "未知 Regime 回落 strict（保守默认）",
}


def select_profile(regime_label: Optional[str]) -> Dict:
    """Regime→阵容选择（纯函数，落账依据）。"""
    if not regime_label:
        return dict(UNKNOWN_REGIME)
    entry = REGIME_ROSTER.get(str(regime_label))
    if entry is None:
        return dict(UNKNOWN_REGIME, reason="未知 Regime %r 回落 strict" % regime_label)
    return dict(entry)


def dk_snapshot(daily_df, profile: Optional[str] = None) -> Optional[Dict]:
    """对日 K DataFrame 产出双刀快照（最后一根 bar）。

    返回 {regime_label, regime_tsi, regime_volume_balance, regime_sentiment,
    profile, blocked, dk_signal, reason}；daily_df 为空/异常时 fail-open
    返回 {"dk_error": ...}（调用方据此填 dk_error 字段）。
    profile 显式传入时跳过阵容选择（人工指定阵容的实验路径，选择依据
    = caller override 留痕）。

    M6 拍板 B（2026-10-18）：dk_signal 是「低价带标的专用信号」——双刀
    价带 [6,200] 对高价股恒 False 属设计语义（非缺陷），巡检/告警侧不得
    将其视为异常。
    """
    try:
        from quant_double_knife import _compute_regime, quant_double_knife

        if daily_df is None or len(daily_df) == 0:
            return {"dk_error": "日 K 为空"}
        required = {"open", "high", "low", "close", "amount"}
        missing = required.difference(daily_df.columns)
        if missing:
            return {"dk_error": "日 K 缺列: %s" % sorted(missing)}

        regime_row = _compute_regime(
            daily_df["close"], daily_df["high"], daily_df["low"], daily_df["amount"]
        ).iloc[-1]
        regime_label = str(regime_row["regime_label"])
        roster = select_profile(regime_label)
        used_profile = profile or roster["profile"]
        blocked = bool(roster.get("blocked"))
        if profile is not None and roster.get("blocked"):
            reason = "%s（caller override=%s，仍拦截）" % (
                roster.get("reason", ""),
                profile,
            )
        elif profile is not None:
            reason = "caller override=%s" % profile
        else:
            reason = roster.get("reason", "")

        signal = False
        if not blocked:
            out = quant_double_knife(daily_df, profile=used_profile)
            signal = bool(out["double_knife_signal"].iloc[-1])
        return {
            "regime_label": regime_label,
            "regime_tsi": round(float(regime_row["regime_tsi"]), 4),
            "regime_volume_balance": round(
                float(regime_row["regime_volume_balance"]), 4
            ),
            "regime_sentiment": round(float(regime_row["regime_sentiment"]), 4),
            "profile": used_profile,
            "blocked": blocked,
            "dk_signal": signal,
            "reason": reason,
        }
    except Exception as exc:  # noqa: BLE001  fail-open：影子主链优先
        return {"dk_error": "%s: %s" % (type(exc).__name__, exc)}


def bars_to_df(bars) -> "object":
    """kline_daily 行 → 双刀所需 DataFrame（open/high/low/close/amount）。

    行结构 [date, open, close, high, low, vol, ...]；amount 无显式字段，
    用 vol×close 近似（_compute_regime 的 volume_balance 是 signed_flow
    之和/amount 之和，量纲同缩放，方向性不受影响）。转换失败抛异常，
    由 dk_snapshot 外层 fail-open 统一兜底。
    """
    import pandas as pd

    rows = []
    for b in bars:
        date, o, c, h, l, v = b[0], b[1], b[2], b[3], b[4], b[5] if len(b) > 5 else 0
        rows.append((date, float(o), float(h), float(l), float(c), float(v) * float(c)))
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "amount"])
    return df.set_index("date")


DK_FIELDS = (
    "dk_signal",
    "dk_regime",
    "dk_profile",
    "dk_blocked",
    "dk_reason",
    "dk_error",
)


def enrich_row(row: Dict, snapshot: Optional[Dict]) -> Dict:
    """影子决策行增补 dk_* 增量字段（纯函数，旧行零破坏）。

    snapshot=None 或含 dk_error 时，信号字段置 None、错误留 dk_error
    ——旧行兼容判例：缺列消费方（eval_shadow 等）读不到即当无。
    """
    if not snapshot:
        row.update({f: None for f in DK_FIELDS})
        return row
    if snapshot.get("dk_error"):
        row.update({f: None for f in DK_FIELDS if f != "dk_error"})
        row["dk_error"] = str(snapshot["dk_error"])[:120]
        return row
    row.update(
        {
            "dk_signal": bool(snapshot["dk_signal"]),
            "dk_regime": snapshot["regime_label"],
            "dk_profile": snapshot["profile"],
            "dk_blocked": bool(snapshot["blocked"]),
            "dk_reason": (snapshot.get("reason") or "")[:120] or None,
            "dk_error": None,
        }
    )
    return row
