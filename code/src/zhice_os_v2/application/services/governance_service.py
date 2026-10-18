"""策略治理：晋升/降级/自动审查。"""
from __future__ import annotations

from zhice_os_v2.application.services.decay_monitor import DecayMonitor
from zhice_os_v2.domain.strategy.strategy import StrategyStatus, StrategyVersion
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class GovernanceService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def promote(self, strategy_version_id: str) -> StrategyVersion:
        version = self._db.get_strategy_version(strategy_version_id)
        if version is None:
            raise KeyError(strategy_version_id)
        if version.status != StrategyStatus.STAGING:
            raise ValueError("only STAGING strategy can be promoted")
        # 先把同策略（同 strategy_id）其它 ACTIVE 版本降级，保证「每策略至多一个 ACTIVE」，
        # 否则 versions-dashboard 会出现多个 ACTIVE、下游按 ACTIVE 选版本产生歧义。
        for other in self._db.list_strategy_versions():
            if (
                other.strategy_id == version.strategy_id
                and other.strategy_version_id != version.strategy_version_id
                and other.status == StrategyStatus.ACTIVE
            ):
                other.status = StrategyStatus.DISABLED
                self._db.save_strategy_version(other)
        version.status = StrategyStatus.ACTIVE
        self._db.save_strategy_version(version)
        return version

    def demote(self, strategy_version_id: str, reason: str) -> StrategyVersion:
        version = self._db.get_strategy_version(strategy_version_id)
        if version is None:
            raise KeyError(strategy_version_id)
        if version.status != StrategyStatus.ACTIVE:
            raise ValueError("only ACTIVE strategy can be demoted")
        version.status = StrategyStatus.DISABLED
        self._db.save_strategy_version(version)
        return version

    def auto_review(self, market: str | None = None) -> dict:
        """自动审查（衰减建议）。market 给定则只审该市场（None=全市场）。"""
        decay_items = DecayMonitor(self._db).monitor(market=market)
        suggestions = []
        for item in decay_items:
            if item["status"] == "DECAY":
                suggestions.append(
                    {
                        "strategy_version_id": item["strategy_version_id"],
                        "action": "DEMOTE",
                        "reason": f"decay={item['decay']}",
                    }
                )
        return {"checked": len(decay_items), "suggestions": suggestions}
