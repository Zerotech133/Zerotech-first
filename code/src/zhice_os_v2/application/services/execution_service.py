"""执行服务：将授权通过的指令转换为持仓变化。"""
from __future__ import annotations

import logging
from decimal import Decimal

from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus
from zhice_os_v2.domain.ids import fit_id
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)


class ExecutionService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def apply_fill(
        self,
        result: OrderResult,
        strategy_version_id: str,
        market: str,
        ts_code: str,
        side: str = "BUY",
        trade_plan_id: str | None = None,
        user_strategy_id: str | None = None,
        account_id: str | None = None,
    ) -> PositionLot | None:
        # 运行时归属（Phase B enforced 路径）。legacy 调用不传 → None，PositionLot
        # 归属保持 None、行为逐字不变；仅 enforced owned 路径传实例归属。
        # 部分成交（PARTIALLY_FILLED）也须按 filled_qty 入账，否则已成交股数丢失
        if result.status not in (OrderStatus.FILLED, OrderStatus.PARTIALLY_FILLED):
            return None
        if result.filled_qty is None or result.filled_qty <= 0:
            return None

        side_str = str(getattr(side, "value", side)).upper()
        if side_str == "SELL":
            # 卖出=减仓/平仓，而非新建多头（此前无条件建 BUY 仓，卖出反而凭空加多头）
            sell_info = self._apply_sell(result, strategy_version_id, market, ts_code)
            if sell_info["over_sell_qty"] > 0:
                # 超持仓：已成交部分已落库，但有剩余无法平仓；返回 None 通知上层
                logger.warning(
                    "apply_fill 超持仓：ts_code=%s 请求卖出 %s 已平仓 %s 无法平仓 %s，返回 None",
                    ts_code, result.filled_qty, sell_info["closed_qty"], sell_info["over_sell_qty"],
                )
                return None
            return sell_info["position"]

        position = PositionLot(
            position_id=fit_id("pos-", result.order_id),
            strategy_version_id=strategy_version_id,
            market=market,
            ts_code=ts_code,
            direction=side_str or "BUY",
            open_qty=result.filled_qty,
            open_price=result.filled_price or Decimal("0"),
            trade_plan_id=trade_plan_id,
            fee=result.fee,  # 买入成交费（佣金/印花税/过户费），随 lot 留存供平仓分摊
            user_strategy_id=user_strategy_id,
            account_id=account_id,
        )
        self._db.save_position(position)
        return position

    def _apply_sell(
        self,
        result: OrderResult,
        strategy_version_id: str,
        market: str,
        ts_code: str,
    ) -> dict:
        """卖出成交：按 FIFO 消耗同策略同标的的 OPEN 批次，逐笔部分平仓并落 realized。

        返回 {"position": PositionLot | None, "over_sell_qty": Decimal, "closed_qty": Decimal}
        - over_sell_qty > 0 表示超持仓：有剩余无法平仓（已平部分已落库，不回滚）
        - position 为最后一个被操作的持仓 lot（整仓平时为 CLOSED 状态）
        """
        from zhice_os_v2.application.services.position_ops_service import PositionOpsService

        ops = PositionOpsService(self._db)
        total_fill_qty = Decimal(str(result.filled_qty))
        remaining = total_fill_qty
        price = result.filled_price or Decimal("0")
        lots = [
            p for p in self._db.list_open_positions(market)
            if p.strategy_version_id == strategy_version_id and p.ts_code == ts_code
        ]
        lots.sort(key=lambda p: p.opened_at)
        last: PositionLot | None = None
        closed_qty = Decimal("0")
        for lot in lots:
            if remaining <= 0:
                break
            take = min(remaining, lot.open_qty)
            # 卖出手续费按本次成交量占总成交量的比例分配到各 lot
            sell_fee_portion = (result.fee * take / total_fill_qty) if total_fill_qty > 0 else Decimal("0")
            res = ops.partial_close(lot.position_id, take, price, sell_fee=sell_fee_portion)
            if res.get("ok"):
                closed_qty += take
                remaining -= take
                last = self._db.get_position(lot.position_id)

        if remaining > 0:
            logger.warning(
                "超持仓卖出：ts_code=%s 要卖 %s 但持仓不足，已平仓 %s 股，未成交 %s 股",
                ts_code, result.filled_qty, closed_qty, remaining,
            )

        return {"position": last, "over_sell_qty": remaining, "closed_qty": closed_qty}
