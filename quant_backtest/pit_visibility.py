# -*- coding: utf-8 -*-
"""PIT 可见性公共件（WP-PIT-v1 范围 · 公共件）.

可见性契约（单一事实源，与 P1 cells.yaml 同口径）:
  任何事件行必须携带事件日(event_date) + 可见滞后(lag_days);
  可见时刻 = event_date 后第 lag_days 个交易日;
  as-of 研究只允许用 可见时刻 <= 研究基准日 的事件, 且研究窗口
  以可见时刻为 T0 —— 杜绝"事后回填事件被当作当时可见"的 look-ahead。

zerobound-kg 事件图补 as-of 契约后, R3 侧复用同一口径(范围 A)。
"""
from __future__ import annotations

from typing import Dict, List, Optional

# event_date_kind → 默认 lag_days(交易日); publish=收盘后/次日公布 → T0=次日
DEFAULT_LAG = {"publish": 1, "record": 0, "trade": 0}


def resolve_lag_days(event_date_kind: Optional[str],
                     lag_days: Optional[int]) -> int:
    """cells.yaml 元数据 → 生效 lag_days. 缺省按 publish 保守取 1."""
    if lag_days is not None and lag_days >= 0:
        return int(lag_days)
    return DEFAULT_LAG.get(event_date_kind or "publish", 1)


def visibility_proof(n_total: int, n_visible: int,
                     n_estimated: int = 0,
                     t0: Optional[str] = None,
                     kind: str = "publish", lag_days: int = 1) -> Dict:
    """PIT 覆盖率证明元数据(随研究结果一并输出, 不合格率>0 必须如实展示)."""
    return {
        "t0": t0, "event_date_kind": kind, "lag_days": lag_days,
        "n_total": int(n_total), "n_visible": int(n_visible),
        "n_estimated_lag": int(n_estimated),
        "pit_coverage": round(n_visible / n_total, 4) if n_total else None,
        "contract": "available_after<=t0; T0=event_date+lag_days_trading",
    }


def trading_days_after(dates_sorted: List[str], d: str, n: int) -> Optional[str]:
    """有序交易日列表(YYYYMMDD str)中, 严格晚于 d 的第 n 个交易日."""
    later = [x for x in dates_sorted if x > str(d)]
    if len(later) < n or n < 1:
        return None
    return later[n - 1]
