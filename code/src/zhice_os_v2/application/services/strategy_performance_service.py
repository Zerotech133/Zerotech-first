"""策略绩效统计服务。"""
from __future__ import annotations

from collections import defaultdict
from typing import cast

from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class StrategyPerformanceService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def performance(self) -> list[dict]:
        groups: dict[str, list] = defaultdict(list)
        for r in self._db.list_replays():
            groups[r.strategy_version_id].append(r)

        results = []
        for sv_id, items in groups.items():
            wins = sum(1 for r in items if r.pnl > 0)
            total_pnl = sum(r.pnl for r in items)
            results.append(
                {
                    "strategy_version_id": sv_id,
                    "trade_count": len(items),
                    "win_rate": round(wins / len(items), 4) if items else 0,
                    "total_pnl": round(float(total_pnl), 2),
                    "avg_pnl": round(float(total_pnl) / len(items), 2) if items else 0.0,
                }
            )
        # 按数值降序（此前 total_pnl 存成 str 会退化成字典序，负号亏损排序错乱）
        results.sort(key=lambda x: cast(float, x["total_pnl"]), reverse=True)
        return results
