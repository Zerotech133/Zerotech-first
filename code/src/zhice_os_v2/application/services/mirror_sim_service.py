"""跟单模拟盘服务：建账户校验 + 再平衡引擎 + 订阅对账冻结/恢复 + 盯市视图。

直连 get_session/ORM 读平台持仓（同 subscription_observation_service），已入
test_architecture KNOWN_VIOLATIONS。默认 legacy 执行模式下可用（与 enforced 无交集）。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal

from zhice_os_v2.domain.mirror.mirror_sim import compute_target_mv, rebalance_orders
from zhice_os_v2.infrastructure.db import models as orm
from zhice_os_v2.infrastructure.db.session import get_session
from zhice_os_v2.infrastructure.repositories.mirror_sim_repository import MirrorSimRepository

logger = logging.getLogger(__name__)

CHURN_THRESHOLD = 0.01  # 目标与当前市值差 < 1% 权益不动，防 churn


class MirrorSimError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _as_aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _is_subscription_active(user_id: str, strategy_id: str) -> bool:
    from zhice_os_v2.application.services.membership_service import SubscriptionService
    return SubscriptionService().is_subscribed(user_id, strategy_id)


def _is_observable_builtin(strategy_id: str) -> bool:
    s = get_session()
    try:
        row = (s.query(orm.StrategyModel.owner_user_id, orm.StrategyModel.visibility)
               .filter(orm.StrategyModel.strategy_id == strategy_id).one_or_none())
        return row is not None and row[0] is None and row[1] == "PUBLIC"
    finally:
        s.close()


def _strategy_market(strategy_id: str) -> str | None:
    s = get_session()
    try:
        row = (s.query(orm.StrategyModel.market)
               .filter(orm.StrategyModel.strategy_id == strategy_id).one_or_none())
        return row[0] if row else None
    finally:
        s.close()


def _latest_prices(symbols: list[str], market: str) -> dict[str, float]:
    """现价：复用 positions._get_latest_prices（日线基+新鲜快照），延迟 import 避免层耦合。"""
    if not symbols:
        return {}
    from zhice_os_v2.api.routers.positions import _get_latest_prices
    return _get_latest_prices(symbols, market)


def _account_view(acc) -> dict:
    return {
        "mirror_account_id": acc.mirror_account_id,
        "strategy_id": acc.strategy_id,
        "market": acc.market,
        "initial_capital": str(acc.initial_capital),
        "cash_balance": str(acc.cash_balance),
        "status": acc.status,
        "created_at": acc.created_at.isoformat() if acc.created_at else None,
    }


class MirrorSimService:
    def __init__(self, repo: MirrorSimRepository | None = None) -> None:
        self._repo = repo or MirrorSimRepository()

    def create_account(self, user, *, strategy_id: str, initial_capital: Decimal) -> dict:
        if initial_capital is None or Decimal(initial_capital) <= 0:
            raise MirrorSimError("mirror.invalid_capital")
        if not _is_subscription_active(user.user_id, strategy_id):
            raise MirrorSimError("mirror.subscription_required")
        if not _is_observable_builtin(strategy_id):
            raise MirrorSimError("mirror.not_observable_strategy")
        if self._repo.get_by_user_strategy(user.user_id, strategy_id) is not None:
            raise MirrorSimError("mirror.account_exists")
        market = _strategy_market(strategy_id) or "CRYPTO"
        self._repo.create_account(
            mirror_account_id=self._repo.new_account_id(), user_id=user.user_id,
            strategy_id=strategy_id, market=market, initial_capital=Decimal(initial_capital), now=_now(),
        )
        return _account_view(self._repo.get_by_user_strategy(user.user_id, strategy_id))

    def list_accounts(self, user) -> list[dict]:
        return [_account_view(a) for a in self._repo.list_by_user(user.user_id)]

    def sweep(self) -> dict:
        stats = {"processed": 0, "frozen": 0, "resumed": 0, "orders": 0}
        for acc in self._repo.list_active():
            try:
                stats["orders"] += self._rebalance_one(acc.mirror_account_id)
                stats["processed"] += 1
            except Exception:
                logger.exception("mirror sweep 账户失败 %s", acc.mirror_account_id)
        self._reconcile_subscriptions(stats)
        return stats

    def _reconcile_subscriptions(self, stats: dict) -> None:
        """active 但订阅失效→frozen；frozen 但订阅恢复→active。per-account 事务，单账户异常不影响全局。"""
        s = get_session()
        try:
            snapshots = [(a.mirror_account_id, a.user_id, a.strategy_id)
                         for a in s.query(orm.MirrorSimAccountModel).all()]
        finally:
            s.close()

        for mid, uid, sid in snapshots:
            s2 = get_session()
            try:
                acc = (s2.query(orm.MirrorSimAccountModel)
                       .filter_by(mirror_account_id=mid).one_or_none())
                if acc is None:
                    continue
                active = _is_subscription_active(uid, sid)
                if acc.status == "active" and not active:
                    acc.status = "frozen"
                    acc.updated_at = _now()
                    s2.commit()
                    stats["frozen"] += 1
                elif acc.status == "frozen" and active:
                    acc.status = "active"
                    acc.updated_at = _now()
                    s2.commit()
                    stats["resumed"] += 1
            except Exception:
                s2.rollback()
                logger.exception("mirror reconcile 账户失败 %s", mid)
            finally:
                s2.close()

    def _rebalance_one(self, mirror_account_id: str) -> int:
        """单账户再平衡（per-account 事务）。返回下单数。"""
        s = get_session()
        try:
            acc = (s.query(orm.MirrorSimAccountModel)
                   .filter_by(mirror_account_id=mirror_account_id).with_for_update().one_or_none())
            if acc is None or acc.status != "active":
                return 0
            if not _is_subscription_active(acc.user_id, acc.strategy_id):
                return 0  # 订阅失效本轮不动，交由 _reconcile 冻结

            plat = self._repo.platform_open_positions(acc.strategy_id)
            positions = (s.query(orm.MirrorSimPositionModel)
                         .filter_by(mirror_account_id=mirror_account_id).all())
            symbols = sorted({p["symbol"] for p in plat} | {p.symbol for p in positions})
            prices = _latest_prices(symbols, acc.market)

            # 已持仓标的缺价 → 用户权益会被低估，进而误卖其它健康标的；本轮整账户跳过、下轮重试。
            if any(p.symbol not in prices for p in positions):
                return 0

            plat_mv: dict[str, float] = {}
            tracked: set[str] = set()
            total_plat = 0.0
            for row in plat:
                px = prices.get(row["symbol"])
                if not px:
                    continue
                mv = row["qty"] * px
                plat_mv[row["symbol"]] = plat_mv.get(row["symbol"], 0.0) + mv
                total_plat += mv
                if row["opened_at"] is not None and _as_aware(row["opened_at"]) > _as_aware(acc.created_at):
                    tracked.add(row["symbol"])

            user_qty = {p.symbol: float(p.quantity) for p in positions}
            equity = float(acc.cash_balance) + sum(
                float(p.quantity) * prices.get(p.symbol, 0.0) for p in positions)
            target = compute_target_mv(plat_mv, tracked, total_plat, equity)
            orders = rebalance_orders(
                target_mv=target, user_qty=user_qty, prices=prices,
                cash=float(acc.cash_balance), equity=equity, churn_threshold=CHURN_THRESHOLD,
            )
            if not orders:
                return 0
            self._apply_orders(s, acc, positions, orders)
            acc.updated_at = _now()
            s.commit()
            return len(orders)
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    def _apply_orders(self, s, acc, positions, orders) -> None:
        pos_by_sym = {p.symbol: p for p in positions}
        now = _now()
        for o in orders:
            qty = Decimal(str(o.quantity))
            px = Decimal(str(o.price))
            if o.side == "SELL":
                p = pos_by_sym.get(o.symbol)
                if p is None:
                    continue
                sell = min(p.quantity, qty)
                p.quantity -= sell
                acc.cash_balance += sell * px
                if p.quantity <= 0:
                    s.delete(p)
                    pos_by_sym.pop(o.symbol, None)
                fill_qty = sell  # 实际成交量（受持仓封顶）
            else:  # BUY
                cost = qty * px
                if cost > acc.cash_balance:  # 双保险不透支
                    qty = acc.cash_balance / px if px > 0 else Decimal("0")
                    cost = qty * px
                if qty <= 0:
                    continue
                acc.cash_balance -= cost
                p = pos_by_sym.get(o.symbol)
                if p is None:
                    p = orm.MirrorSimPositionModel(mirror_account_id=acc.mirror_account_id,
                                                   symbol=o.symbol, quantity=qty, avg_cost=px)
                    s.add(p)
                    pos_by_sym[o.symbol] = p
                else:
                    new_qty = p.quantity + qty
                    p.avg_cost = (p.quantity * p.avg_cost + qty * px) / new_qty if new_qty > 0 else px
                    p.quantity = new_qty
                fill_qty = qty  # BUY qty 已就地封顶
            s.add(orm.MirrorSimFillModel(
                mirror_account_id=acc.mirror_account_id, symbol=o.symbol, side=o.side,
                quantity=fill_qty, price=px, reason=f"rebalance@{now.isoformat(timespec='seconds')}",
                created_at=now))

    def detail(self, user, *, strategy_id: str) -> dict:
        acc = self._repo.get_by_user_strategy(user.user_id, strategy_id)
        if acc is None:
            raise MirrorSimError("mirror.account_not_found")
        positions = self._repo.list_positions(acc.mirror_account_id)
        prices = _latest_prices([p.symbol for p in positions], acc.market)
        pos_views = []
        hold_mv = 0.0
        for p in positions:
            px = prices.get(p.symbol)
            mv = float(p.quantity) * px if px else None
            if mv is not None:
                hold_mv += mv
            pos_views.append({
                "symbol": p.symbol, "quantity": str(p.quantity), "avg_cost": str(p.avg_cost),
                "current_price": px, "market_value": mv,
                "unrealized_pnl": (mv - float(p.quantity) * float(p.avg_cost)) if mv is not None else None,
            })
        equity = float(acc.cash_balance) + hold_mv
        view = _account_view(acc)
        view.update({
            "positions": pos_views,
            "equity": equity,
            "total_pnl": equity - float(acc.initial_capital),
            "recent_fills": [{
                "symbol": f.symbol, "side": f.side, "quantity": str(f.quantity),
                "price": str(f.price), "reason": f.reason,
                "created_at": f.created_at.isoformat() if f.created_at else None,
            } for f in self._repo.recent_fills(acc.mirror_account_id)],
        })
        return view
