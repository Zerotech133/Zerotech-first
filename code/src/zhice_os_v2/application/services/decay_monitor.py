"""策略衰减监控：基于复盘数据识别绩效下滑。"""
from __future__ import annotations

from collections import defaultdict
from typing import cast

from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class DecayMonitor:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def monitor(self, threshold: float = 0.15, market: str | None = None) -> list[dict]:
        """衰减监控。market 给定则只看该市场（None=全市场，存量行为）。"""
        replays = self._db.list_replays()
        if market is not None:
            replays = [r for r in replays if r.market == market]
        by_strategy: dict[str, list] = defaultdict(list)
        for r in replays:
            by_strategy[r.strategy_version_id].append(r)

        results = []
        for strategy_version_id, items in by_strategy.items():
            items.sort(key=lambda x: x.created_at)
            if len(items) < 10:
                continue
            split = len(items) // 2
            older = items[:split]
            recent = items[split:]
            older_rate = sum(1 for x in older if x.pnl > 0) / len(older)
            recent_rate = sum(1 for x in recent if x.pnl > 0) / len(recent)
            decay = older_rate - recent_rate
            results.append(
                {
                    "strategy_version_id": strategy_version_id,
                    "total": len(items),
                    "older_win_rate": round(older_rate, 4),
                    "recent_win_rate": round(recent_rate, 4),
                    "decay": round(decay, 4),
                    "status": "DECAY" if decay >= threshold else "NORMAL",
                }
            )
        results.sort(key=lambda x: cast(float, x["decay"]), reverse=True)
        return results
