"""日报服务：汇总复盘、衰减、治理为人类可读报告。"""
from __future__ import annotations

from zhice_os_v2.application.services.decay_monitor import DecayMonitor
from zhice_os_v2.application.services.governance_service import GovernanceService
from zhice_os_v2.application.services.report_service import ReportService
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class ReportingService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def daily_report(self, market: str | None = None) -> dict:
        replay = ReportService(self._db).replay_report(market=market)
        decay = DecayMonitor(self._db).monitor()
        governance = GovernanceService(self._db).auto_review()
        # replay_report 单市场返回 total_pnl（本位币）、ALL 返回 total_pnl_cny（折算），
        # 二者互斥 → 兼容取值，避免不带 market 时 KeyError。
        if "total_pnl" in replay:
            pnl_line = f"总盈亏: {replay['total_pnl']}"
        else:
            pnl_line = f"总盈亏(CNY): {replay['total_pnl_cny']}"
        lines = [
            f"市场: {market or 'ALL'}",
            f"复盘总数: {replay['total']}",
            f"胜率: {replay['win_rate']:.2%}",
            pnl_line,
        ]
        if decay:
            lines.append("衰减策略: " + ", ".join(d["strategy_version_id"] for d in decay if d["status"] == "DECAY"))
        if governance["suggestions"]:
            lines.append("治理建议: " + str(len(governance["suggestions"])) + " 条")
        return {
            "title": "zhice-OS V2 每日报告",
            "content": "\n".join(lines),
            "replay": replay,
            "decay": decay,
            "governance": governance,
        }
