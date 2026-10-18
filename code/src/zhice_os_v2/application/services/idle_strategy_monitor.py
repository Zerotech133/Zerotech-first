"""零成交（idle 策略）监控（2026-09-02 遗留#3）。

背景：pre_market 撑爆 sim_max_positions 上限时，CN registry 策略「每日选股 20 却 0 成交」
数天无告警而静默失效。本监控检出「连续选股但零建仓」的策略并发 WARN，作运维哨兵。

核心 find_idle_strategies 为纯函数（易测）；check_and_alert 装配真库数据后调用并发告警。
"""
from __future__ import annotations

from datetime import date

# 哨兵告警标题前缀：自动消解据此识别哪些 OPEN 告警属哨兵产出（区别于止损/风控等其他告警）。
SENTINEL_PREFIXES = ("策略零成交 ", "僵尸策略 ")


def idle_alert_title(item: dict) -> str:
    """idle 告警标题（消解/去重键）。与 alert_idle_strategies 同一出口，防口径漂移。"""
    return f"策略零成交 {item['sv']}"


def zombie_alert_title(z: dict) -> str:
    """僵尸告警标题（消解/去重键）。name = strategy_name or strategy_id。"""
    return f"僵尸策略 {z.get('strategy_name') or z['strategy_id']}"


def select_stale_titles(open_titles: list[str], active_titles: set[str]) -> list[str]:
    """在属哨兵前缀的未闭合告警标题中，挑出本轮已不在 active 集者（条件已解除→应自动消解）。

    纯函数：非哨兵前缀的告警（止损/风控等）一律不动；仍在 active 集的哨兵告警保留 OPEN。
    """
    return [t for t in open_titles
            if t.startswith(SENTINEL_PREFIXES) and t not in active_titles]


def find_idle_strategies(
    pool_dates_by_sv: dict[str, list[date]],
    open_dates_by_sv: dict[str, list[date]],
    lookback: int = 3,
) -> list[dict]:
    """检出 idle 策略：某 sv 最近 lookback 个「候选池日」在选股，却 0 建仓于这些日。

    - 池日不足 lookback（历史太短）→ 跳过，避免新策略误报。
    - 判据只看「该 sv 自己的最近 lookback 个池日」窗口内是否有建仓；窗外建仓不算。
    返回 [{sv, pool_days, opens_in_window}]，按 sv 名排序。
    """
    idle: list[dict] = []
    for sv in sorted(pool_dates_by_sv):
        recent = sorted(set(pool_dates_by_sv[sv]))[-lookback:]
        if len(recent) < lookback:
            continue
        opens = set(open_dates_by_sv.get(sv, []))
        hit = opens & set(recent)
        if not hit:
            idle.append({"sv": sv, "pool_days": [d.isoformat() for d in recent],
                         "opens_in_window": 0})
    return idle


def alert_idle_strategies(idle: list[dict], lookback: int = 3) -> None:
    """对检出的 idle 策略逐个发 WARN 告警（数据装配在调度层完成，本函数纯应用层）。"""
    from zhice_os_v2.application.services.alert_service import send_alert
    for item in idle:
        send_alert(
            "WARN_HIGH",
            idle_alert_title(item),
            f"最近 {lookback} 个选股日（{', '.join(item['pool_days'])}）持续出候选池"
            f"但 0 建仓，疑似被阻塞（如上限/风控/入场条件），请核查。",
            dedup_open=True,  # 条件持续成立时不逐日累加同题 OPEN
        )


def find_zombie_strategies(
    active_strategies: list[dict],
    strategy_ids_with_active_version: set[str],
) -> list[dict]:
    """检出「僵尸策略」：策略级 status=ACTIVE，却无任何 status=ACTIVE 的版本。

    此类策略在 registry 盘前链（按策略级 status 选 type）被算作有效，但在 spec 信号链
    （按版本级 status 过滤）永远 strategies=0 → 静默不产候选/信号（如 US VIBE sv-3 STAGING）。
    本检查补齐两条链状态门控口径不一致造成的盲区。

    active_strategies: [{"strategy_id","strategy_name","market"}...]（库中 status=ACTIVE 的策略）
    strategy_ids_with_active_version: 拥有至少一个 ACTIVE 版本的 strategy_id 集合
    返回：僵尸策略列表（按 strategy_id 排序）
    """
    zombies = [
        s for s in active_strategies
        if s["strategy_id"] not in strategy_ids_with_active_version
    ]
    return sorted(zombies, key=lambda s: s["strategy_id"])


def alert_zombie_strategies(zombies: list[dict]) -> None:
    """对僵尸策略逐个发 WARN 告警（数据装配在调度层完成，本函数纯应用层）。"""
    from zhice_os_v2.application.services.alert_service import send_alert
    for z in zombies:
        send_alert(
            "WARN_HIGH",
            zombie_alert_title(z),
            f"策略 {z['strategy_id']}（{z.get('market', '?')}）状态 ACTIVE 但无任何 "
            f"ACTIVE 版本，spec 信号链会静默跳过（strategies=0），不产候选/信号。"
            f"请激活其版本或下线该策略。",
            dedup_open=True,  # 条件持续成立时不逐日累加同题 OPEN
        )
