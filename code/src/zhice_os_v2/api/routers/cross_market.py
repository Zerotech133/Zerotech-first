"""跨市场归一 API（V1 cross-market 对齐）。"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from zhice_os_v2.api.dependencies import get_current_user, get_db
from zhice_os_v2.application.services.account_stats_service import AccountStatsService
from zhice_os_v2.domain.market.fx import CURRENCY_BY_MARKET, FX_TO_CNY, get_fx_info
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

router = APIRouter(prefix="/portfolio/cross-market", tags=["cross-market"], dependencies=[Depends(get_current_user)])


@router.get("")
def cross_market(db: PersistencePort = Depends(get_db)):
    """四市场账户汇总 + CNY 归一。复用 AccountStatsService.market_summary(m) 各市场。
    空市场（无持仓）也返回（equity=初始资金）。"""
    service = AccountStatsService(db)
    markets: list[dict] = []
    total_cny = 0.0
    total_pnl_cny = 0.0
    for market, currency in CURRENCY_BY_MARKET.items():
        s = service.market_summary(market)
        fx = FX_TO_CNY[currency]
        equity_cny = s["total_equity"] * fx
        pnl_cny = s["total_pnl"] * fx
        total_cny += equity_cny
        total_pnl_cny += pnl_cny
        markets.append({
            "market": market,
            "currency": currency,
            "total_equity": s["total_equity"],
            "available_cash": s["available_cash"],
            "market_value": s["market_value"],
            "unrealized_pnl": s["unrealized_pnl"],
            "today_pnl": s["today_pnl"],
            "total_pnl": s["total_pnl"],
            "position_count": s["position_count"],
            "account_count": s["account_count"],
            "equity_cny": round(equity_cny, 2),
            "pnl_cny": round(pnl_cny, 2),
        })
    return {
        "markets": markets,
        "total_cny": round(total_cny, 2),
        "total_pnl_cny": round(total_pnl_cny, 2),
        "fx_rates": FX_TO_CNY,
        **get_fx_info(),  # fx_asof / fx_stale 实时读取（refresh_fx() 刷新后立即生效）
    }
