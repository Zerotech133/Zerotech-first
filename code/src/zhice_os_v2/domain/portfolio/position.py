"""持仓。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class PositionStatus(StrEnum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"


@dataclass
class PositionLot:
    position_id: str
    strategy_version_id: str
    market: str
    ts_code: str
    direction: str
    open_qty: Decimal
    open_price: Decimal
    status: PositionStatus = PositionStatus.OPEN
    # 归属链：生成该持仓的 trade_plan（结构性 id 修复后 == approval.order_intent_id）。
    # 此前领域对象无此字段、save_position 也不写，v2_position.trade_plan_id 恒 NULL，
    # 丢失"某持仓由哪个计划生成"的可追溯链。仅补 trade_plan_id，不引入 avg_cost。
    trade_plan_id: str | None = None
    close_qty: Decimal | None = None
    close_price: Decimal | None = None
    # 已实现盈亏（平仓时按方向计算并落库 v2_position.pnl_amount）。
    # 此前领域对象无此字段、save_position 也不写，导致部分平仓路径 realized 静默计 0。
    pnl_amount: Decimal | None = None
    # 本批次归属的成交手续费（买入时=全额佣金/印花税/过户费；平仓留痕 lot=买入比例费+卖出费）。
    # 买入 lot 的费用按平仓比例分摊到每次减仓；pnl_amount 已为净值（已扣 fee）。
    fee: Decimal = field(default_factory=lambda: Decimal("0"))
    opened_at: datetime = field(default_factory=utc_now)
    closed_at: datetime | None = None
    # 运行时归属（Phase B 多用户执行隔离）。legacy 路径保持 None，不改变现有行为；
    # user_strategy_id -> v2_user_strategy_instance；account_id -> v2_user_account。
    user_strategy_id: str | None = None
    account_id: str | None = None
