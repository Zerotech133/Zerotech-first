"""组合总览服务。"""
from __future__ import annotations

from collections import defaultdict

from zhice_os_v2.domain.market.fx import get_fx_info, to_cny
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class PortfolioService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def summary(self) -> dict:
        """组合总览：跨市场金额一律先折算 CNY 再合计（禁止 USD/HKD/USDT 与 CNY 直接相加）。"""
        positions = self._db.list_open_positions()
        replays = self._db.list_replays()

        # 分市场累计本位币金额，再各自折算 CNY
        cost_by_market: dict[str, float] = defaultdict(float)
        pnl_by_market: dict[str, float] = defaultdict(float)
        for p in positions:
            cost_by_market[p.market] += float(p.open_price) * float(p.open_qty)
        for r in replays:
            pnl_by_market[r.market] += float(r.pnl)

        total_cost_cny = sum(to_cny(v, m) for m, v in cost_by_market.items())
        total_pnl_cny = sum(to_cny(v, m) for m, v in pnl_by_market.items())
        markets = sorted(set(cost_by_market) | set(pnl_by_market))
        return {
            "position_count": len(positions),
            "total_cost_cny": round(total_cost_cny, 2),
            "total_replay_pnl_cny": round(total_pnl_cny, 2),
            "replay_count": len(replays),
            "markets": markets,
            "by_market": [
                {
                    "market": m,
                    "cost": round(cost_by_market.get(m, 0.0), 2),
                    "replay_pnl": round(pnl_by_market.get(m, 0.0), 2),
                }
                for m in markets
            ],
            **get_fx_info(),  # fx_asof / fx_stale 实时读取
        }
