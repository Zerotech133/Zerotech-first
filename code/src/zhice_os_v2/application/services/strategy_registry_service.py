"""策略注册表服务：对接 V1 移植策略，并叠加真实基本面护栏。"""
from __future__ import annotations

from datetime import date
from functools import partial

from zhice_os_v2.domain.strategy.fundamental_gate import (
    FundamentalGateConfig,
    filter_picks_pure,
)
from zhice_os_v2.domain.strategy.strategies import (
    chan,
    control_basket,
    event_strategies,
    mean_reversion,
    trend_momentum,
)
from zhice_os_v2.infrastructure.fundamentals.provider import load_fundamentals_asof

# 基本面过滤前向 selector 多要的倍数：为过滤后回填留足头部候选，
# 同时把 fundamentals 加载量约束在 factor×max_candidates（避免全宇宙拉取）。
_FUNDAMENTAL_BACKFILL_FACTOR = 5

STRATEGY_REGISTRY = {
    "TREND": trend_momentum.select_candidates,
    "MEAN_REVERSION": mean_reversion.select_candidates,
    "CHAN": chan.strategy.select_candidates,
    "CHAN_V2": chan.strategy_v2.select_candidates,
    # V1 缠论迭代系（strategy_v2 开关组合，出场参数一致保证 A/B 只差选股端）
    "CHAN_V3": partial(chan.strategy_v2.select_candidates, weekly_filter=True),
    "CHAN_V4": partial(chan.strategy_v2.select_candidates, market_gate=True),
    "CHAN_V5": partial(chan.strategy_v2.select_candidates, intraday_confirm=True),
    "CHAN_V6": partial(chan.strategy_v2.select_candidates, market_gate=True, intraday_confirm=True),
    # V7 = v1 几何选股 + 双门（周线+30分钟）。大盘 regime 门因 26 周慢 EMA 严重滞后，
    # 在指数反弹初期(未站上慢EMA)会一票否决全部候选、长期空仓，故去掉；系统性风险靠周线门兜。
    "CHAN_V7": partial(chan.strategy.select_candidates,
                       weekly_filter=True, intraday_confirm=True),
    # 对照组：大盘门+随机篮子（隔离选股价值）
    "CTRL_MKT": control_basket.select_candidates,
    # 事件信号三策略（V1 参数 1:1）
    "EARN_SURPRISE": event_strategies.select_earnings_surprise,
    "HOLDER_BUY": event_strategies.select_holder_buy,
    "TOP_NET_BUY": event_strategies.select_top_net_buy,
}


class StrategyRegistryService:
    def run(
        self,
        strategy_type: str,
        trade_date: date,
        max_candidates: int = 20,
        market: str = "CN",
    ) -> list[dict]:
        selector = STRATEGY_REGISTRY.get(strategy_type)
        if selector is None:
            raise KeyError(strategy_type)
        # 先过滤后截断：selector 内部按 max_candidates 硬截断，若在过滤前截断，
        # 排名 max_candidates+1 起的候选即便能过基本面也无从回填。故向 selector
        # 多要一批（放大配额），基本面过滤后再截到 max_candidates，让优质候选回填。
        oversample = max_candidates * _FUNDAMENTAL_BACKFILL_FACTOR
        picks = selector(trade_date, max_candidates=oversample, market=market)  # type: ignore[operator]
        codes = [p.get("ts_code") for p in picks if p.get("ts_code")]
        funds = load_fundamentals_asof(codes, trade_date)
        kept, _ = filter_picks_pure(picks, funds, {}, {}, FundamentalGateConfig())
        return kept[:max_candidates]

    def list_types(self) -> list[str]:
        return list(STRATEGY_REGISTRY)
