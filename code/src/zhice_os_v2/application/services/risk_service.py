"""风控服务（V1 规则移植 + 敞口/单券/流动性/日损规则恢复）。

规则清单（rule_results 键）——前 6 条与 V1 risk_check_service 对齐，第 7 条为本轮新增：
  1. PRICE_RANGE               价格红线（profile.risk_price_guardrail）
  2. MIN_LIQUIDITY             最低流动性（profile.risk_min_turnover）
  3. DATA_FRESHNESS            快照新鲜度（盘中>120min REJECT；V1 T218）
  4. MAX_POSITION_RATIO        计划仓位比例（策略 spec 透传，>0.10 拒）
  5. PORTFOLIO_TOTAL_EXPOSURE  子账户总敞口（阈值 settings.risk_params.max_total_position_pct）
  6. SINGLE_SECURITY_EXPOSURE  单标的敞口（阈值 settings.risk_params.max_single_position_pct）
  7. DAILY_LOSS                单日亏损熔断（阈值 settings.risk_params.max_daily_loss_pct）

设计要点：
  - 阈值来源 settings.risk_params（v2_app_state 实时读库，见 risk_params.py）；
    读取异常回退代码默认——规则照跑不跳过（默认值守门）。
  - 规则数据缺失（内存后端 / 快照缺列 / 敞口查询失败）记 {"result": "SKIPPED",
    "reason": ...} 留痕（decision 落库可见），不再"静默不跑"；除 DATA_FRESHNESS
    基础设施故障 fail-CLOSED（历史行为保留）外，数据缺失一律放行不硬拒。
  - 敞口/日损为 V1 子账户口径：按 plan.strategy_version_id +（owned 计划的）
    account_id/user_strategy_id 过滤持仓；净值 = 市场初始资金 + 该口径已实现盈亏
    （与 position_sizing_service.size_new_position 同基数）。
  - 敞口为 projected 口径（现有持仓成本 + 本计划 qty×ref_price），对齐 08-18
    历史 REJECT 记录格式（limit_pct/exposure_pct 键）。V1 用净值×ratio 估算
    是因为当时计划无 qty；V2 计划携带 qty×ref_price，用真实计划成本更准。
"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.application.services.risk_params import load_risk_params
from zhice_os_v2.domain.market.profile import MarketProfile
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.risk.risk import RiskDecision, RiskResult


def _sql_backend() -> bool:
    """sql 后端才查真库（内存后端=单测隔离，规则记 SKIPPED 不连库）。"""
    try:
        from zhice_os_v2.config.settings import get_settings
        return get_settings().persistence_backend == "sql"
    except Exception:
        return False


def _owner_filters(orm, plan: TradePlan) -> list:
    """持仓归属过滤（Phase B 口径）：owned 计划按 account_id/实例过滤，legacy 不过滤。

    【enforced 门禁观察项（复核 #2，切 enforced 前须处理）】legacy 计划（无
    account_id/user_strategy_id）不过滤 → 同 sv 下其他用户的 owned 持仓会被
    吞并进 legacy 计划的敞口/日损统计（偏严方向：多算别人的仓）。切换 enforced
    多租户生产前，应把 legacy 路径也限定 account_id IS NULL（或按账户维度
    重构基数），避免跨租户敞口串算。
    """
    filters = []
    if getattr(plan, "account_id", None):
        filters.append(orm.PositionModel.account_id == plan.account_id)
    elif getattr(plan, "user_strategy_id", None):
        filters.append(orm.PositionModel.user_strategy_id == plan.user_strategy_id)
    return filters


def _sub_account_snapshot(plan: TradePlan) -> dict | None:
    """子账户（sv+owner 口径）敞口快照：总成本 / 同标的成本 / 净值。

    净值 = 市场初始资金 + 该口径 CLOSED 已实现盈亏（position_sizing 同基数）。
    查询失败 / 非 sql 后端 / 净值<=0 → None（调用方记 SKIPPED 放行——position_sizing
    的 C1t 总天花板已在数量层约束敞口，此处是双保险，数据缺失不硬拒）。

    【enforced 门禁观察项（复核 #1，切 enforced 前须处理）】owned 账户的规则
    基数（净值/日损分母）用的是市场级初始资金 _initial_capital(market)（legacy
    单一虚拟账户口径），而非 v2_user_account.initial_capital 的 per-account
    实际入金——多用户下各账户实际资金不同，市场级基数会把规则算松/算紧。
    切 enforced 前需改为按 plan.account_id 反查账户初始资金作基数。
    """
    if not _sql_backend():
        return None
    try:
        from sqlalchemy import func

        from zhice_os_v2.application.services.account_stats_service import _initial_capital
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        base = [
            orm.PositionModel.market == plan.market,
            orm.PositionModel.strategy_version_id == plan.strategy_version_id,
        ]
        owner = _owner_filters(orm, plan)
        session = get_session()
        try:
            total_cost = session.query(
                func.coalesce(func.sum(
                    orm.PositionModel.open_price * orm.PositionModel.open_qty), 0)
            ).filter(orm.PositionModel.status == "OPEN", *base, *owner).scalar()
            security_cost = session.query(
                func.coalesce(func.sum(
                    orm.PositionModel.open_price * orm.PositionModel.open_qty), 0)
            ).filter(orm.PositionModel.status == "OPEN",
                     orm.PositionModel.ts_code == plan.ts_code, *base, *owner).scalar()
            realized = session.query(
                func.coalesce(func.sum(orm.PositionModel.pnl_amount), 0)
            ).filter(orm.PositionModel.status == "CLOSED", *base, *owner).scalar()
        finally:
            session.close()
        net_value = _initial_capital(plan.market) + float(realized or 0)
        if net_value <= 0:
            return None
        return {
            "total_cost": float(total_cost or 0),
            "security_cost": float(security_cost or 0),
            "net_value": net_value,
        }
    except Exception:
        return None


def _daily_loss_snapshot(plan: TradePlan, profile: MarketProfile | None) -> float | None:
    """子账户当日盈亏（已实现 + 盯市浮动，V1 今日口径）。

    - 当日已实现：CLOSED 且 closed_at（市场时区）为今日的 pnl_amount 合计；
    - 浮动：OPEN 持仓盯市——当日开仓 (现价-开仓价)×qty，否则 (现价-昨收)×qty
      （缺昨收的笔记跳过，与 AccountStatsService._account_today_pnl 同语义）。
    数据缺失/查询失败 → None（调用方记 SKIPPED）。
    """
    if not _sql_backend():
        return None
    try:
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        base = [
            orm.PositionModel.market == plan.market,
            orm.PositionModel.strategy_version_id == plan.strategy_version_id,
        ]
        owner = _owner_filters(orm, plan)
        session = get_session()
        try:
            closed_rows = session.query(
                orm.PositionModel.pnl_amount, orm.PositionModel.closed_at
            ).filter(orm.PositionModel.status == "CLOSED", *base, *owner).all()
            open_rows = session.query(
                orm.PositionModel.ts_code, orm.PositionModel.open_price,
                orm.PositionModel.open_qty, orm.PositionModel.opened_at,
            ).filter(orm.PositionModel.status == "OPEN", *base, *owner).all()
        finally:
            session.close()

        try:
            tz = profile.tz if profile is not None else None
            today = profile.trading_date() if profile is not None else None
        except Exception:
            tz, today = None, None
        if today is None:
            from datetime import UTC, datetime
            today = datetime.now(UTC).date()

        def _as_date(v):
            try:
                if v.tzinfo is None:
                    # naive（sqlite 等方言取回丢 tz）按 UTC 补——库内时间统一存 UTC
                    from datetime import UTC
                    v = v.replace(tzinfo=UTC)
                return v.astimezone(tz).date() if tz is not None else v.date()
            except Exception:
                return None

        realized_today = sum(
            float(p or 0) for p, closed_at in closed_rows
            if closed_at is not None and _as_date(closed_at) == today
        )

        # 盯市（需要现价/昨收，复用 positions router 的批量查价口径）
        codes = sorted({r[0] for r in open_rows})
        prices: dict[str, float] = {}
        pre_closes: dict[str, float] = {}
        if codes:
            from zhice_os_v2.api.routers.positions import _get_latest_prices, _get_pre_closes
            prices = _get_latest_prices(codes, plan.market)
            pre_closes = _get_pre_closes(codes, plan.market)

        floating_today = 0.0
        for ts_code, open_price, open_qty, opened_at in open_rows:
            cur = prices.get(ts_code, float(open_price))
            opened_today = (
                opened_at is not None and _as_date(opened_at) == today
            )
            if opened_today:
                floating_today += (cur - float(open_price)) * float(open_qty)
            else:
                pre = pre_closes.get(ts_code)
                if pre is None:
                    continue  # 缺昨收跳过（与 _account_today_pnl 同语义）
                floating_today += (cur - pre) * float(open_qty)
        return realized_today + floating_today
    except Exception:
        return None


class RiskService:
    def check_plan(self, plan: TradePlan, profile: MarketProfile | None = None,
                   price: float | None = None, turnover: float | None = None) -> RiskDecision:
        # 阻断原因 → BLOCK；仅告警原因（如价格偏高）→ WARN。
        # 此前所有 reason 一律 BLOCK，导致「仅告警」被硬拒、WARN 永不产出。
        block_reasons: list[str] = []
        warn_reasons: list[str] = []
        rule_results: dict = {}
        params = load_risk_params()

        if plan.qty <= 0:
            block_reasons.append("qty_must_be_positive")
        if plan.ref_price <= 0:
            block_reasons.append("price_must_be_positive")

        if profile is not None:
            lo, hi = profile.risk_price_guardrail()
            if price is not None:
                if price < float(lo):
                    block_reasons.append("PRICE_TOO_LOW")
                    rule_results["PRICE_RANGE"] = {"result": "REJECT", "price": str(price)}
                elif price > float(hi):
                    warn_reasons.append("PRICE_HIGH")
                    rule_results["PRICE_RANGE"] = {"result": "WARNING", "price": str(price)}
                else:
                    rule_results["PRICE_RANGE"] = {"result": "PASS", "price": str(price)}

        # ── 快照查询（一次取 snapshot_time + turnover，DATA_FRESHNESS / MIN_LIQUIDITY 共用）──
        # 内存后端（单测）不查真库；两规则分别记 SKIPPED。
        snap_row: tuple | None = None
        snap_error: Exception | None = None
        if _sql_backend():
            try:
                from zhice_os_v2.infrastructure.db import models as _orm
                from zhice_os_v2.infrastructure.db.session import get_session as _gs

                _s = _gs()
                try:
                    snap_row = (
                        _s.query(_orm.LatestSnapshotModel.snapshot_time,
                                 _orm.LatestSnapshotModel.turnover)
                        .filter(_orm.LatestSnapshotModel.symbol == plan.ts_code)
                        .order_by(_orm.LatestSnapshotModel.snapshot_time.desc())
                        .first()
                    )
                finally:
                    _s.close()
            except Exception as e:  # noqa: BLE001 - 由 DATA_FRESHNESS fail-CLOSED 兜底
                snap_error = e

        # ── MIN_LIQUIDITY：turnover 参数优先，缺失时从最新快照取；都无 → SKIPPED 留痕 ──
        if profile is None:
            rule_results["MIN_LIQUIDITY"] = {"result": "SKIPPED", "reason": "no_market_profile"}
        else:
            eff_turnover = turnover if turnover is not None else (
                float(snap_row[1]) if snap_row is not None and snap_row[1] is not None else None
            )
            if eff_turnover is None:
                rule_results["MIN_LIQUIDITY"] = {
                    "result": "SKIPPED", "reason": "no_turnover_data"}
            elif eff_turnover < float(profile.risk_min_turnover()):
                block_reasons.append("LIQUIDITY_INSUFFICIENT")
                rule_results["MIN_LIQUIDITY"] = {"result": "REJECT", "turnover": str(eff_turnover)}
            else:
                rule_results["MIN_LIQUIDITY"] = {"result": "PASS", "turnover": str(eff_turnover)}

        # ── MAX_POSITION_RATIO：plan.max_position_ratio 由策略 spec 透传
        #    （trade_plan_service.create_from_candidate），None=未透传 → SKIPPED 留痕 ──
        if plan.max_position_ratio is None:
            rule_results["MAX_POSITION_RATIO"] = {"result": "SKIPPED",
                                                  "reason": "max_position_ratio_missing"}
        elif plan.max_position_ratio > Decimal("0.10"):
            block_reasons.append("POSITION_RATIO_TOO_HIGH")
            rule_results["MAX_POSITION_RATIO"] = {"result": "REJECT"}
        else:
            rule_results["MAX_POSITION_RATIO"] = {"result": "PASS"}

        # ── PORTFOLIO_TOTAL_EXPOSURE / SINGLE_SECURITY_EXPOSURE（V1 子账户口径恢复）──
        # projected = 现有持仓成本 + 本计划成本（qty×ref_price）；阈值来自
        # settings.risk_params（读取异常已回退默认，见 risk_params.load_risk_params）。
        snap = _sub_account_snapshot(plan)
        plan_cost = float(plan.qty) * float(plan.ref_price)
        if snap is None:
            rule_results["PORTFOLIO_TOTAL_EXPOSURE"] = {
                "result": "SKIPPED", "reason": "exposure_data_unavailable"}
            rule_results["SINGLE_SECURITY_EXPOSURE"] = {
                "result": "SKIPPED", "reason": "exposure_data_unavailable"}
        else:
            net = snap["net_value"]
            total_pct = (snap["total_cost"] + plan_cost) / net * 100
            single_pct = (snap["security_cost"] + plan_cost) / net * 100
            if total_pct > params.max_total_position_pct:
                block_reasons.append("PORTFOLIO_EXPOSURE_EXCEEDED")
                rule_results["PORTFOLIO_TOTAL_EXPOSURE"] = {
                    "result": "REJECT",
                    "limit_pct": str(params.max_total_position_pct),
                    "exposure_pct": str(round(total_pct, 2)),
                }
            else:
                rule_results["PORTFOLIO_TOTAL_EXPOSURE"] = {
                    "result": "PASS", "exposure_pct": str(round(total_pct, 2))}
            if single_pct > params.max_single_position_pct:
                block_reasons.append("SINGLE_SECURITY_OVERWEIGHT")
                rule_results["SINGLE_SECURITY_EXPOSURE"] = {
                    "result": "REJECT",
                    "limit_pct": str(params.max_single_position_pct),
                    "exposure_pct": str(round(single_pct, 2)),
                }
            else:
                rule_results["SINGLE_SECURITY_EXPOSURE"] = {
                    "result": "PASS", "exposure_pct": str(round(single_pct, 2))}

        # ── DAILY_LOSS：当日（已实现+盯市）亏损超 max_daily_loss_pct%（相对初始资金）拒新开仓 ──
        daily_pnl = _daily_loss_snapshot(plan, profile)
        if daily_pnl is None:
            rule_results["DAILY_LOSS"] = {"result": "SKIPPED",
                                          "reason": "daily_pnl_unavailable"}
        else:
            try:
                from zhice_os_v2.application.services.account_stats_service import (
                    _initial_capital,
                )
                initial = _initial_capital(plan.market)
            except Exception:
                initial = 0.0
            limit_amount = initial * params.max_daily_loss_pct / 100
            if initial > 0 and limit_amount > 0 and daily_pnl < -limit_amount:
                block_reasons.append("DAILY_LOSS_EXCEEDED")
                rule_results["DAILY_LOSS"] = {
                    "result": "REJECT",
                    "today_pnl": round(daily_pnl, 2),
                    "limit_pct": str(params.max_daily_loss_pct),
                    "limit_amount": round(limit_amount, 2),
                }
            else:
                rule_results["DAILY_LOSS"] = {
                    "result": "PASS", "today_pnl": round(daily_pnl, 2)}

        # ── DATA_FRESHNESS（V1 T218）：快照 age>120min REJECT；
        # 盘前豁免窗（首段开盘前且 ≤72h，覆盖周末）降 WARNING；>30min WARNING。──
        try:
            if not _sql_backend():
                # 内存后端（单测）不查真库：留痕跳过（此前整键缺失=静默不跑）
                rule_results["DATA_FRESHNESS"] = {"result": "SKIPPED",
                                                  "reason": "memory_backend"}
            else:
                from zhice_os_v2.domain.ontology.objects import utc_now as _now

                # 新鲜度 REJECT 只在【交易时段内】有意义：盘中快照该秒级更新，>120min
                # 说明数据断供。非交易时段（盘后/休市）所有标的快照必然陈旧，此时 REJECT
                # 会误拦——故非交易时段只记 PASS 不拦。
                in_session = True
                if profile is not None:
                    try:
                        _t = _now().astimezone(profile.tz).time()
                        _sessions = profile.sessions()
                        if _sessions and not any(s.open <= _t <= s.close for s in _sessions):
                            in_session = False
                    except Exception:
                        in_session = True

                if snap_error is not None:
                    raise snap_error  # 基础设施故障 → fail-CLOSED（下方 except 捕获）
                if snap_row is None or snap_row[0] is None:
                    warn_reasons.append("NO_SNAPSHOT_DATA")
                    rule_results["DATA_FRESHNESS"] = {"result": "WARNING", "age_min": None}
                else:
                    age_min = (_now() - snap_row[0]).total_seconds() / 60
                    if in_session and age_min > 120:
                        block_reasons.append("DATA_STALE_REJECT")
                        rule_results["DATA_FRESHNESS"] = {"result": "REJECT",
                                                          "age_min": round(age_min)}
                    elif in_session and age_min > 30:
                        warn_reasons.append("SNAPSHOT_STALE")
                        rule_results["DATA_FRESHNESS"] = {"result": "WARNING",
                                                          "age_min": round(age_min)}
                    else:
                        rule_results["DATA_FRESHNESS"] = {"result": "PASS",
                                                          "age_min": round(age_min)}
        except Exception as e:
            # 新鲜度基础设施故障 → fail-CLOSED：未知风险视为阻断
            block_reasons.append("FRESHNESS_CHECK_FAILED")
            rule_results["DATA_FRESHNESS"] = {"result": "REJECT", "error": str(e)[:100]}

        if block_reasons:
            result = RiskResult.BLOCK
        elif warn_reasons:
            result = RiskResult.WARN
        else:
            result = RiskResult.PASS
        return RiskDecision(
            risk_decision_id=f"risk-{plan.plan_id}",
            trade_plan_id=plan.plan_id,
            result=result,
            reasons=block_reasons + warn_reasons,
            rule_results=rule_results,
        )
