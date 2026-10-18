"""按策略差异化的出场策略（止盈 / 止损 / 浮动止损）。

设计要点：
  - 选股策略只负责"选股"，不产出出场参数（见 _template.py）；出场是"策略类型"级别的配置。
  - 每个 strategy_type 在 EXIT_POLICY_REGISTRY 登记一套出场参数，未登记走 DEFAULT_EXIT_POLICY（= 旧全局 -5%/+10% 行为，向后兼容）。
  - 参数集中在本文件，调参只改这一处；trade_plan_builder 建仓时定初始止损/止盈价，
    盘中引擎按持仓所属策略取浮动止损参数。

注：当前为百分比机械口径（第一版），为缠论"跌破中枢下沿"、趋势"跌破N日线"等动态出场留出结构，
    后续可在 ExitPolicy 增加 dynamic_stop 钩子而不动调用方。
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class ExitPolicy:
    """单个策略类型的出场参数。百分比均为正数小数（0.05 = 5%）。"""

    stop_loss_pct: Decimal          # 初始止损：入场价 ×(1 - stop_loss_pct)
    take_profit_pct: Decimal        # 止盈：入场价 ×(1 + take_profit_pct)
    use_trailing: bool              # 是否启用浮动止损
    trailing_activate_pct: Decimal  # 浮盈达到此值时启动浮动止损
    trailing_trail_pct: Decimal     # 止损跟随：当前价下方此幅度
    max_hold_days: int | None = None    # 时间止损：持有达此交易日数强制平仓；None=不启用
    dynamic_stop: str | None = None     # 动态出场类型（"CHAN_ZS"=跌破日线笔中枢下沿）；None=不启用


# 默认口径 = 历史全局行为（-5% 止损 / +10% 止盈 / +3% 启动浮动止损、跟随 1.5%）。
# 任何未在下方登记 strategy_type 的策略走此兜底，保证向后兼容。
DEFAULT_EXIT_POLICY = ExitPolicy(
    stop_loss_pct=Decimal("0.05"),
    take_profit_pct=Decimal("0.10"),
    use_trailing=True,
    trailing_activate_pct=Decimal("0.03"),
    trailing_trail_pct=Decimal("0.015"),
)


# strategy_type → 出场策略。key 与 STRATEGY_REGISTRY / Strategy.strategy_type 对齐。
EXIT_POLICY_REGISTRY: dict[str, ExitPolicy] = {
    # 趋势动量：截断亏损、让利润奔跑。初始止损偏紧，止盈放宽，靠浮动止损锁利。
    "TREND": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.20"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 均值回归：快进快出、目标小、止损紧，到目标即走，不用浮动止损。
    "MEAN_REVERSION": ExitPolicy(
        stop_loss_pct=Decimal("0.04"),
        take_profit_pct=Decimal("0.06"),
        use_trailing=False,
        trailing_activate_pct=Decimal("0.03"),
        trailing_trail_pct=Decimal("0.015"),
    ),
    # 缠论（原版）：给中枢/背驰演化留空间，机械百分比仅作兜底——止损宽、止盈高、浮动止损跟随宽。
    "CHAN": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.18"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.05"),
        trailing_trail_pct=Decimal("0.035"),
    ),
    # 缠论（规则书 v2）：同缠论思路，略收紧。
    "CHAN_V2": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 缠论 v3 = v2 + 周线大级别方向过滤；出场参数与 v2 完全一致，保证 A/B 仅差在选股端。
    "CHAN_V3": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 缠论 v4 = v2 + 大盘级别方向门；出场参数与 v2 完全一致，A/B 仅差在大盘 regime 空仓。
    "CHAN_V4": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 对照组：大盘门+随机篮子；出场参数与 CHAN_V4 完全一致，唯一差异是选股（缠论 vs 随机）。
    "CTRL_MKT": ExitPolicy(
        stop_loss_pct=Decimal("0.07"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 缠论 v5 = v2 + 30分钟小级别确认；出场参数与 v2 完全一致，A/B 仅差在多级别联立选股端。
    "CHAN_V5": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 缠论 v6 = v2 + 大盘门 + 30分钟确认；出场参数与 v2 一致，与 CTRL_MKT 同门同出场做选股终审。
    "CHAN_V6": ExitPolicy(
        stop_loss_pct=Decimal("0.06"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 缠论 v7 = v1 几何选股 + 三门 + 收紧止损(5%) + 15 交易日时间止损 + 中枢下沿动态出场。
    "CHAN_V7": ExitPolicy(
        stop_loss_pct=Decimal("0.05"),
        take_profit_pct=Decimal("0.18"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.05"),
        trailing_trail_pct=Decimal("0.035"),
        max_hold_days=15,
        dynamic_stop="CHAN_ZS",
    ),
    # 事件信号：业绩预告预增漂移(PEAD)。出场参数与缠论族一致，保证跨策略可比。
    "EARN_SURPRISE": ExitPolicy(
        stop_loss_pct=Decimal("0.07"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 事件信号：大股东增持漂移。出场参数与其他受检信号一致，保证可比。
    "HOLDER_BUY": ExitPolicy(
        stop_loss_pct=Decimal("0.07"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
    # 事件信号：龙虎榜净买入漂移。出场参数与其他受检信号一致，保证可比。
    "TOP_NET_BUY": ExitPolicy(
        stop_loss_pct=Decimal("0.07"),
        take_profit_pct=Decimal("0.16"),
        use_trailing=True,
        trailing_activate_pct=Decimal("0.04"),
        trailing_trail_pct=Decimal("0.03"),
    ),
}


def get_exit_policy(strategy_type: str | None) -> ExitPolicy:
    """按 strategy_type 取出场策略；未登记 / 空 → 默认口径。"""
    if not strategy_type:
        return DEFAULT_EXIT_POLICY
    return EXIT_POLICY_REGISTRY.get(strategy_type, DEFAULT_EXIT_POLICY)


def get_exit_policy_for_version(strategy_version_id: str, db) -> ExitPolicy:
    """按 strategy_version_id 反查 strategy_type，返回对应出场策略；查不到走默认。

    db 为 V2 PersistencePort。
    """
    version = db.get_strategy_version(strategy_version_id)
    if version is None:
        return DEFAULT_EXIT_POLICY
    strategy = db.get_strategy(version.strategy_id)
    return get_exit_policy(strategy.strategy_type if strategy else None)
