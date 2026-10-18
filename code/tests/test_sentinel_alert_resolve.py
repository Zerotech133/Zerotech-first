"""哨兵告警标题口径 + 自动消解选择器（select_stale_titles）回归锁。

select_stale_titles 是消解逻辑的核心：决定哪些 OPEN 告警在条件解除后被置 RESOLVED。
必须只碰哨兵前缀告警、且只消解本轮不在 active 集者，绝不误伤止损/风控等其他告警。
标题函数是消解/去重的键，须与 alert_* 出口口径一致。
"""
from __future__ import annotations

from zhice_os_v2.application.services.idle_strategy_monitor import (
    SENTINEL_PREFIXES,
    idle_alert_title,
    select_stale_titles,
    zombie_alert_title,
)


def test_idle_alert_title():
    assert idle_alert_title({"sv": "v1-crypto-sv-1"}) == "策略零成交 v1-crypto-sv-1"


def test_zombie_alert_title_prefers_name_falls_back_to_id():
    assert (
        zombie_alert_title({"strategy_id": "s1", "strategy_name": "[V1] 港股vibeRSI反转"})
        == "僵尸策略 [V1] 港股vibeRSI反转"
    )
    assert zombie_alert_title({"strategy_id": "v1-hk-strat-8"}) == "僵尸策略 v1-hk-strat-8"


def test_titles_carry_sentinel_prefixes():
    # 标题函数产出必须落在 SENTINEL_PREFIXES 内，否则消解识别不到
    assert idle_alert_title({"sv": "x"}).startswith(SENTINEL_PREFIXES)
    assert zombie_alert_title({"strategy_id": "y"}).startswith(SENTINEL_PREFIXES)


def test_select_stale_only_sentinel_and_not_active():
    """非哨兵告警不动；仍在 active 集的哨兵告警保留；条件解除的哨兵告警被选中消解。"""
    opens = [
        "策略零成交 v1-crypto-sv-1",  # 哨兵，且不在 active → stale
        "僵尸策略 X",                  # 哨兵，仍在 active → 保留
        "止损触发 AAPL",               # 非哨兵 → 绝不碰
        "风控拦截 plan-1",             # 非哨兵 → 绝不碰
    ]
    active = {"僵尸策略 X"}
    assert select_stale_titles(opens, active) == ["策略零成交 v1-crypto-sv-1"]


def test_select_stale_empty_when_all_active():
    opens = ["策略零成交 A", "僵尸策略 B"]
    active = {"策略零成交 A", "僵尸策略 B"}
    assert select_stale_titles(opens, active) == []


def test_select_stale_ignores_non_sentinel_even_if_not_active():
    assert select_stale_titles(["止损触发 AAPL", "组合暴露超限"], set()) == []
