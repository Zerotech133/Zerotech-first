"""持仓操作服务：加仓（含纪律）/部分减仓/分批止盈。对标 V1 批次制持仓。

批次制语义：加仓不改原 lot，而是新建独立 lot；部分减仓拆分——原 lot 扣减 open_qty，
另建 "-cN" 后缀的 CLOSED 留痕 lot 记录本次成交。
"""
from __future__ import annotations

import re
from decimal import ROUND_DOWN, Decimal
from uuid import uuid4

from zhice_os_v2.domain.ontology.objects import utc_now
from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

# V1 纪律：每个计划最多 2 次进场（首建 + 一次加仓）
MAX_ENTRIES_PER_PLAN = 2
# 分批止盈阈值：T1 = +10% 平 50%，T2 = +20% 平剩余全部
T1_THRESHOLD = Decimal("0.10")
T2_THRESHOLD = Decimal("0.20")
T1_CLOSE_RATIO = Decimal("0.5")

# 平仓留痕 lot 的 position_id 约定后缀：{base_id}-c1 / -c2 / ...
_TRACE_ID_PATTERN = re.compile(r"-c\d+$")
_LONG_DIRECTIONS = {"BUY", "LONG"}


class PositionOpsService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # 加仓
    # ------------------------------------------------------------------
    def add_position(
        self,
        strategy_version_id: str,
        market: str,
        ts_code: str,
        qty: Decimal,
        price: Decimal,
        last_fill_price: Decimal | None = None,
    ) -> dict:
        """加仓：新建独立 lot（批次制，不改原 lot）。

        纪律（V1）：
        - last_fill_price 提供时，仅当 price >= last_fill_price 才允许（不向下摊薄）；
        - 同标的累计 lot 数 >= MAX_ENTRIES_PER_PLAN 时拒绝。
        """
        if qty is None or qty <= 0:
            return {"ok": False, "position_id": None, "reason": "invalid_qty"}
        if price is None or price <= 0:
            return {"ok": False, "position_id": None, "reason": "invalid_price"}

        entries = self._entries_for(strategy_version_id, ts_code)
        if len(entries) >= MAX_ENTRIES_PER_PLAN:
            return {"ok": False, "position_id": None, "reason": "max_entries_reached"}

        if last_fill_price is not None and price < last_fill_price:
            return {"ok": False, "position_id": None, "reason": "averaging_down_forbidden"}

        lot = PositionLot(
            position_id=f"pos-add-{uuid4().hex[:12]}",
            strategy_version_id=strategy_version_id,
            market=market,
            ts_code=ts_code,
            direction=entries[-1].direction if entries else "BUY",
            open_qty=qty,
            open_price=price,
        )
        self._db.save_position(lot)
        return {"ok": True, "position_id": lot.position_id, "reason": "added"}

    # ------------------------------------------------------------------
    # 部分减仓
    # ------------------------------------------------------------------
    def partial_close(
        self,
        position_id: str,
        close_qty: Decimal,
        close_price: Decimal,
        sell_fee: Decimal = Decimal("0"),
    ) -> dict:
        """部分减仓：拆分 lot。close_qty == open_qty 时整仓 CLOSED（不留痕 lot）。

        费用处理：
        - open_fee_portion = lot.fee * (close_qty / lot.open_qty)  买入费用按比例摊销
        - net_pnl = gross_pnl - open_fee_portion - sell_fee         净值（已扣双边费用）
        - 平仓留痕 lot 的 fee = open_fee_portion + sell_fee（双边费汇总，可审计）
        - 剩余 lot 的 fee 相应扣减，保留归属精度
        """
        lot = self._db.get_position(position_id)
        if lot is None:
            return {"ok": False, "position_id": position_id, "reason": "position_not_found"}
        if lot.status != PositionStatus.OPEN:
            return {"ok": False, "position_id": position_id, "reason": "position_not_open"}
        if close_qty is None or close_qty <= 0 or close_qty > lot.open_qty:
            return {"ok": False, "position_id": position_id, "reason": "invalid_close_qty"}
        if close_price is None or close_price <= 0:
            return {"ok": False, "position_id": position_id, "reason": "invalid_close_price"}

        gross_pnl = self._pnl(lot, close_qty, close_price)
        # 买入费用按平仓比例分摊（lot.fee 默认 0，无 fee 时此处为 0）
        open_fee_portion = (lot.fee * close_qty / lot.open_qty) if lot.open_qty > 0 else Decimal("0")
        net_pnl = gross_pnl - open_fee_portion - sell_fee
        total_fee = open_fee_portion + sell_fee
        remaining = lot.open_qty - close_qty

        if remaining == 0:
            # 整仓平：原 lot 直接 CLOSED；fee 字段更新为双边费汇总
            lot.status = PositionStatus.CLOSED
            lot.close_qty = lot.open_qty
            lot.close_price = close_price
            lot.pnl_amount = net_pnl
            lot.fee = total_fee  # 完整费用留痕（买入全额 + 卖出全额）
            lot.closed_at = utc_now()
            self._db.save_position(lot)
            return {
                "ok": True,
                "position_id": position_id,
                "closed_qty": close_qty,
                "remaining_qty": Decimal("0"),
                "realized_pnl": net_pnl,
                "trace_position_id": None,
                "reason": "closed",
            }

        # 剩余 > 0：原 lot 扣减并保持 OPEN；fee 按剩余比例保留
        lot.fee = lot.fee - open_fee_portion
        lot.open_qty = remaining
        self._db.save_position(lot)

        # 新建已平部分留痕 lot（-c1/-c2/...）
        trace_id = self._next_trace_id(position_id)
        trace = PositionLot(
            position_id=trace_id,
            strategy_version_id=lot.strategy_version_id,
            market=lot.market,
            ts_code=lot.ts_code,
            direction=lot.direction,
            open_qty=close_qty,
            open_price=lot.open_price,
            status=PositionStatus.CLOSED,
            close_qty=close_qty,
            close_price=close_price,
            pnl_amount=net_pnl,
            fee=total_fee,  # 双边费汇总（买入比例 + 卖出）
            opened_at=lot.opened_at,
            closed_at=utc_now(),
        )
        self._db.save_position(trace)
        return {
            "ok": True,
            "position_id": position_id,
            "closed_qty": close_qty,
            "remaining_qty": remaining,
            "realized_pnl": net_pnl,
            "trace_position_id": trace_id,
            "reason": "partial_closed",
        }

    # ------------------------------------------------------------------
    # 分批止盈
    # ------------------------------------------------------------------
    def tiered_take_profit(self, position_id: str, current_price: Decimal) -> dict:
        """分批止盈：T1 = +10% 平 50%，T2 = +20% 平剩余全部。

        档位跟踪：T1 是否已执行看库中是否存在 {position_id}-c1 留痕；T2 后整仓 CLOSED。
        """
        def none(reason: str) -> dict:
            return {"action": "NONE", "closed_qty": Decimal("0"), "realized_pnl": Decimal("0"), "reason": reason}

        lot = self._db.get_position(position_id)
        if lot is None:
            return none("position_not_found")
        if lot.status != PositionStatus.OPEN:
            return none("position_not_open")
        if current_price is None or current_price <= 0:
            return none("invalid_price")
        if lot.open_price <= 0:
            return none("invalid_open_price")

        pct = current_price / lot.open_price - Decimal("1")
        t1_done = self._db.get_position(f"{position_id}-c1") is not None

        # 先判 T2（高档位优先：未经 T1 直达 20% 时一次平剩余全部）
        if pct >= T2_THRESHOLD:
            close_qty = lot.open_qty
            result = self.partial_close(position_id, close_qty, current_price)
            if not result["ok"]:
                return none(result["reason"])
            return {
                "action": "T2",
                "closed_qty": result["closed_qty"],
                "realized_pnl": result["realized_pnl"],
                "reason": "t2_take_profit",
            }

        if pct >= T1_THRESHOLD and not t1_done:
            close_qty = lot.open_qty * T1_CLOSE_RATIO
            # 股票整数股：父 lot 为整数时向下取整
            if lot.open_qty == lot.open_qty.to_integral_value():
                close_qty = close_qty.to_integral_value(rounding=ROUND_DOWN)
            if close_qty <= 0:
                return none("qty_too_small")
            result = self.partial_close(position_id, close_qty, current_price)
            if not result["ok"]:
                return none(result["reason"])
            return {
                "action": "T1",
                "closed_qty": result["closed_qty"],
                "realized_pnl": result["realized_pnl"],
                "reason": "t1_take_profit",
            }

        return none("below_threshold")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _entries_for(self, strategy_version_id: str, ts_code: str) -> list[PositionLot]:
        """同标的的进场 lot（排除 -cN 平仓留痕），按开仓时间排序。"""
        entries = [
            p
            for p in self._db.list_positions()
            if p.strategy_version_id == strategy_version_id
            and p.ts_code == ts_code
            and not _TRACE_ID_PATTERN.search(p.position_id)
        ]
        return sorted(entries, key=lambda p: p.opened_at)

    def _next_trace_id(self, position_id: str) -> str:
        n = 1
        while self._db.get_position(f"{position_id}-c{n}") is not None:
            n += 1
        return f"{position_id}-c{n}"

    @staticmethod
    def _pnl(lot: PositionLot, close_qty: Decimal, close_price: Decimal) -> Decimal:
        unit = close_price - lot.open_price if lot.direction in _LONG_DIRECTIONS else lot.open_price - close_price
        return unit * close_qty
