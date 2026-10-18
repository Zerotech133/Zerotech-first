"""知识/复盘检索服务。"""
from __future__ import annotations

from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class KnowledgeService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def search_replays(self, q: str = "", market: str | None = None, limit: int = 20) -> list[dict]:
        replays = self._db.list_replays()
        if market:
            replays = [r for r in replays if r.market == market]
        if q:
            replays = [r for r in replays if q in r.ts_code or q in r.strategy_version_id]
        return [
            {
                "replay_id": r.replay_id,
                "strategy_version_id": r.strategy_version_id,
                "market": r.market,
                "ts_code": r.ts_code,
                "pnl": str(r.pnl),
                "exit_reason": r.exit_reason,
            }
            for r in replays[:limit]
        ]

    def explain_replay(self, replay_id: str) -> dict:
        for r in self._db.list_replays():
            if r.replay_id == replay_id:
                return {
                    "replay_id": r.replay_id,
                    "ts_code": r.ts_code,
                    "explanation": f"该笔交易由策略 {r.strategy_version_id} 产生，盈亏 {r.pnl}，出场原因 {r.exit_reason}。",
                }
        raise KeyError(replay_id)
