"""执行服务：将授权通过的指令转换为持仓变化。

P0-2 SELL 守卫（T+1 / 可卖量 / 停牌 / 归属）：
- ``sell_guard(...)``：审批/执行前的统一卖出守卫，返回可卖量、有效数量与拒绝原因；
  审批链（``ApprovalExecutionService``）在提交 broker 前调用，拒绝时抛 ``SellGuardDenied``。
- ``_apply_sell(..., enforce_sell_guard=True)``：FIFO 只消耗「可卖 + 归属匹配」的批次，
  保证即使上层漏判也**绝不**平当日 lot / 跨账户 lot；默认 False 保持 legacy 直接调用行为
  （历史请求/测试逐字不变）。
- T+1 判定：``Profile.same_day_sell_allowed`` 为 False（CN）时，当日开仓批次不可卖；
  ``opened_at`` 缺失 fail-closed（T+1 市场视为不可卖）；非交易日无「当日开仓」概念，不锁
  （周末/节假日造成的测试快照不误伤；正式交易日历接入前用 Profile.fallback_is_open 回退）。
- 停牌/跌停：复用 ``domain.execution.market_rules.can_sell``（价≤0=停牌/无行情；
  快照超 30 分钟未更新=疑似停牌；跌停封死不可卖）。
- 归属：``user_strategy_id``/``account_id`` 精确匹配；legacy 无归属卖出只允许平
  「无运行时归属」的 lot，禁止跨用户/跨账户误平。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from zhice_os_v2.domain.execution.broker import OrderResult, OrderStatus
from zhice_os_v2.domain.execution.market_rules import can_sell
from zhice_os_v2.domain.ids import fit_id
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

logger = logging.getLogger(__name__)

# 稳定机读拒绝码（供 HTTP（LiveExecutionNotConfigured 子类）与告警映射）
SELL_DENY_NO_POSITION = "sell.no_position"
SELL_DENY_T1_LOCKED = "sell.t1_locked"
SELL_DENY_OPENED_AT_MISSING = "sell.opened_at_missing"
SELL_DENY_OWNER_MISMATCH = "sell.owner_mismatch"
SELL_DENY_SUSPENDED = "sell.suspended"
SELL_DENY_INVALID_QTY = "sell.invalid_qty"


def current_trading_date(market: str, now: datetime | None = None):
    """市场时区「最近交易日」：今天开市取今天，否则回退最近工作日（无正式日历时 fallback）。

    与 ``Profile.trading_date()`` 的区别：后者取日历日期，周末会把「上一交易日开仓」误判为
    今日；T+1 锁按最近交易日判断。节假日边界由 Profile.fallback_is_open 回退工作日近似，
    待正式交易日历接入后统一（风险已记录在任务报告）。
    """
    profile = get_profile(market)
    ref = (now or datetime.now(profile.tz))
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    ref = ref.astimezone(profile.tz)
    day = ref.date()
    for _ in range(15):
        try:
            if profile.fallback_is_open(day):
                return day
        except Exception:  # noqa: BLE001 - 画像异常时退回日历日
            break
        day -= timedelta(days=1)
    return ref.date()


def _local_date(value: datetime | None, tz):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(tz).date()


def _lot_sell_block_reason(lot: PositionLot, market: str, now: datetime | None) -> str | None:
    """单 lot 是否因 T+1 不可卖（返回拒绝码；可卖返回 None）。"""
    profile = get_profile(market)
    if profile.same_day_sell_allowed:
        return None
    ref = (now or datetime.now(profile.tz))
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    ref_local = ref.astimezone(profile.tz)
    try:
        if not profile.fallback_is_open(ref_local.date()):
            # 非交易日：没有「当日开仓」概念（也无法交易）；不锁，避免周末/节假日快照误伤。
            return None
    except Exception:  # noqa: BLE001 - 画像异常时按交易日处理
        pass
    opened_local = _local_date(lot.opened_at, profile.tz)
    if opened_local is None:
        return SELL_DENY_OPENED_AT_MISSING
    if opened_local >= ref_local.date():
        return SELL_DENY_T1_LOCKED
    return None


def _owner_matches(
    lot: PositionLot,
    user_strategy_id: str | None,
    account_id: str | None,
) -> bool:
    """归属匹配：带归属时精确匹配；legacy 无归属卖出只允许平无归属 lot。"""
    if user_strategy_id is None and account_id is None:
        return lot.user_strategy_id is None and lot.account_id is None
    if user_strategy_id is not None and lot.user_strategy_id != user_strategy_id:
        return False
    if account_id is not None and lot.account_id != account_id:
        return False
    return True


class ExecutionService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    # ------------------------------------------------------------------ #
    # 卖出守卫（供审批/执行链在提交前调用）
    # ------------------------------------------------------------------ #
    def sell_guard(
        self,
        *,
        market: str,
        strategy_version_id: str,
        ts_code: str,
        qty,
        user_strategy_id: str | None = None,
        account_id: str | None = None,
        price=None,
        pre_close=None,
        name: str | None = None,
        snapshot_time: datetime | None = None,
        now: datetime | None = None,
    ) -> dict:
        """审批/执行前统一卖出守卫。

        返回 dict（不抛异常，便于路由/上层组装 HTTP 语义）：
        - denied/code/reason：是否拒绝、稳定机读码、原因
        - requested_qty / sellable_qty / effective_qty：请求量、可卖量、实际建议提交量
        - t1_locked_qty / ownership_excluded_qty / clamped / price_check：证据
        """
        requested = Decimal(str(qty or 0))
        result: dict = {
            "market": market,
            "ts_code": ts_code,
            "strategy_version_id": str(strategy_version_id),
            "requested_qty": str(requested),
            "sellable_qty": "0",
            "effective_qty": "0",
            "t1_locked_qty": "0",
            "ownership_excluded_qty": "0",
            "opened_at_missing_qty": "0",
            "clamped": False,
            "price_check": "skipped(no_price)",
            "denied": False,
            "deny_code": None,
            "deny_reason": None,
            "t1_blocked_lots": [],
        }

        def _deny(code: str, reason: str) -> dict:
            result["denied"] = True
            result["deny_code"] = code
            result["deny_reason"] = reason
            result["effective_qty"] = "0"
            return result

        if requested <= 0:
            return _deny(SELL_DENY_INVALID_QTY, f"卖出数量必须>0（{requested}）")

        lots = [
            p for p in self._db.list_open_positions(market)
            if p.strategy_version_id == strategy_version_id and p.ts_code == ts_code
        ]
        result["open_lots"] = len(lots)
        owned_lots = [lot for lot in lots if _owner_matches(lot, user_strategy_id, account_id)]
        excluded_qty = sum(
            (lot.open_qty for lot in lots if not _owner_matches(lot, user_strategy_id, account_id)),
            Decimal("0"),
        )
        result["ownership_excluded_qty"] = str(excluded_qty)
        if excluded_qty > 0:
            result["ownership_mismatch_lots"] = len(lots) - len(owned_lots)

        sellable_lots: list[PositionLot] = []
        t1_qty = Decimal("0")
        missing_qty = Decimal("0")
        for lot in owned_lots:
            reason = _lot_sell_block_reason(lot, market, now)
            if reason is None:
                sellable_lots.append(lot)
                continue
            if reason == SELL_DENY_OPENED_AT_MISSING:
                missing_qty += lot.open_qty
            else:
                t1_qty += lot.open_qty
            result["t1_blocked_lots"].append({
                "position_id": lot.position_id,
                "open_qty": str(lot.open_qty),
                "opened_at": lot.opened_at.isoformat() if lot.opened_at else None,
                "reason": reason,
            })
        sellable_qty = sum((lot.open_qty for lot in sellable_lots), Decimal("0"))
        result["sellable_qty"] = str(sellable_qty)
        result["t1_locked_qty"] = str(t1_qty)
        result["opened_at_missing_qty"] = str(missing_qty)

        # 停牌/无行情/跌停：复用市场规则（price=None 时跳过，由上层决定是否必须带价）
        if price is not None:
            try:
                sellable_ok, why = can_sell(
                    price, pre_close, ts_code, name,
                    snapshot_time=snapshot_time, now=now, market=market,
                )
            except Exception as exc:  # noqa: BLE001 - 规则异常按不可卖 fail-closed
                sellable_ok, why = False, f"卖出可行性校验异常({type(exc).__name__})"
            result["price_check"] = why
            if not sellable_ok:
                return _deny(SELL_DENY_SUSPENDED, why)

        if sellable_qty <= 0:
            if not lots:
                return _deny(SELL_DENY_NO_POSITION, "无同策略同标的持仓")
            if not owned_lots:
                return _deny(
                    SELL_DENY_OWNER_MISMATCH,
                    "持仓归属与卖出方不一致（禁止跨账户/跨用户平仓）",
                )
            if missing_qty > 0:
                return _deny(SELL_DENY_OPENED_AT_MISSING, "持仓 opened_at 缺失，T+1 无法确认，fail-closed")
            if t1_qty > 0:
                return _deny(
                    SELL_DENY_T1_LOCKED,
                    "T+1：当日开仓批次当日不可卖（可卖量为 0）",
                )
            return _deny(SELL_DENY_NO_POSITION, "无可卖持仓")

        effective = min(requested, sellable_qty)
        result["effective_qty"] = str(effective)
        if requested > sellable_qty:
            locked_delta = t1_qty + excluded_qty + missing_qty
            if locked_delta > 0:
                # 差额来自 T+1/归属/opened_at 缺失 → 整笔拒绝，禁止用部分成交掩盖违规
                if t1_qty > 0:
                    code, reason = SELL_DENY_T1_LOCKED, (
                        f"请求卖出 {requested} > 可卖 {sellable_qty}；其中 {t1_qty} 股为当日开仓（T+1 锁定）"
                    )
                elif excluded_qty > 0:
                    code, reason = SELL_DENY_OWNER_MISMATCH, (
                        f"请求卖出 {requested} > 可卖 {sellable_qty}；其中 {excluded_qty} 股归属不符"
                    )
                else:
                    code, reason = SELL_DENY_OPENED_AT_MISSING, (
                        f"请求卖出 {requested} > 可卖 {sellable_qty}；其中 {missing_qty} 股 opened_at 缺失"
                    )
                return _deny(code, reason)
            # 普通超卖：clamp 到可卖量，由上层按部分成交（PARTIALLY_EXECUTED）处理
            result["clamped"] = True
            logger.warning(
                "卖出守卫：超卖 clamp ts_code=%s requested=%s sellable=%s",
                ts_code, requested, sellable_qty,
            )
        return result

    # ------------------------------------------------------------------ #
    # 成交入账
    # ------------------------------------------------------------------ #
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
        enforce_sell_guard: bool = False,
        now: datetime | None = None,
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
            sell_info = self._apply_sell(
                result, strategy_version_id, market, ts_code,
                user_strategy_id=user_strategy_id,
                account_id=account_id,
                enforce_sell_guard=enforce_sell_guard,
                now=now,
            )
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
        user_strategy_id: str | None = None,
        account_id: str | None = None,
        enforce_sell_guard: bool = False,
        now: datetime | None = None,
    ) -> dict:
        """卖出成交：按 FIFO 消耗同策略同标的的 OPEN 批次，逐笔部分平仓并落 realized。

        enforce_sell_guard=True（审批/执行链）：只消耗「归属匹配 + T+1 可卖」的 lot，
        当日 lot / 他人账户 lot 永不被平；opened_at 缺失在 T+1 市场 fail-closed。
        默认 False：legacy 直接调用逐字保持旧行为（仍执行归属过滤，防跨账户误平）。

        返回 {"position": PositionLot | None, "over_sell_qty": Decimal, "closed_qty": Decimal,
              "guard_skipped_qty": Decimal}
        - over_sell_qty > 0 表示超持仓/被守卫过滤后仍有剩余无法平仓（已平部分已落库，不回滚）
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
            and _owner_matches(p, user_strategy_id, account_id)
        ]
        guard_skipped_qty = Decimal("0")
        if enforce_sell_guard:
            sellable_lots: list[PositionLot] = []
            for lot in lots:
                if _lot_sell_block_reason(lot, market, now) is None:
                    sellable_lots.append(lot)
                else:
                    guard_skipped_qty += lot.open_qty
            if guard_skipped_qty > 0:
                logger.warning(
                    "卖出守卫：%s 有 %s 股当日/不可确认批次被排除（T+1/opened_at），不参与 FIFO",
                    ts_code, guard_skipped_qty,
                )
            lots = sellable_lots
        lots.sort(key=lambda p: (p.opened_at is None, p.opened_at or datetime.min.replace(tzinfo=UTC)))
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

        return {
            "position": last,
            "over_sell_qty": remaining,
            "closed_qty": closed_qty,
            "guard_skipped_qty": guard_skipped_qty,
        }
