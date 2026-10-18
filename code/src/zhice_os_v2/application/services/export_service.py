"""数据导出服务。"""
from __future__ import annotations

import csv
import io

from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class ExportService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def replays_csv(self) -> str:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(["replay_id", "strategy_version_id", "market", "ts_code", "pnl", "exit_reason"])
        for r in self._db.list_replays():
            writer.writerow([r.replay_id, r.strategy_version_id, r.market, r.ts_code, r.pnl, r.exit_reason])
        return buffer.getvalue()
