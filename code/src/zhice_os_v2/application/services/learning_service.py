"""学习服务：第一阶段基于规则生成学习案例。"""
from __future__ import annotations

from zhice_os_v2.domain.learning.learning import LearningCase
from zhice_os_v2.domain.replay.replay import ReplayCase
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class LearningService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def record_from_replay(self, replay: ReplayCase) -> LearningCase:
        insight = "WIN" if replay.pnl > 0 else "LOSS"
        lesson = (
            f"{replay.exit_reason} {insight}: {replay.ts_code} pnl={replay.pnl} "
            f"pnl_pct={replay.pnl_pct}"
        )
        # learning_id 列限 varchar(64)：'learn-'+replay_id 常 超 64（id 长串窝案的
        # 同款病），超限 md5 尾缀压缩（同 _record_fill._fit 算法）。
        lid = f"learn-{replay.replay_id}"
        if len(lid) > 64:
            import hashlib
            h = hashlib.md5(lid.encode()).hexdigest()[:10]
            lid = f"learn-{replay.replay_id[:52]}-{h}"[:64]
        learning = LearningCase(
            learning_id=lid,
            strategy_version_id=replay.strategy_version_id,
            market=replay.market,
            replay_id=replay.replay_id,
            insight=insight,
            ts_code=replay.ts_code,
            pnl_pct=replay.pnl_pct,
            lesson_summary=lesson,
        )
        self._db.save_learning(learning)
        return learning
