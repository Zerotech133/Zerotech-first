"""统一 A 股卖出守卫：CN 当日开仓不可卖、可卖量、停牌/跌停 fail-closed。

背景（P0-1，task-38）：
- CN 实行 T+1：当日开仓批次当日不可卖；此前仅 `workflow_service.run_intraday` 与
  `run_spec_signals` 各自做了一次 T+1 锁，人工平仓 / 审批 / 模拟盘等旁路没有统一守卫。
- 本模块把「能不能卖」收敛成纯函数：T+1 锁 + 可卖量 + `market_rules.can_sell`
  （停牌/跌停封死），所有卖出入口（当前先接 `POST /positions/close`）必须复用。
- 安全默认（fail-closed）：
  * CN 持仓 `opened_at` 缺失 → 无法证明非当日，视为当日仓不可卖；
  * 无有效卖价（价≤0/None）→ 不可卖；
  * 可卖量不足始终拒绝，不做部分成交猜测。

用法::

    d = check_sell(market="CN", ts_code="600519.SH", qty=100,
                   lots=open_positions, price=10.0, pre_close=10.2)
    if not d.ok:
        reject(d.reason)  # 调用方必须整单拒绝（不得部分写入）
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Iterable

from zhice_os_v2.domain.execution.market_rules import can_sell
from zhice_os_v2.domain.market.registry import get_profile


@dataclass(frozen=True)
class SellDecision:
    """卖出守卫结论（纯数据，便于 API/服务层统一映射 HTTP / 异常）。"""

    ok: bool
    reason: str
    market: str
    requested_qty: Decimal
    available_qty: Decimal
    locked: bool = False


def _dec(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _fmt(qty: Decimal) -> str:
    """数量展示：去掉无意义小数尾零（100.0000 → 100）。"""
    text = format(qty.normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _as_utc(value: datetime) -> datetime:
    """DB 持久化时间为 UTC；naive 时间按 UTC 解释（与 ORM DateTime(timezone=True) 口径一致）。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _market_date(opened_at: datetime, market: str) -> date:
    """opened_at（UTC/naive）换算到市场本地日期。"""
    profile = get_profile(market)
    return _as_utc(opened_at).astimezone(profile.tz).date()


def _market_today(market: str, now: datetime | None = None) -> date:
    profile = get_profile(market)
    current = _as_utc(now) if now is not None else datetime.now(UTC)
    return current.astimezone(profile.tz).date()


def t_plus_one_locked(
    opened_at: datetime | None,
    market: str = "CN",
    now: datetime | None = None,
) -> bool:
    """该批次是否因 T+1 当日不可卖（CN 专用；其他市场 T+0 恒 False）。

    - 市场 Profile.same_day_sell_allowed=True（US/HK/CRYPTO）→ False；
    - CN 且 opened_at 缺失 → True（fail-closed，无法证明非当日）；
    - CN 且 opened_at 的市场本地日期 == 当前市场日期 → True。
    """
    profile = get_profile(market)
    if profile.same_day_sell_allowed:
        return False
    if opened_at is None:
        return True
    return _market_date(opened_at, market) == _market_today(market, now)


def _lot_qty(lot: Any) -> Decimal:
    if isinstance(lot, dict):
        return _dec(lot.get("open_qty", 0))
    return _dec(getattr(lot, "open_qty", 0))


def _lot_opened_at(lot: Any) -> datetime | None:
    if isinstance(lot, dict):
        return lot.get("opened_at")
    return getattr(lot, "opened_at", None)


def _lock_reasons(lots: Iterable[Any], market: str, now: datetime | None) -> list[str]:
    """返回被 T+1 锁排除的批次原因（去重、稳定顺序，供报文展示）。"""
    reasons: list[str] = []
    for lot in lots:
        opened_at = _lot_opened_at(lot)
        if not t_plus_one_locked(opened_at, market, now):
            continue
        if opened_at is None:
            reason = "opened_at 缺失，无法确认当日开仓"
        else:
            reason = f"当日开仓 {_market_date(opened_at, market).isoformat()}"
        if reason not in reasons:
            reasons.append(reason)
    return reasons


def available_qty(
    lots: Iterable[Any],
    market: str = "CN",
    now: datetime | None = None,
) -> Decimal:
    """可卖数量：CN 排除当日开仓批次（opened_at 缺失同样排除）；其他市场全量。"""
    total = Decimal("0")
    for lot in lots:
        if t_plus_one_locked(_lot_opened_at(lot), market, now):
            continue
        total += _lot_qty(lot)
    return total


def check_sell(
    market: str,
    ts_code: str,
    qty: Any,
    lots: Iterable[Any],
    price: Any,
    pre_close: Any = None,
    name: str | None = None,
    snapshot_time: datetime | None = None,
    now: datetime | None = None,
) -> SellDecision:
    """统一卖出校验（纯函数，不写库、不下单）。

    Args:
        market: 市场代码（CN/US/HK/CRYPTO）
        ts_code: 标的代码
        qty: 请求卖出数量（须 ≤ 可卖量）
        lots: 该标的的持仓批次（含 opened_at/open_qty；可为 PositionLot/SimpleNamespace/dict）
        price: 卖出价（须为有效行情价；None/≤0 视为停牌/数据缺失 fail-closed）
        pre_close: 昨收（用于跌停判定；缺失时 can_sell 跳过跌停检查）
        name: 证券名称（ST 判定用，可空）
        snapshot_time: 行情快照时间（超过 market_rules.STALE_MINUTES 视为疑似停牌）
        now: 注入当前时间（测试用；默认 UTC now）

    Returns:
        SellDecision；`ok=False` 时调用方必须整单拒绝且**不得部分写入**。
    """
    requested = _dec(qty)
    lots = list(lots)
    available = available_qty(lots, market, now)
    lock_reasons = _lock_reasons(lots, market, now)
    locked = bool(lock_reasons)

    if requested <= 0:
        return SellDecision(
            ok=False,
            reason=f"卖出数量必须大于 0（请求 {_fmt(requested)}）",
            market=market,
            requested_qty=requested,
            available_qty=available,
            locked=locked,
        )

    if requested > available:
        if available == 0 and lock_reasons:
            reason = f"T+1锁：{'；'.join(lock_reasons)}（可卖量 0 < 请求 {_fmt(requested)}）"
        else:
            reason = (
                f"可卖量不足：可卖 {_fmt(available)} < 请求 {_fmt(requested)}"
            )
        return SellDecision(
            ok=False,
            reason=reason,
            market=market,
            requested_qty=requested,
            available_qty=available,
            locked=locked,
        )

    if price is None or float(price) <= 0:
        return SellDecision(
            ok=False,
            reason="无有效卖价（价≤0 或行情缺失），停牌/数据失联 fail-closed",
            market=market,
            requested_qty=requested,
            available_qty=available,
            locked=locked,
        )

    sellable, why = can_sell(
        float(price),
        pre_close,
        ts_code,
        name,
        snapshot_time=snapshot_time,
        now=_as_utc(now) if now is not None else None,
        market=market,
    )
    if not sellable:
        return SellDecision(
            ok=False,
            reason=why,
            market=market,
            requested_qty=requested,
            available_qty=available,
            locked=locked,
        )
    return SellDecision(
        ok=True,
        reason="可卖",
        market=market,
        requested_qty=requested,
        available_qty=available,
        locked=locked,
    )


def check_position_sell(
    position: Any,
    price: Any,
    pre_close: Any = None,
    name: str | None = None,
    snapshot_time: datetime | None = None,
    now: datetime | None = None,
) -> SellDecision:
    """单持仓便捷入口（qty=open_qty，lots=[position]）；供平仓 API/服务直达调用。"""
    market = getattr(position, "market", "CN")
    return check_sell(
        market=market,
        ts_code=getattr(position, "ts_code", ""),
        qty=_lot_qty(position),
        lots=[position],
        price=price,
        pre_close=pre_close,
        name=name,
        snapshot_time=snapshot_time,
        now=now,
    )
