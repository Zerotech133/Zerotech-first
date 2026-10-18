"""账户统计服务：基于真实持仓 + 最新价计算多策略独立虚拟账户指标。

V1 完整口径（对齐 zhice/backend get_account_summary）：
  - realized_pnl：该策略所有 CLOSED 持仓 pnl_amount 之和
  - today_pnl：OPEN 持仓逐笔——当日开仓 (现价-开仓价)*qty；否则 (现价-昨收)*qty
    （昨收 = v2_bar_daily 该标的最近第 2 根 close；参照"今日"= 该标的最新日线交易日）
  - total_pnl = realized_pnl + unrealized_pnl；total_equity = initial + total_pnl
"""
from __future__ import annotations

from collections import defaultdict

from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


def _initial_capital(market: str) -> float:
    s = get_settings()
    return {
        "CN": s.sim_initial_capital,
        "US": s.sim_initial_capital_us,
        "HK": s.sim_initial_capital_hk,
        "CRYPTO": s.sim_initial_capital_crypto,
    }.get(market, s.sim_initial_capital)


class AccountStatsService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def _latest_prices(self, symbols: list[str], market: str) -> dict[str, float]:
        # 复用 positions router 的查价逻辑
        from zhice_os_v2.api.routers.positions import _get_latest_prices
        return _get_latest_prices(symbols, market)

    def _pre_closes(self, symbols: list[str], market: str) -> dict[str, float]:
        # 昨收：最近第 2 根日线 close（V1 口径 pre_close）
        from zhice_os_v2.api.routers.positions import _get_pre_closes
        return _get_pre_closes(symbols, market)

    def _latest_trade_date(self, market: str):
        # 市场最新日线交易日（"今日"参照日，V1 latest_trading_day 口径）
        from zhice_os_v2.api.routers.positions import _get_latest_trade_date
        return _get_latest_trade_date(market)

    def _realized_pnl_by_strategy(self, market: str) -> dict[str, float]:
        # 各策略 CLOSED 持仓 pnl_amount 之和
        from zhice_os_v2.api.routers.positions import _get_realized_pnl_by_strategy
        return _get_realized_pnl_by_strategy(market)

    @staticmethod
    def _strategy_names(market: str | None = None) -> dict[str, str]:
        """strategy_version_id → 策略名（V1 名称，如「缠论v2」；去掉 [V1] 前缀）。

        market 传入时只返回该市场的策略（账户列表场景）；
        None 返回全库（持仓明细等仅做名字解析的场景）。
        """
        try:
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session
            session = get_session()
            q = (
                session.query(orm.StrategyVersionModel.strategy_version_id, orm.StrategyModel.strategy_name)
                .join(orm.StrategyModel, orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id)
            )
            if market:
                q = q.filter(orm.StrategyVersionModel.market == market)
            rows = q.all()
            session.close()
            out = {}
            for sv_id, name in rows:
                n = (name or "").strip()
                if n.startswith("[V1] "):
                    n = n[5:]
                out[sv_id] = n
            return out
        except Exception:
            return {}

    def _account_today_pnl(self, lots: list, prices: dict[str, float],
                           pre_closes: dict[str, float], ref_date, tz) -> float:
        """账户今日盈亏：逐笔 V1 口径，缺昨收的笔记 None 跳过。"""
        from zhice_os_v2.api.routers.positions import _is_opened_today, _lot_today_pnl
        total = 0.0
        for l in lots:
            cur = prices.get(l.ts_code, float(l.open_price))
            opened_today = _is_opened_today(l.opened_at, ref_date, tz)
            tp = _lot_today_pnl(opened_today, cur, float(l.open_price), float(l.open_qty),
                                pre_closes.get(l.ts_code))
            if tp is not None:
                total += tp
        return total

    def by_strategy(
        self,
        market: str,
        *,
        account_id: str | None = None,
        user_strategy_id: str | None = None,
    ) -> list[dict]:
        """按策略版本分账户，返回每个账户的完整指标（V1 字段集）。

        owner-scoped 过滤（Phase B）：account_id / user_strategy_id 缺省 None=旧全局行为
        （逐字一致）；传入则只统计该账户/该实例归属的持仓，且不再补全"零仓策略"
        （零仓补全属全局视图特性，owner 视图不泄露他人策略账户）。
        """
        positions = self._db.list_open_positions(market=market)
        owner_scoped = account_id is not None or user_strategy_id is not None
        if account_id is not None:
            positions = [p for p in positions if p.account_id == account_id]
        if user_strategy_id is not None:
            positions = [p for p in positions if p.user_strategy_id == user_strategy_id]
        # 按 strategy_version_id 分组
        groups: dict[str, list] = defaultdict(list)
        for p in positions:
            groups[p.strategy_version_id].append(p)

        # 批量查价 / 昨收 / 参照交易日 / 已实现盈亏
        all_symbols = list({p.ts_code for p in positions})
        prices = self._latest_prices(all_symbols, market)
        pre_closes = self._pre_closes(all_symbols, market)
        ref_date = self._latest_trade_date(market)
        realized_map = self._realized_pnl_by_strategy(market)
        tz = get_profile(market).tz

        initial = _initial_capital(market)
        names = self._strategy_names(market)
        # 无持仓的策略也列出（从策略版本表补全，权益=初始+已实现）——0 仓账户在列表可见。
        # owner-scoped 视图不补全（零仓补全是全局列表特性，否则会暴露他人策略账户）。
        if not owner_scoped:
            all_svs = set(names.keys()) | set(realized_map.keys())
            for sv in sorted(all_svs - set(groups.keys())):
                groups[sv] = []
        result = []
        for sv_id, lots in groups.items():
            if sv_id in ("", "0"):
                continue  # 无归属仓不展示为账户（合并口径已含）
            total_cost = sum(float(l.open_price) * float(l.open_qty) for l in lots)
            market_value = sum(prices.get(l.ts_code, float(l.open_price)) * float(l.open_qty) for l in lots)
            unrealized_pnl = market_value - total_cost
            realized_pnl = realized_map.get(sv_id, 0.0)
            total_pnl = realized_pnl + unrealized_pnl
            # 权益 = 初始 + 已实现 + 浮动（V1：total_equity - initial = total_pnl）
            total_equity = initial + total_pnl
            # 现金 = 初始 − 在仓成本 + 已实现（卖出回款含利润）；非负 clamp（导入历史超配显示满仓）
            cash = max(0.0, initial - total_cost + realized_pnl)
            # 仓位 = 持仓市值 / 权益，封顶 100%（超配显示为满仓）
            position_ratio = min(100.0, market_value / total_equity * 100) if total_equity > 0 else 0
            today_pnl = self._account_today_pnl(lots, prices, pre_closes, ref_date, tz)
            result.append({
                "strategy_version_id": sv_id,
                "strategy_name": names.get(sv_id, sv_id),
                "market": market,
                "initial_capital": round(initial, 2),
                "available_cash": round(cash, 2),
                "market_value": round(market_value, 2),
                "total_equity": round(total_equity, 2),
                "unrealized_pnl": round(unrealized_pnl, 2),
                "unrealized_pnl_pct": round(unrealized_pnl / total_cost * 100, 2) if total_cost else 0,
                "today_pnl": round(today_pnl, 2),
                "total_pnl": round(total_pnl, 2),
                "total_pnl_pct": round(total_pnl / initial * 100, 2) if initial else 0,
                "realized_pnl": round(realized_pnl, 2),
                "realized_pnl_pct": round(realized_pnl / initial * 100, 2) if initial else 0,
                "position_ratio": round(position_ratio, 2),
                "position_count": len(lots),
            })
        return sorted(result, key=lambda x: x["total_equity"], reverse=True)

    def market_summary(self, market: str) -> dict:
        """整个市场的汇总（所有策略账户合并，V1 字段集）。"""
        accounts = self.by_strategy(market)
        if not accounts:
            # 无策略账户（该市场无数据）：权益归零并标 no_data，不再凭空返回 1×初始资金，
            # 否则跨市场归一会把并不存在的权益（如 HK 80万）计入合并总额。
            return {
                "market": market,
                "total_equity": 0,
                "available_cash": 0,
                "market_value": 0,
                "unrealized_pnl": 0,
                "unrealized_pnl_pct": 0,
                "today_pnl": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "realized_pnl": 0,
                "realized_pnl_pct": 0,
                "position_ratio": 0,
                "position_count": 0,
                "account_count": 0,
                "no_data": True,
            }
        initial = _initial_capital(market)
        total_initial = initial * len(accounts)
        total_mv = sum(a["market_value"] for a in accounts)
        total_cost = sum(a["market_value"] - a["unrealized_pnl"] for a in accounts)
        total_equity = sum(a["total_equity"] for a in accounts)
        total_unrealized = sum(a["unrealized_pnl"] for a in accounts)
        total_realized = sum(a["realized_pnl"] for a in accounts)
        total_pnl = sum(a["total_pnl"] for a in accounts)
        return {
            "market": market,
            "total_equity": round(total_equity, 2),
            "available_cash": round(sum(a["available_cash"] for a in accounts), 2),
            "market_value": round(total_mv, 2),
            "unrealized_pnl": round(total_unrealized, 2),
            "unrealized_pnl_pct": round(total_unrealized / total_cost * 100, 2) if total_cost else 0,
            "today_pnl": round(sum(a["today_pnl"] for a in accounts), 2),
            "total_pnl": round(total_pnl, 2),
            "total_pnl_pct": round(total_pnl / total_initial * 100, 2) if total_initial else 0,
            "realized_pnl": round(total_realized, 2),
            "realized_pnl_pct": round(total_realized / total_initial * 100, 2) if total_initial else 0,
            "position_ratio": round(min(100.0, total_mv / total_equity * 100), 2) if total_equity > 0 else 0,
            "position_count": sum(a["position_count"] for a in accounts),
            "account_count": len(accounts),
        }
