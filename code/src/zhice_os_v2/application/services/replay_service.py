"""复盘服务。"""
from __future__ import annotations

from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.domain.replay.replay import ReplayCase, ReviewStatus
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class ReplayService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def build_replay_case(self, position: PositionLot) -> ReplayCase | None:
        if position.status != PositionStatus.CLOSED or position.close_price is None:
            return None
        pnl = (position.close_price - position.open_price) * position.open_qty
        pnl_pct = (position.close_price - position.open_price) / position.open_price
        holding_seconds = 0
        if position.closed_at and position.opened_at:
            holding_seconds = int((position.closed_at - position.opened_at).total_seconds())
        # replay_id 列限 varchar(64)：signal 引擎 position_id 带 sv+时间桶前缀，
        # 'replay-'+pid 常超 64 → insert 报 value too long（与 fill_id 同款病）。
        # 超限用 md5 尾缀压缩（同 workflow_service._record_fill._fit 算法）。
        rid = f"replay-{position.position_id}"
        if len(rid) > 64:
            import hashlib
            h = hashlib.md5(rid.encode()).hexdigest()[:10]
            rid = f"replay-{position.position_id[:52]}-{h}"[:64]
        replay = ReplayCase(
            replay_id=rid,
            strategy_version_id=position.strategy_version_id,
            market=position.market,
            ts_code=position.ts_code,
            pnl=pnl,
            pnl_pct=pnl_pct,
            holding_period_seconds=holding_seconds,
            exit_reason="EXIT",
            status=ReviewStatus.PENDING,
            open_date=position.opened_at.date() if position.opened_at else None,
            close_date=position.closed_at.date() if position.closed_at else None,
            qty=position.open_qty,
            open_price=position.open_price,
            close_price=position.close_price,
            outcome="WIN" if pnl > 0 else ("LOSS" if pnl < 0 else "BREAKEVEN"),
            # 复盘继承持仓归属（Phase B）：legacy 持仓归属为 None → replay 亦 None，行为不变；
            # enforced owned 持仓 → replay 带同一实例/账户归属，owner-scoped 复盘视图可用。
            user_strategy_id=position.user_strategy_id,
            account_id=position.account_id,
        )
        self._db.save_replay(replay)
        return replay
