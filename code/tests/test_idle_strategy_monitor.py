"""零成交（idle 策略）监控（2026-09-02 遗留#3）。

某策略连续 N 个候选池日在选股，却在这些日 0 建仓 → 可能被阻塞（如本次 pre_market 撑爆
上限致 CN registry 数天零成交）。find_idle_strategies 纯函数检出，日次任务发 WARN 告警。
"""
from __future__ import annotations

from datetime import date

from zhice_os_v2.application.services.idle_strategy_monitor import (
    find_idle_strategies,
    find_zombie_strategies,
)

D = date


def test_idle_detected_pools_but_no_opens():
    """连续 3 个池日选股、窗内 0 建仓 → idle。"""
    pools = {"sv-A": [D(2026, 8, 31), D(2026, 9, 1), D(2026, 9, 2)]}
    opens = {}  # 无任何建仓
    res = find_idle_strategies(pools, opens, lookback=3)
    assert [r["sv"] for r in res] == ["sv-A"]


def test_active_strategy_not_idle():
    """池日有建仓 → 不告警。"""
    pools = {"sv-B": [D(2026, 8, 31), D(2026, 9, 1), D(2026, 9, 2)]}
    opens = {"sv-B": [D(2026, 9, 1)]}  # 窗内有建仓
    res = find_idle_strategies(pools, opens, lookback=3)
    assert res == []


def test_insufficient_history_skipped():
    """池日不足 lookback（历史太短）→ 不判定（避免新策略误报）。"""
    pools = {"sv-C": [D(2026, 9, 1), D(2026, 9, 2)]}  # 只 2 天 < 3
    opens = {}
    res = find_idle_strategies(pools, opens, lookback=3)
    assert res == []


def test_opens_outside_window_still_idle():
    """建仓在窗口之外（更早）、最近 3 池日 0 建仓 → 仍 idle。"""
    pools = {"sv-D": [D(2026, 8, 20), D(2026, 8, 31), D(2026, 9, 1), D(2026, 9, 2)]}
    opens = {"sv-D": [D(2026, 8, 20)]}  # 只在窗外那天建过
    res = find_idle_strategies(pools, opens, lookback=3)
    assert [r["sv"] for r in res] == ["sv-D"]


def test_multiple_strategies_mixed():
    """多策略混合：只挑出真 idle 的。"""
    pools = {
        "sv-idle": [D(2026, 8, 31), D(2026, 9, 1), D(2026, 9, 2)],
        "sv-ok": [D(2026, 8, 31), D(2026, 9, 1), D(2026, 9, 2)],
    }
    opens = {"sv-ok": [D(2026, 9, 2)]}
    res = find_idle_strategies(pools, opens, lookback=3)
    assert [r["sv"] for r in res] == ["sv-idle"]


# ---- 僵尸策略（ACTIVE 策略却无 ACTIVE 版本）----

def test_zombie_active_strategy_without_active_version():
    """策略 ACTIVE 但无任何 ACTIVE 版本 → 僵尸（如 US VIBE sv-3 STAGING）。"""
    active = [{"strategy_id": "s-vibe", "strategy_name": "VIBE", "market": "US"}]
    res = find_zombie_strategies(active, strategy_ids_with_active_version=set())
    assert [z["strategy_id"] for z in res] == ["s-vibe"]


def test_not_zombie_when_has_active_version():
    """策略有至少一个 ACTIVE 版本 → 非僵尸。"""
    active = [{"strategy_id": "s-trend", "strategy_name": "TREND", "market": "US"}]
    res = find_zombie_strategies(active, strategy_ids_with_active_version={"s-trend"})
    assert res == []


def test_zombie_mixed_and_sorted():
    """多策略混合：只挑出无 ACTIVE 版本者，按 strategy_id 排序。"""
    active = [
        {"strategy_id": "s2", "strategy_name": "b", "market": "US"},
        {"strategy_id": "s1", "strategy_name": "a", "market": "CRYPTO"},
    ]
    res = find_zombie_strategies(active, strategy_ids_with_active_version={"s2"})
    assert [z["strategy_id"] for z in res] == ["s1"]
