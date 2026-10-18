"""复盘报告服务：从 ReplayCase 生成结构化报告。"""
from __future__ import annotations

from zhice_os_v2.domain.market.fx import get_fx_info, to_cny
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class ReportService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def replay_report(self, market: str | None = None) -> dict:
        replays = self._db.list_replays()
        if market:
            replays = [r for r in replays if r.market == market]
        # 盈/亏/平统一口径：>0 胜、<0 负、==0 平（平不计入胜负，与 reports.py 一致）
        wins = [r for r in replays if r.pnl > 0]
        losses = [r for r in replays if r.pnl < 0]
        breakeven = [r for r in replays if r.pnl == 0]

        # total_pnl：指定市场用本位币；ALL（跨市场）折算 CNY 再合计，禁止直接混币求和
        if market:
            total_pnl_field = {"total_pnl": round(float(sum(r.pnl for r in replays)), 2)}
        else:
            total_cny = sum(to_cny(float(r.pnl), r.market) for r in replays)
            total_pnl_field = {"total_pnl_cny": round(float(total_cny), 2),
                               "fx_stale": get_fx_info()["fx_stale"]}
        return {
            "market": market or "ALL",
            "total": len(replays),
            "wins": len(wins),
            "losses": len(losses),
            "breakeven": len(breakeven),
            "win_rate": round(len(wins) / len(replays), 4) if replays else 0.0,
            **total_pnl_field,
            "replays": [
                {
                    "replay_id": r.replay_id,
                    "market": r.market,
                    "ts_code": r.ts_code,
                    "pnl": round(float(r.pnl), 2),
                    "exit_reason": r.exit_reason,
                }
                for r in replays
            ],
        }
