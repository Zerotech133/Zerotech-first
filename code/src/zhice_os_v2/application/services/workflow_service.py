"""工作流服务：盘前/盘中/盘后。"""
from __future__ import annotations

import logging
from contextlib import AbstractContextManager, nullcontext
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any, cast

from zhice_os_v2.application.services.approval_center import ApprovalCenter, StrategySwitch
from zhice_os_v2.application.services.learning_service import LearningService
from zhice_os_v2.application.services.replay_service import ReplayService
from zhice_os_v2.application.services.reporting_service import ReportingService
from zhice_os_v2.application.services.risk_service import RiskService
from zhice_os_v2.application.services.strategy_registry_service import StrategyRegistryService
from zhice_os_v2.application.services.trade_plan_service import TradePlanService
from zhice_os_v2.domain.approval.approval import ApprovalStatus
from zhice_os_v2.domain.candidate.candidate import Candidate
from zhice_os_v2.domain.market.registry import get_profile
from zhice_os_v2.domain.portfolio.position import PositionStatus
from zhice_os_v2.domain.strategy.exit_policies import get_exit_policy_for_version
from zhice_os_v2.domain.strategy.spec import RiskSpec
from zhice_os_v2.infrastructure.market_data.kline import fetch_db_bars
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort

if TYPE_CHECKING:
    from zhice_os_v2.application.services.opening_submission_guard import (
        OpeningSubmissionGuard,
    )

# 卖出理由文案（exit_kind → 展示文本）
_EXIT_KIND_TEXT = {
    "TAKE_PROFIT": "止盈离场：达到目标涨幅，锁定利润",
    "STOP_LOSS": "止损离场：跌破止损位，执行风控纪律",
    "TRAILING": "移动止损离场：回撤触及跟踪止损",
    "MANUAL": "人工平仓：手动离场",
    "EOD": "收盘平仓：日终清仓（当日了结）",
    "TIME_STOP": "时间止损：持有超过最大交易日数，到期了结",
    "CHAN_EXIT": "缠论离场：跌破中枢下沿，结构走坏",
}

# 策略类型 → V1 策略版本 id（计划/候选落表关联用，market:stype → sv_id）
_SV_BY_STYPE = {
    "CN:TREND": "v1-cn-sv-1",
    "CN:MEAN_REVERSION": "v1-cn-sv-2",
    "CN:CHAN": "v1-cn-sv-4",
    "CN:CHAN_V2": "v1-cn-sv-5",
    "CN:CHAN_V7": "v1-cn-sv-7",  # STAGING — A/B 观察，不实盘下单
}


def _make_candidate_id(sv: str, ts_code: str, trade_date) -> str:
    """统一 candidate_id 口径：带 sv 唯一，候选池与建计划共用(杜绝双形态)。

    长度护栏(spec §3.1)：candidate_id / plan_id(=plan-{cid}) / request_id(=apr-plan-{cid})
    三列均 String(64)，request_id 最长(+9)，故 candidate_id 须 ≤ 55。实测最长
    sv≈14、ts≈13 → cid≈42，余量充足；此断言防将来超长 sv/ts 静默截断致 id 错配。
    """
    cid = f"cand-{sv}-{ts_code}-{trade_date.isoformat()}"
    if len(cid) > 55:
        raise ValueError(
            f"candidate_id 超长({len(cid)}>55)，request_id 将溢出 String(64): {cid}")
    return cid


def _sv_for_type(market: str, stype: str) -> str:
    """(market, strategy_type) → 当前生效 strategy_version_id（字符串），用于内置策略持仓/授权归属。

    CN 用固定映射（逐字节保持）；其余市场从 v2_strategy(market, strategy_type) 的版本中优先取
    「生效版」（status ∈ PROD/ACTIVE，呼应本函数语义），同类型多生效版时按 sv id 升序取第一。
    多版本时（如 US TREND 曾同时挂 sv-1 生产 + sv-2 测试构建；sv-2 已降级 STAGING）据此稳定
    取生效版 sv-1，而非碰巧的最小 id。若无任何生效版则回退到全部版本按 id 升序取一（保证
    仅有 STAGING/DRAFT 单版本的策略——如各市场 VIBE_EXTERNAL——仍能解析，不误伤）。
    无记录返回 ""（调用方据此跳过该类型，避免产生 sv-0 孤儿持仓）。
    """
    key = f"{market}:{stype}"
    if key in _SV_BY_STYPE:
        return _SV_BY_STYPE[key]
    try:
        from sqlalchemy import select

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session
        session = get_session()
        try:
            def _pick(active_only: bool) -> str:
                q = (
                    select(orm.StrategyVersionModel.strategy_version_id)
                    .join(orm.StrategyModel,
                          orm.StrategyModel.strategy_id == orm.StrategyVersionModel.strategy_id)
                    .where(orm.StrategyModel.market == market,
                           orm.StrategyModel.strategy_type == stype)
                )
                if active_only:
                    # 真正按生效状态筛：只认生产版（PROD/ACTIVE），排除 STAGING/DRAFT。
                    q = q.where(orm.StrategyVersionModel.status.in_(("PROD", "ACTIVE")))
                row = session.execute(
                    q.order_by(orm.StrategyVersionModel.strategy_version_id.asc()).limit(1)
                ).first()
                return row[0] if row else ""
            # 优先取生效版；无生效版才回退全量（避免误伤仅有 STAGING/DRAFT 的单版本策略）。
            return _pick(active_only=True) or _pick(active_only=False)
        finally:
            session.close()
    except Exception:  # noqa: BLE001
        return ""


def _sv_full_id(market: str, num: int) -> str:
    """数字 sv → v1-{mkt}-sv-{n} 完整 ID（无映射回退原始字符串）。"""
    cand = f"v1-{market.lower()}-sv-{num}"
    return cand


def _strategy_type_status(market: str, sv: str) -> str:
    """sv 的版本状态（ACTIVE/STAGING/DRAFT...；查询失败默认 ACTIVE 不误拦）。"""
    try:
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            row = (
                session.query(orm.StrategyVersionModel.status)
                .filter(orm.StrategyVersionModel.strategy_version_id == sv)
                .first()
            )
            return str(row[0]) if row else "ACTIVE"
        finally:
            session.close()
    except Exception:
        return "ACTIVE"


def _set_plan_confidence(plan_id: str, score) -> None:
    """候选评分写入 v2_trade_plan.confidence_level（ORM 列，域对象无此字段）。"""
    if score is None:
        return
    try:
        from sqlalchemy import update as _upd

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session
        session = get_session()
        try:
            session.execute(
                _upd(orm.TradePlanModel).where(orm.TradePlanModel.plan_id == plan_id)
                .values(confidence_level=float(score))
            )
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.debug("set_plan_confidence failed plan_id=%s", plan_id)


def _record_risk(market: str, plan_id: str, risk) -> None:
    """风控检查/决策落表（风控中心页数据源：v2_risk_check + v2_risk_decision）。幂等。"""
    try:
        from zhice_os_v2.config.settings import get_settings
        if get_settings().persistence_backend != "sql":
            return
        from sqlalchemy.dialects.postgresql import insert as pg_insert

        from zhice_os_v2.domain.ontology.objects import utc_now
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        now = utc_now()
        check_id = f"rchk-{plan_id}"
        decision_id = f"rdec-{plan_id}"
        result = str(getattr(risk.result, "value", risk.result))
        warning = result != "PASS"
        rules = risk.rule_results or {}
        session = get_session()
        try:
            session.execute(pg_insert(orm.RiskCheckModel).values(
                check_id=check_id, plan_id=plan_id, market=market,
                check_stage="PRE_TRADE", check_round=1, created_at=now,
            ).on_conflict_do_nothing(index_elements=["check_id"]))
            session.execute(pg_insert(orm.RiskDecisionModel).values(
                decision_id=decision_id, check_id=check_id, market=market,
                decision_result=result, warning_flag=warning,
                rule_results=rules, approver_type="SYSTEM", created_at=now,
            ).on_conflict_do_nothing(index_elements=["decision_id"]))
            session.commit()
        finally:
            session.close()
    except Exception:
        logger.debug("record_risk failed plan_id=%s", plan_id)


def _is_today(opened_at: datetime | None, profile) -> bool:
    """opened_at（市场时区）是否今日。无 opened_at 视为非今日。"""
    if opened_at is None:
        return False
    try:
        return opened_at.astimezone(profile.tz).date() == profile.trading_date()
    except Exception:
        return False


def _time_stop_hit(
    calendar: dict, opened, as_of, max_hold_days: int | None
) -> bool:
    """持有交易日数是否达到时间止损阈值。max_hold_days=None/0 或缺 opened/as_of 恒 False。"""
    if not max_hold_days or opened is None or as_of is None:
        return False
    from zhice_os_v2.domain.market.trading_days import count_trading_days
    opened_d = opened.date() if hasattr(opened, "date") else opened
    as_of_d = as_of.date() if hasattr(as_of, "date") else as_of
    return count_trading_days(calendar, opened_d, as_of_d) >= max_hold_days


def _load_trading_calendar(market: str) -> dict:
    """加载该市场交易日历 {trade_date: is_open}，供时间止损计数。

    出错（含非 SQL 后端/测试隔离环境）返回空 dict；
    空 dict 时 count_trading_days=0，_time_stop_hit 恒 False，行为安全。
    """
    try:
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session
        session = get_session()
        try:
            rows = session.query(
                orm.MarketCalendarModel.trade_date,
                orm.MarketCalendarModel.is_open,
            ).filter(orm.MarketCalendarModel.market == market).all()
            return {r[0]: bool(r[1]) for r in rows}
        finally:
            session.close()
    except Exception:
        return {}


def _load_position_stops(position_ids: list[str]) -> dict[str, Decimal]:
    """v2_position.stop_loss_price 批量读（domain PositionLot 无该字段，走 ORM/SQL 直读）。"""
    if not position_ids:
        return {}
    try:
        from sqlalchemy import select

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            rows = session.execute(
                select(orm.PositionModel.position_id, orm.PositionModel.stop_loss_price)
                .where(orm.PositionModel.position_id.in_(position_ids))
            ).all()
            return {r[0]: r[1] for r in rows if r[1] is not None}
        finally:
            session.close()
    except Exception:
        return {}


# 活跃/可执行计划状态（出场价来源）：排除 CANCELLED/EXPIRED/REJECTED——被裁决作废/
# 过期/驳回的计划止盈止损价是脏源（如 sv-3 已成交计划的低止盈套到 sv-1 持仓 → 每轮误止盈）。
_ACTIVE_PLAN_STATUSES = ("PENDING", "APPROVED", "EXECUTED")


def _load_plan_exits(ts_codes: list[str], market: str) -> dict[tuple[str, str], tuple]:
    """(ts_code, strategy_version_id) → 最新【活跃】trade_plan 的 (stop_loss_price, take_profit_price)。

    两道防污染：
      ① 只取活跃/可执行态计划（_ACTIVE_PLAN_STATUSES），排除 CANCELLED/EXPIRED/REJECTED；
      ② 键含 strategy_version_id：同标的多策略各取本策略计划，杜绝跨策略串价
         （sv-3 的止盈绝不落到 sv-1 持仓上）。
    """
    if not ts_codes:
        return {}
    try:
        from sqlalchemy import and_, func

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            subq = (
                session.query(
                    orm.TradePlanModel.ts_code,
                    orm.TradePlanModel.strategy_version_id,
                    func.max(orm.TradePlanModel.created_at).label("max_created"),
                )
                .filter(
                    orm.TradePlanModel.ts_code.in_(ts_codes),
                    orm.TradePlanModel.market == market,
                    orm.TradePlanModel.plan_status.in_(_ACTIVE_PLAN_STATUSES),
                )
                .group_by(orm.TradePlanModel.ts_code,
                          orm.TradePlanModel.strategy_version_id)
                .subquery()
            )
            # JOIN 同时匹配 (ts_code, sv, created_at=max)：每 (标的,策略) 组取各自最新
            # 活跃计划，避免老计划过时价（如 BTC 6 万时代 tp 对 7.7 万新仓恒触发）误平新仓。
            rows = (
                session.query(
                    orm.TradePlanModel.ts_code,
                    orm.TradePlanModel.strategy_version_id,
                    orm.TradePlanModel.stop_loss_price,
                    orm.TradePlanModel.take_profit_price,
                )
                .join(
                    subq,
                    and_(
                        orm.TradePlanModel.ts_code == subq.c.ts_code,
                        orm.TradePlanModel.strategy_version_id
                        == subq.c.strategy_version_id,
                        orm.TradePlanModel.created_at == subq.c.max_created,
                    ),
                )
                .filter(orm.TradePlanModel.market == market,
                        orm.TradePlanModel.plan_status.in_(_ACTIVE_PLAN_STATUSES))
                .all()
            )
            # 同一 (sv, created_at) 可能多条：保留首条（最新一组）
            out: dict[tuple[str, str], tuple] = {}
            for r in rows:
                key = (r[0], r[1])
                if key not in out and (r[2] is not None or r[3] is not None):
                    out[key] = (r[2], r[3])
            return out
        finally:
            session.close()
    except Exception:
        return {}


def _load_security_names(ts_codes: list[str], market: str) -> dict[str, str]:
    """证券名映射（判 ST 涨跌停幅用）。当前 V2 无 security 主数据表，返回空。"""
    return {}


def _sim_auto(market: str) -> bool:
    """模拟盘自动执行开关。优先读持久化(v2_app_state key='sim_auto')——跨进程：
    API 进程 POST /brokers/simulated 写库，scheduler 进程读库；回退进程内 switch。
    """
    try:
        from zhice_os_v2.api.routers.app_state_store import _load_state
        if bool(_load_state("sim_auto", {}).get(market)):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from zhice_os_v2.api.dependencies import get_switches
        sw = cast("dict[tuple[str, int], StrategySwitch]", get_switches()).get((market, 0))
        return bool(sw and sw.simulated_enabled)
    except Exception:  # noqa: BLE001
        return False


def _closed_today_symbols(market: str, trade_date: date) -> set[str]:
    """当日已平仓的 ts_code 集合（盘前防重开护栏用；避免重开已止损标的）。"""
    try:
        from sqlalchemy import Date as SaDate
        from sqlalchemy import cast, select

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session
        session = get_session()
        try:
            rows = session.execute(
                select(orm.PositionModel.ts_code).where(
                    orm.PositionModel.market == market,
                    orm.PositionModel.status == PositionStatus.CLOSED.value,
                    cast(orm.PositionModel.closed_at, SaDate) == trade_date,
                )
            ).all()
        finally:
            session.close()
        return {r[0] for r in rows if r[0]}
    except Exception:  # noqa: BLE001
        return set()


def _save_position_stop(position_id: str, stop_loss_price: Decimal) -> bool:
    """移动止损写回 v2_position.stop_loss_price。"""
    try:
        from sqlalchemy import update

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            session.execute(
                update(orm.PositionModel)
                .where(orm.PositionModel.position_id == position_id)
                .values(stop_loss_price=stop_loss_price)
            )
            session.commit()
            return True
        finally:
            session.close()
    except Exception:
        return False


def _persist_position_pnl(position_id: str, pnl_amount: Decimal, pnl_pct: Decimal) -> bool:
    """平仓盈亏写回 v2_position.pnl_amount / pnl_pct。"""
    try:
        from sqlalchemy import update

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        session = get_session()
        try:
            session.execute(
                update(orm.PositionModel)
                .where(orm.PositionModel.position_id == position_id)
                .values(pnl_amount=pnl_amount, pnl_pct=pnl_pct)
            )
            session.commit()
            return True
        finally:
            session.close()
    except Exception:
        return False


logger = logging.getLogger(__name__)


class WorkflowService:
    def __init__(
        self,
        db: PersistencePort,
        *,
        opening_guard: OpeningSubmissionGuard | None = None,
    ) -> None:
        self._db = db
        # B2-3 开仓提交门禁：_auto_execute 的带归属 BUY 在 broker 提交边界复核会员。
        # 默认惰性构造真 guard（读真表）；内存 db 测试注入放行桩，避免真 guard 读空表误拒。
        self._opening_guard = opening_guard

    @property
    def opening_guard(self) -> OpeningSubmissionGuard:
        if self._opening_guard is None:
            from zhice_os_v2.application.services.opening_submission_guard import (
                OpeningSubmissionGuard,
            )

            self._opening_guard = OpeningSubmissionGuard()
        return self._opening_guard

    def _opening_context(
        self, user_strategy_id: str | None
    ) -> AbstractContextManager[Any]:
        """B2-3 开仓门禁上下文：带归属 → guard.hold（提交边界复核会员，可抛
        OpeningSubmissionDenied）；无归属（legacy 盘前/盘中）→ nullcontext 不经门禁。"""
        if user_strategy_id:
            return self.opening_guard.hold(user_strategy_id=user_strategy_id)
        return nullcontext()

    # 策略类型 → V1 版本号映射（候选池落库 strategy_version_id 用）

    @staticmethod
    def _prune_candidate_assets(session, orm, pool_id: str, keep_ids: set[str]) -> None:
        """幂等重建：删除本池本轮未出现的旧 assets，避免重跑累积漂移。"""
        from sqlalchemy import select as _select

        assets = session.execute(
            _select(orm.CandidateAssetModel).where(orm.CandidateAssetModel.pool_id == pool_id)
        ).scalars().all()
        for asset in assets:
            if asset.candidate_id not in keep_ids:
                session.delete(asset)

    def _persist_candidate_pool(
        self, market: str, trade_date: date, strategy_type: str, candidates: list[dict]
    ) -> str | None:
        """盘前候选落库：v2_candidate_pool + v2_candidate_asset（幂等，直接 ORM）。"""
        if not candidates:
            return None
        try:
            from zhice_os_v2.domain.ontology.objects import utc_now
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session

            ml = market.lower()
            pool_id = f"{ml}_pool_{trade_date.strftime('%Y%m%d')}_{strategy_type.lower()}"
            # 池 sv 与正式计划同源（_sv_for_type），杜绝硬编码编号约定把不存在的类型
            # （如 US 无 MEAN_REVERSION）撞到别人的真实 sv id。无策略记录即不落库该池，
            # 呼应盘前枚举"无记录就跳过避免孤儿"语义。
            strategy_version_id = _sv_for_type(market, strategy_type)
            if not strategy_version_id:
                logger.info(
                    "skip candidate pool: 无策略记录 %s:%s（不伪造 sv）",
                    market, strategy_type,
                )
                return None
            now = utc_now()
            session = get_session()
            try:
                from sqlalchemy import select as _select

                pool = session.execute(
                    _select(orm.CandidatePoolModel)
                    .where(orm.CandidatePoolModel.pool_id == pool_id)
                ).scalar_one_or_none()
                if pool is None:
                    pool = orm.CandidatePoolModel(
                        pool_id=pool_id,
                        strategy_version_id=strategy_version_id,
                        market=market,
                        pool_date=trade_date,
                        pool_type="PREMARKET",
                        batch_no=1,
                        candidate_count=len(candidates),
                        status="ACTIVE",
                        created_at=now,
                    )
                    session.add(pool)
                else:
                    pool.strategy_version_id = strategy_version_id
                    pool.candidate_count = len(candidates)
                    pool.status = "ACTIVE"
                keep_ids: set[str] = set()
                for c in candidates:
                    candidate_id = (
                        _make_candidate_id(strategy_version_id, c["ts_code"], trade_date)
                    )
                    keep_ids.add(candidate_id)
                    asset = session.execute(
                        _select(orm.CandidateAssetModel)
                        .where(orm.CandidateAssetModel.candidate_id == candidate_id)
                    ).scalar_one_or_none()
                    if asset is None:
                        session.add(orm.CandidateAssetModel(
                            candidate_id=candidate_id,
                            pool_id=pool_id,
                            market=market,
                            ts_code=c["ts_code"],
                            candidate_score=Decimal(str(c.get("score", 0))),
                            candidate_reason=c.get("reason", ""),
                            status="ACTIVE",
                            created_at=now,
                        ))
                    else:
                        asset.pool_id = pool_id
                        asset.candidate_score = Decimal(str(c.get("score", 0)))
                        asset.candidate_reason = c.get("reason", "")
                        asset.status = "ACTIVE"
                self._prune_candidate_assets(session, orm, pool_id, keep_ids)
                pool.candidate_count = len(keep_ids)
                session.commit()
                return pool_id
            finally:
                session.close()
        except Exception:
            logger.exception(
                "persist_candidate_pool failed market=%s strategy=%s date=%s",
                market, strategy_type, trade_date,
            )
            return None

    def _persist_signal_pool(
        self, market: str, trade_date: date, sv: str, candidates: list[dict]
    ) -> str | None:
        """信号型(spec)候选落库：v2_candidate_pool + v2_candidate_asset（幂等，直接 ORM）。

        与 registry 的 _persist_candidate_pool 同构，差异：pool_id 用 sv（每 sv 每交易日一池）、
        pool_type=SIGNAL、直接用已知 sv（spec 版本 sv 必存在，不走 _sv_for_type）。
        让信号型策略也在候选池可见，与 registry 池并列，便于对比。
        """
        if not candidates or not sv:
            return None
        try:
            from zhice_os_v2.domain.ontology.objects import utc_now
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session

            pool_id = f"{market.lower()}_pool_{trade_date.strftime('%Y%m%d')}_spec_{sv}"
            now = utc_now()
            session = get_session()
            try:
                from sqlalchemy import select as _select

                pool = session.execute(
                    _select(orm.CandidatePoolModel)
                    .where(orm.CandidatePoolModel.pool_id == pool_id)
                ).scalar_one_or_none()
                if pool is None:
                    pool = orm.CandidatePoolModel(
                        pool_id=pool_id,
                        strategy_version_id=sv,
                        market=market,
                        pool_date=trade_date,
                        pool_type="SIGNAL",
                        batch_no=1,
                        candidate_count=len(candidates),
                        status="ACTIVE",
                        created_at=now,
                    )
                    session.add(pool)
                else:
                    pool.strategy_version_id = sv
                    pool.candidate_count = len(candidates)
                    pool.status = "ACTIVE"
                keep_ids: set[str] = set()
                for c in candidates:
                    candidate_id = _make_candidate_id(sv, c["ts_code"], trade_date)
                    keep_ids.add(candidate_id)
                    asset = session.execute(
                        _select(orm.CandidateAssetModel)
                        .where(orm.CandidateAssetModel.candidate_id == candidate_id)
                    ).scalar_one_or_none()
                    if asset is None:
                        session.add(orm.CandidateAssetModel(
                            candidate_id=candidate_id,
                            pool_id=pool_id,
                            market=market,
                            ts_code=c["ts_code"],
                            candidate_score=Decimal(str(c.get("score", 0))),
                            candidate_reason=c.get("reason", ""),
                            status="ACTIVE",
                            created_at=now,
                        ))
                    else:
                        asset.pool_id = pool_id
                        asset.candidate_score = Decimal(str(c.get("score", 0)))
                        asset.candidate_reason = c.get("reason", "")
                        asset.status = "ACTIVE"
                self._prune_candidate_assets(session, orm, pool_id, keep_ids)
                pool.candidate_count = len(keep_ids)
                session.commit()
                return pool_id
            finally:
                session.close()
        except Exception:
            logger.exception(
                "persist_signal_pool failed market=%s sv=%s date=%s",
                market, sv, trade_date,
            )
            return None

    def run_pre_market(
        self,
        market: str,
        trade_date: date,
        max_candidates: int = 10,
        strategy_type: str = "TREND",
        strategy_types: list[str] | None = None,
    ) -> dict:
        """盘前执行模式门（Phase B Task 4）。默认 legacy——行为与改造前逐字一致。

        - legacy：直接调用现有执行体 `_run_legacy_pre_market`（逻辑一字不改）。
        - shadow：先只读 `preview_fanout` 记一行指标（fail-open：preview 崩了也绝不改变
          legacy 行为），然后照跑 legacy 体——对外行为不变、写库零增量。
        - enforced：走 `execute_fanout` 新用户隔离路径（默认不启用）。
        """
        from zhice_os_v2.config.settings import get_settings

        mode = get_settings().multi_user_execution_mode
        if mode == "legacy":
            return self._run_legacy_pre_market(
                market, trade_date, max_candidates=max_candidates,
                strategy_type=strategy_type, strategy_types=strategy_types,
            )

        # shadow / enforced 用与 legacy 同口径的类型列表。
        types = list(strategy_types) if strategy_types else [strategy_type]
        from zhice_os_v2.application.services.user_instance_execution_service import (
            UserInstanceExecutionService,
        )
        instance_service = UserInstanceExecutionService(self._db)

        if mode == "shadow":
            try:
                preview = instance_service.preview_fanout(market, trade_date, types)
                logger.info("multi_user_shadow %s", preview.to_metrics())
            except Exception:
                # shadow 绝不改变 legacy 行为：preview 只读，崩了也不影响照跑 legacy。
                logger.exception("multi_user_shadow preview failed (legacy 照跑)")
            return self._run_legacy_pre_market(
                market, trade_date, max_candidates=max_candidates,
                strategy_type=strategy_type, strategy_types=strategy_types,
            )

        # enforced
        return instance_service.execute_fanout(market, trade_date, types)

    def _run_legacy_pre_market(
        self,
        market: str,
        trade_date: date,
        max_candidates: int = 10,
        strategy_type: str = "TREND",
        strategy_types: list[str] | None = None,
    ) -> dict:
        """盘前：多策略 选股→候选落库→计划→风控→授权→(auto执行)。

        strategy_types 为 None 时保持兼容单策略（strategy_type）；
        显式传列表（如调度器 ["TREND","MEAN_REVERSION","CHAN","CHAN_V2"]）则循环执行，
        候选合并返回，approvals 项带 strategy_type。
        """
        # 延迟导入避免应用层 ↔ API 层 import 环（get_switches 是进程级单例）
        from zhice_os_v2.api.dependencies import get_switches

        registry = StrategyRegistryService()
        types = list(strategy_types) if strategy_types else [strategy_type]
        # V1 Step3 策略路由：按市场环境(regime)过滤/排序策略类型。
        # 首选各类型真实版本的 status；查询失败退化为不路由（全跑）。
        try:
            from zhice_os_v2.application.services.regime_service import route_strategies
            version_status = {}
            for st in types:
                sv = _sv_for_type(market, st)
                version_status[st] = _strategy_type_status(market, sv) if sv else "ACTIVE"
            routed = route_strategies(market, types, version_status)
            if routed["enabled"] != types:
                logger.info("pre_market regime routed market=%s regime=%s types=%s -> %s",
                            market, routed["regime_info"].get("regime"), types, routed["enabled"])
            types = routed["enabled"]
            if not types:
                return {"market": market, "trade_date": str(trade_date), "candidates": 0,
                        "plans": [], "approvals": [], "regime_routed_out": True,
                        "regime": routed["regime_info"].get("regime")}
        except Exception:
            logger.debug("regime routing skipped (degraded)", exc_info=True)
        plan_service = TradePlanService(self._db)
        risk_service = RiskService()
        switches = get_switches()
        approval_center = ApprovalCenter(
            switches=cast("dict[tuple[str, str], StrategySwitch]", switches)
        )
        sim_auto = _sim_auto(market)  # 持久化开关(跨进程) 或 进程内 switch
        profile = get_profile(market)
        plans = []
        approvals = []
        pools = []
        total_candidates = 0

        # 每策略独立选股（不共享 max_candidates 池），批量查价合并处理
        strategy_picks: dict[str, list[dict]] = {}
        sv_by_st: dict[str, str] = {}  # st → 真实 strategy_version_id（归属，避免 sv-0 孤儿）
        for st in types:
            sv_str = _sv_for_type(market, st)
            if not sv_str:
                # 该市场无此类型的策略记录 → 跳过（否则持仓会挂到不存在的 sv-0）
                logger.info("pre_market skip %s:%s (无策略记录, 避免孤儿持仓)", market, st)
                continue
            try:
                picks = registry.run(st, trade_date, max_candidates=max_candidates, market=market)
            except KeyError:
                logger.warning("pre_market unknown strategy_type=%s, skipped", st)
                continue
            sv_by_st[st] = sv_str
            strategy_picks[st] = picks
            pool_id = self._persist_candidate_pool(market, trade_date, st, picks)
            if pool_id:
                pools.append({"strategy_type": st, "pool_id": pool_id, "count": len(picks)})
            total_candidates += len(picks)

        # 批量查候选标的最新收盘价（真实市场价，非评分）
        from zhice_os_v2.api.routers.positions import _get_latest_prices
        cand_codes = [c["ts_code"] for picks in strategy_picks.values() for c in picks]
        latest_prices = _get_latest_prices(cand_codes, market) if cand_codes else {}
        # 防重复开仓护栏：跳过"已有 OPEN 持仓 或 当日已平仓"的标的。
        # 幂等关键——盘前若一天多次跑（如 CRYPTO 7×24 每 4h），否则会把已止损平仓的
        # 标的（position_id 按日稳定）重新 save_position 复活成 OPEN，或对已持有标的重复开仓。
        held = {p.ts_code for p in self._db.list_open_positions(market=market)}
        held |= _closed_today_symbols(market, trade_date)
        # P1-9 修复：sim_max_positions 全局持仓数上限（盘前建计划前检查）
        # 计算当前占用：OPEN 持仓 + 当市场 PENDING/APPROVED/EXECUTING 授权（待成交计划）
        from zhice_os_v2.config.settings import get_settings as _get_settings_pm
        _max_positions = _get_settings_pm().sim_max_positions
        _open_count = len(self._db.list_open_positions(market=market))
        _active_approvals_count = sum(
            1 for a in self._db.list_approvals()
            if a.market == market
            and str(a.status) in ("PENDING", "APPROVED", "EXECUTING")
        )
        _total_active = _open_count + _active_approvals_count
        if _total_active >= _max_positions:
            logger.info(
                "sim_max_positions 上限已达: market=%s active=%s/%s (持仓=%s 待成交=%s), 跳过本轮建计划",
                market, _total_active, _max_positions, _open_count, _active_approvals_count,
            )
            return {
                "market": market,
                "trade_date": str(trade_date),
                "candidates": total_candidates,
                "plans": [],
                "approvals": [],
                "pools": pools,
                "position_limit_reached": True,
            }
        # 建计划：多策略 top-3 全部落库后再统一冲突裁决（对齐云端时序）
        built_plans: list[dict] = []
        for st, candidates in strategy_picks.items():
            # 每策略计划 top-3（对齐云端 build_trade_plans_for_pool max_plans=3）：
            # 按 candidate_score 降序取前 3 进入建计划，其余留在候选池不建计划。
            top3 = sorted(candidates, key=lambda c: c.get("score", 0), reverse=True)[:3]
            for c in top3:
                if c["ts_code"] in held:
                    continue
                candidate = Candidate(
                    # sv 直接带真实策略版本(而非空串)：create_from_candidate 首次 INSERT
                    # 即正确 sv。save_trade_plan 的 UPDATE 分支不刷 sv，故 TREND 同 id
                    # 二次落库(UPDATE)曾把 sv 永留空串——空串源头在此,非落库层。
                    candidate_id=_make_candidate_id(sv_by_st.get(st, ""), c["ts_code"], trade_date),
                    strategy_version_id=sv_by_st[st],
                    market=market,
                    ts_code=c["ts_code"],
                    score=float(c.get("score", 0)),
                    reason=c.get("reason", ""),
                )
                market_price = latest_prices.get(c["ts_code"])
                ref_price = Decimal(str(market_price)) if market_price and market_price > 0 else Decimal("100")
                # V1 五重仓位约束（C0 持仓数/C1 预算制/C1t 总天花板/C2 风险预算/C3 现金）
                from zhice_os_v2.application.services.position_sizing_service import (
                    size_new_position,
                )
                qty = size_new_position(
                    self._db, market, sv_by_st[st], c["ts_code"], ref_price,
                )
                if float(qty) <= 0:
                    continue  # 预算满/超上限（V1 语义：预算过滤，非异常）
                plan = plan_service.create_from_candidate(
                    candidate=candidate,
                    risk=RiskSpec(),
                    ref_price=ref_price,
                    qty=Decimal(qty),
                )
                # 计划落表：candidate_id 已带 sv（_make_candidate_id）→ plan_id
                # (plan-{candidate_id}) 天然按策略唯一，无需再打 -{st} 后缀。后缀曾是
                # "落库带后缀 vs approval 引用 base" 双形态的根源，已随 id 唯一化删除。
                plan.strategy_version_id = plan.strategy_version_id or sv_by_st[st]
                # 信心分 = 候选评分（v2_trade_plan.confidence_level，计划页展示）
                self._db.save_trade_plan(plan)
                _set_plan_confidence(plan.plan_id, c.get("score"))
                built_plans.append({"st": st, "c": c, "plan": plan,
                                    "ref_price": ref_price})

        # Step6.5（V1 语义）：多策略计划全部落库后先做冲突裁决，再进风控/授权。
        # 同 ts_code 被多个策略选中 → 保留等级最高(PROD>STAGING>DRAFT)者，其余 CANCELLED。
        from zhice_os_v2.application.services.conflict_resolver_service import (
            resolve_plan_conflicts,
        )
        conflict_result = resolve_plan_conflicts(market)
        cancelled_ids = set(conflict_result.get("cancelled_plan_ids", []))
        if cancelled_ids:
            logger.info("pre_market conflicts resolved market=%s cancelled=%s",
                        market, len(cancelled_ids))

        for _bp in built_plans:
            st, c, plan = _bp["st"], _bp["c"], _bp["plan"]
            ref_price = _bp["ref_price"]
            # Step6.5 败者：跳过风控/授权（已被裁决 CANCELLED）。plan_id 已唯一带 sv，
            # 与 conflict_resolver 返回的落库 plan_id 同形态，直接比对即命中（不再需要
            # 重建策略后缀——后缀已删）。
            if plan.plan_id in cancelled_ids:
                continue
            risk = risk_service.check_plan(
                plan, profile=profile,
                price=float(ref_price) if ref_price else None,
            )
            # 风控结论落表（风控中心页数据源）
            _record_risk(market, plan.plan_id, risk)
            if risk.result == "BLOCK":
                # Task 12：风控拒绝是普通用户业务消息——只落 v2_alert（alert_risk_rejected
                # → send_alert 无 kind，纯落库路径），绝不进企业微信 webhook（webhook
                # 只承载运维白名单 kind，见 notification_health_service）。
                try:
                    from zhice_os_v2.application.services.alert_service import (
                        alert_risk_rejected,
                    )
                    alert_risk_rejected(plan.plan_id,
                                        "; ".join(str(v) for v in (risk.reasons[:3]))
                                        if hasattr(risk, "reasons") else "BLOCK")
                except Exception:
                    pass
            plans.append({"plan_id": plan.plan_id, "ts_code": c["ts_code"],
                          "risk": risk.result, "strategy_type": st})
            # PASS 正常入授权；WARN（如价格偏高，仅告警非阻断）也入授权队列，
            # 但强制人工确认——不因 sim_auto 自动批准。BLOCK 才拦下。
            if risk.result in ("PASS", "WARN"):
                approval = approval_center.create_approval(
                    market=market,
                    strategy_version_id=sv_by_st[st],  # 真实字符串 sv → 持仓正确归属
                    order_intent_id=plan.plan_id,
                    ts_code=c["ts_code"],
                    direction=plan.direction,
                    qty=float(plan.qty),
                    price=float(plan.ref_price),
                    reason=c.get("reason", "策略选股"),
                )
                # 模拟盘开关开启且风控 PASS → 自动批准；WARN 留待人工确认。
                # 分步入场（V1 语义）：股票市场盘前只建 计划+授权(PENDING)，不立即
                # 成交——由 run_intraday 的入场循环按 E1/E2/E3 盘中触发买入。
                # CRYPTO 豁免三条件（追涨门与 RSI<30 均值回归抄底冲突，V1 同），
                # 保持盘前直接执行。
                if market != "CRYPTO" and sim_auto and risk.result == "PASS" \
                        and approval.status == ApprovalStatus.PENDING:
                    approval.approve(actor="auto")  # 先批准，成交等盘中入场循环
                elif market == "CRYPTO" and sim_auto and risk.result == "PASS" \
                        and approval.status == ApprovalStatus.PENDING:
                    approval.approve(actor="auto")
                self._db.save_approval(approval)
                position = None
                if market == "CRYPTO" and sim_auto and approval.status == ApprovalStatus.APPROVED:
                    position = self._auto_execute(approval, market, profile)
                # 同步计划状态(V1 语义)：自动批准的授权 → 计划也应离开 PENDING，
                # 否则 plan_status 永挂 PENDING → "待授权"统计虚高、复盘口径错。
                if approval.status == ApprovalStatus.APPROVED:
                    _new = "EXECUTED" if position is not None else "APPROVED"
                    # plan_id 唯一后 approval.order_intent_id == 落库 plan_id 同形态，
                    # 单次同步即可（不再有 base/后缀两形态各写一次）。已成交标 EXECUTED。
                    self._db.update_trade_plan_status(plan.plan_id, _new)
                approvals.append({
                    "request_id": approval.request_id,
                    "ts_code": approval.ts_code,
                    "direction": approval.direction,
                    "status": approval.status,
                    "auto_executed": position is not None,
                    "position_id": position.position_id if position else None,
                    "strategy_type": st,
                })
        result = {
            "market": market,
            "trade_date": str(trade_date),
            "candidates": total_candidates,
            "plans": plans,
            "approvals": approvals,
        }
        if len(types) > 1 or strategy_types is not None:
            result["strategy_types"] = types
            result["pools"] = pools
        return result

    # 离群价阈值：撮合基准价偏离昨收超过此比例即判脏价拒单（0.5 = 50%）
    _OUTLIER_PRICE_DEVIATION = 0.5

    def _outlier_price_ok(self, fill_base, ts_code: str, market: str) -> bool:
        """撮合基准价合理性校验：偏离昨收 >阈值 → 拒单+告警，返回 False（拦截）。

        取不到昨收 / 价≤0 时放行（无参照不误伤），只在能与昨收对比时拦离群价。
        与 run_intraday 对无行情持仓的 alert_data_stale 拦截同属"脏数据防线"语义。
        """
        try:
            from zhice_os_v2.api.routers.positions import _get_pre_closes
            pc = _get_pre_closes([ts_code], market).get(ts_code)
            if not pc or float(pc) <= 0 or float(fill_base) <= 0:
                return True
            dev = abs(float(fill_base) / float(pc) - 1)
            if dev > self._OUTLIER_PRICE_DEVIATION:
                logger.warning(
                    "outlier price blocked: market=%s ts=%s price=%s pre_close=%s dev=%.2f",
                    market, ts_code, fill_base, pc, dev)
                try:
                    from zhice_os_v2.application.services.alert_service import send_alert
                    # Task 12：离群价拦截是标的级业务消息——send_alert 无 kind 只落
                    # v2_alert，绝不进企业微信 webhook。
                    send_alert("WARN_HIGH", f"离群价拦截 {ts_code}",
                               f"撮合价 {fill_base} 偏离昨收 {pc} 达 {dev:.0%}，"
                               f"疑脏价/错位，拒绝建仓")
                except Exception:
                    logger.debug("outlier alert failed ts=%s", ts_code)
                return False
        except Exception:
            logger.debug("outlier price guard skipped ts=%s", ts_code)
        return True

    def _auto_execute(self, approval, market: str, profile, live_price=None):
        """模拟盘自动建仓：SimulatedBroker 限价单 → ExecutionService.apply_fill。

        live_price：盘中触发时的实时快照价（分步入场用）——撮合基准用它而非
        approval.price（盘前存价，距触发可隔数小时，曾致 14:05 触发仍按 9:25 价
        成交）。不传则沿用 approval.price（CRYPTO 即时执行等场景，价差极小）。
        失败只记 log 不中断盘前循环（单标的撮合失败不应拖垮整轮工作流）。
        """
        # 延迟导入避免应用层 ↔ 基础设施层 import 环
        from zhice_os_v2.application.services.execution_service import ExecutionService
        from zhice_os_v2.application.services.opening_submission_guard import (
            OpeningSubmissionDenied,
        )
        from zhice_os_v2.domain.execution.broker import OrderRequest, OrderSide
        from zhice_os_v2.domain.market.context import MarketContext
        from zhice_os_v2.infrastructure.brokers.simulated import SimulatedBroker

        # 急停生效：熔断激活时拒绝一切自动建仓（此前熔断不被执行链消费，急停无效）
        try:
            from zhice_os_v2.api.routers.circuit_breaker import is_tripped
            if is_tripped():
                logger.warning("circuit breaker active, skip auto-execute ts=%s", approval.ts_code)
                return None
        except Exception as e:
            logger.error("circuit breaker check failed, halting as fail-closed ts=%s: %s",
                         approval.ts_code, e)
            return None  # fail-CLOSED: 熔断检查失败时不允许交易

        try:
            import time

            fill_base = Decimal(str(live_price)) if live_price and float(live_price) > 0 \
                else Decimal(str(approval.price or 0))
            # 离群价护栏：撮合基准价偏离昨收 >阈值 → 判定脏价（symbol↔price 错位/取到
            # 别的币价，如 ZEC 取到 SUI 价致 996% pnl），拒单+告警。合法正数（如 0.65）
            # 单靠 >0 校验会放行，必须与昨收比对才拦得住——这是脏价的可靠兜底防线。
            if not self._outlier_price_ok(fill_base, approval.ts_code, market):
                return None
            # 原子性占位：APPROVED → EXECUTING，防止路由 + 调度器双路重复执行。
            # claim 返回 False 意味着另一路径已领走执行权（EXECUTING/EXECUTED），跳过。
            if not self._db.claim_approval_for_execution(approval.request_id):
                logger.info(
                    "auto_execute: approval %s already EXECUTING/EXECUTED, skip",
                    approval.request_id,
                )
                return None
            # order_id 含时间戳，每次执行均唯一，避免同一 approval_id 多次撮合时
            # position_id = "pos-{order_id}" 冲突导致持仓覆写而非新建。
            order_id = f"{approval.request_id}-sim-{int(time.time())}"
            ctx = MarketContext(
                market=market, profile=profile,
                currency=profile.currency, tz=profile.tz,
            )
            broker = SimulatedBroker()
            order_req = OrderRequest(
                order_id=order_id,
                market=market,
                ts_code=approval.ts_code,
                side=OrderSide.BUY,
                qty=Decimal(str(approval.qty)),
                limit_price=fill_base,
            )
            # B2-3 开仓提交门禁：带归属的自动建仓在 broker 提交边界复核会员。撤销会员用户
            # 的自建私有实例在此被拒（OpeningSubmissionDenied）→ 回滚 claim（EXECUTING→APPROVED）、
            # 不建仓、return None（与未成交同）。无归属（approval.user_strategy_id 空）不经门禁，
            # legacy 盘前/盘中路径完全不受影响（approval 无归属）。
            try:
                with self._opening_context(approval.user_strategy_id):
                    order = broker.place_order(ctx, order_req)
            except OpeningSubmissionDenied as denied:
                logger.warning(
                    "auto_execute 开仓门禁拒绝 approval=%s user_strategy=%s code=%s，"
                    "回滚 claim、不建仓",
                    approval.request_id, approval.user_strategy_id, denied.code,
                )
                self._db.release_approval_claim(approval.request_id)
                return None
            # 持仓归属：数字 sv → v1-{mkt}-sv-{n} 完整 ID（无映射回退原始值）
            pos_sv = str(approval.strategy_version_id)
            if pos_sv.isdigit():
                pos_sv = _sv_full_id(market, approval.strategy_version_id)
            position = ExecutionService(self._db).apply_fill(
                order,
                strategy_version_id=pos_sv,
                market=market,
                ts_code=approval.ts_code,
                # 归属链：结构性 id 修复后 order_intent_id == plan.plan_id，直接取用，
                # 无需再查 OrderIntentModel，落 v2_position.trade_plan_id 修复恒 NULL 缺陷
                trade_plan_id=approval.order_intent_id,
            )
            if position is None:
                logger.warning(
                    "auto_execute not filled: market=%s ts_code=%s order=%s status=%s msg=%s",
                    market, approval.ts_code, order.order_id, order.status, order.message,
                )
                # 回滚 claim（EXECUTING→APPROVED）：未成交不应把承认固着在 EXECUTING
                # 永久占用 sim_max_positions 上限，且当日后续拍可重试。
                self._db.release_approval_claim(approval.request_id)
                return None
            # 执行完成：标记终态 EXECUTED，阻止后续调度器轮询再次执行。
            # 直改状态（非 save_approval）：save_approval 把 EXECUTING 当不可覆写终态会拦下
            # EXECUTING→EXECUTED（致全系统 0 个 EXECUTED、已成交承认永卡 EXECUTING 占 cap）。
            approval.status = ApprovalStatus.EXECUTED
            self._db.mark_approval_executed(approval.request_id)
            # 同步计划状态（V1 语义）：盘中入场循环成交后计划应离开 APPROVED 标 EXECUTED，
            # 否则已成交计划永挂 APPROVED →「待执行计划」统计虚高、复盘口径错（与 pre_market
            # 737 行、spec 1035 行同步逻辑对齐；此前 _auto_execute 独缺此步）。
            if approval.order_intent_id:
                self._db.update_trade_plan_status(approval.order_intent_id, "EXECUTED")
            # 成交落库（fill_id=fill-{order_id}，position_id=pos-{order_id} 去前缀即 order_id）
            self._record_fill(
                market=market,
                ts_code=approval.ts_code,
                side="BUY",
                qty=position.open_qty,
                price=position.open_price,
                approval=approval,
                position=position,
            )
            return position
        except Exception:
            logger.exception(
                "auto_execute failed: market=%s ts_code=%s approval=%s",
                market, approval.ts_code, approval.request_id,
            )
            # claim 之后异常同样需回滚，否则承认固着 EXECUTING 占坑（fail-safe，异常吞掉）。
            try:
                self._db.release_approval_claim(approval.request_id)
            except Exception:
                logger.error("release_approval_claim failed for %s", approval.request_id)
            return None

    def run_spec_signals(self, market: str, frequency: str = "intraday") -> dict:
        """信号型 spec 策略执行（分周期，与 registry 策略并存）。

        取 ACTIVE 且 execution.frequency==frequency 的策略版本，在各自 timeframe bar 上
        按 spec.rules 评估 enter/exit：enter 且空仓→开仓链；exit 且持仓→平仓+复盘。
        幂等：enter 前查该 (sv,ts) 无 OPEN；候选/计划/授权 id 带时间桶，跨 bar 不复活。
        """
        from zhice_os_v2.application.services.candidate_service import evaluate_signal
        from zhice_os_v2.infrastructure.market_data.kline import (
            fetch_db_minute_bars,
        )

        # 按 timeframe 分类周期（比 execution.frequency 可靠——很多 spec 未填 frequency）：
        # sub-daily(1m/5m/…)=intraday；1d=daily。
        _intraday_tf = {"1m", "5m", "15m", "30m", "60m"}

        def _bucket(v) -> str:
            tf = str(getattr(v.spec, "timeframe", "1d") or "1d")
            return "intraday" if tf in _intraday_tf else "daily"

        versions = [
            v for v in self._db.list_strategy_versions()
            if str(v.status) == "ACTIVE" and v.market == market and _bucket(v) == frequency
        ]
        if not versions:
            return {"market": market, "frequency": frequency, "strategies": 0,
                    "entered": [], "exited": []}

        profile = get_profile(market)
        open_positions = self._db.list_open_positions(market=market)
        replay_service = ReplayService(self._db)
        learning_service = LearningService(self._db)
        entered: list[dict] = []
        exited: list[dict] = []
        pool_by_sv: dict[str, list[dict]] = {}

        for version in versions:
            spec = version.spec
            sv = version.strategy_version_id
            tf = getattr(spec, "timeframe", "1d") or "1d"
            windows = [int(f.params.get("window", 20)) for f in spec.factors] or [20]
            win = max(windows) * 3 + 10  # RSI 等需足够历史
            held = {p.ts_code: p for p in open_positions if p.strategy_version_id == sv}
            for ts in spec.universe.get("codes", []):
                bars = (fetch_db_minute_bars(ts, tf, win) if frequency == "intraday"
                        else fetch_db_bars(ts, "day", win))
                closes = [float(b["close"]) for b in bars if b.get("close")] if bars else None
                signal = evaluate_signal(spec, closes)
                if signal is None or not closes:
                    continue
                px = Decimal(str(closes[-1]))
                if px <= 0:
                    continue
                if signal == "exit" and ts in held:
                    position = held[ts]
                    # T+1 铁律：CN 当日开仓当日不可卖（与 run_intraday 口径一致）
                    if not profile.same_day_sell_allowed and _is_today(position.opened_at, profile):
                        logger.info(
                            "spec_exit blocked T+1: market=%s ts=%s opened=%s",
                            market, ts, position.opened_at,
                        )
                        continue
                    # can_sell：停牌/跌停封死不可卖
                    from zhice_os_v2.api.routers.positions import _get_pre_closes
                    from zhice_os_v2.domain.execution.market_rules import can_sell as _can_sell
                    _pre_close = _get_pre_closes([ts], market).get(ts)
                    _sec_name = _load_security_names([ts], market).get(ts)
                    _sellable, _why = _can_sell(float(px), _pre_close, ts, _sec_name, market=market)
                    if not _sellable:
                        logger.info(
                            "spec_exit blocked can_sell: market=%s ts=%s reason=%s",
                            market, ts, _why,
                        )
                        continue
                    self._close_position(position, px, exit_kind="SIGNAL_EXIT")
                    rep = replay_service.build_replay_case(position)
                    if rep:
                        learning_service.record_from_replay(rep)
                    exited.append({"ts_code": ts, "strategy_version_id": sv})
                elif signal == "enter" and ts not in held:
                    # 收集候选落池（候选≠建仓：即便预算满被过滤也算被信号选中）
                    _tf = getattr(spec, "timeframe", "1d") or "1d"
                    pool_by_sv.setdefault(sv, []).append(
                        {"ts_code": ts, "score": 0.0, "reason": f"信号入场({_tf})"}
                    )
                    res = self._signal_entry(market, profile, sv, ts, px, spec)
                    if res:
                        entered.append(res)
        # 信号候选落库（每 sv 每交易日一池，pool_type=SIGNAL），与 registry 池并列便于对比
        _td = profile.trading_date()
        pools: list[str] = []
        for _sv, _cands in pool_by_sv.items():
            _pid = self._persist_signal_pool(market, _td, _sv, _cands)
            if _pid:
                pools.append(_pid)
        logger.info(
            "spec_signals market=%s freq=%s strategies=%s entered=%s exited=%s pools=%s",
            market, frequency, len(versions), len(entered), len(exited), pools,
        )
        return {"market": market, "frequency": frequency, "strategies": len(versions),
                "entered": entered, "exited": exited, "pools": pools}

    def _signal_entry(self, market: str, profile, sv: str, ts_code: str, ref_price, spec):
        """信号入场：候选(时间桶唯一)→计划→风控→授权→(sim_auto&PASS)自动执行。"""
        from datetime import datetime

        from zhice_os_v2.api.dependencies import get_switches
        bucket = datetime.now(UTC).strftime("%Y%m%d%H%M")
        # V1 五重仓位约束（与 registry 盘前链同一口径；预算满 → 跳过该信号）
        from zhice_os_v2.application.services.position_sizing_service import (
            size_new_position,
        )
        qty = size_new_position(self._db, market, sv, ts_code, ref_price)
        if float(qty) <= 0:
            return {"ts_code": ts_code, "strategy_version_id": sv,
                    "plan_id": None, "risk": "BUDGET_FILTERED"}
        candidate = Candidate(
            candidate_id=f"cand-{sv}-{ts_code}-{bucket}",
            strategy_version_id=sv, market=market, ts_code=ts_code,
            score=0.0, reason=f"信号入场({getattr(spec, 'timeframe', '1d')})",
        )
        plan = TradePlanService(self._db).create_from_candidate(
            candidate=candidate, risk=spec.risk, ref_price=ref_price, qty=Decimal(qty))
        plan.strategy_version_id = sv
        risk = RiskService().check_plan(plan, profile=profile, price=float(ref_price))
        _record_risk(market, plan.plan_id, risk)
        if risk.result not in ("PASS", "WARN"):
            return {"ts_code": ts_code, "strategy_version_id": sv,
                    "plan_id": plan.plan_id, "risk": risk.result}
        switches = get_switches()
        sim_auto = _sim_auto(market)
        approval = ApprovalCenter(
            switches=cast("dict[tuple[str, str], StrategySwitch]", switches)
        ).create_approval(
            market=market, strategy_version_id=sv, order_intent_id=plan.plan_id,
            ts_code=ts_code, direction=plan.direction, qty=float(plan.qty),
            price=float(plan.ref_price), reason=candidate.reason,
        )
        if sim_auto and risk.result == "PASS" and approval.status == ApprovalStatus.PENDING:
            approval.approve(actor="auto")
        self._db.save_approval(approval)
        position = None
        if sim_auto and approval.status == ApprovalStatus.APPROVED:
            position = self._auto_execute(approval, market, profile)
        # 同步计划状态（此前 spec 信号路径整段漏同步 → plan 永挂 PENDING）：自动批准的
        # 授权 → 计划离开 PENDING，已成交标 EXECUTED，否则 APPROVED。spec 用单一 plan_id
        # （无策略后缀），approval.order_intent_id == plan.plan_id，同步目标一致。
        if approval.status == ApprovalStatus.APPROVED:
            self._db.update_trade_plan_status(
                plan.plan_id, "EXECUTED" if position is not None else "APPROVED")
        return {"ts_code": ts_code, "strategy_version_id": sv, "plan_id": plan.plan_id,
                "risk": risk.result, "approval": approval.request_id,
                "position_id": position.position_id if position else None}

    def run_intraday(self, market: str) -> dict:
        """盘中快照（V1 intra_day_workflow 对齐）：
        1. 拉持仓最新价/昨收
        2. 卖点监控：止损/止盈触发平仓（can_sell 可成交性判定，不可成交记 blocked 顺延）；
           移动止损：浮盈 ≥8% 时止损上移至 开仓价*(1+4%)（只上移不下移，写回 DB）
        3. T+1 锁：CN 当日开仓的持仓跳过（可卖=0）
        4. 每笔平仓生成 replay + learning
        5. 分步入场（V1 语义，非 CRYPTO）：当日已批准未成交的授权 → E1/E2/E3 评估 → 触发即买
        """
        from zhice_os_v2.api.routers.positions import _get_latest_prices, _get_pre_closes
        from zhice_os_v2.domain.execution.market_rules import can_sell

        profile = get_profile(market)
        entry_result = self._run_entry_loop(market, profile) if market != "CRYPTO" else []
        positions = self._db.list_open_positions(market=market)
        if not positions:
            return {
                "market": market, "checked": 0, "stop_loss_triggered": [],
                "take_profit_triggered": [], "trailing_updated": [], "blocked": [],
                "replay_created": 0,
                "time_stop_triggered": [], "chan_exit_triggered": [],
            }

        ts_codes = sorted({p.ts_code for p in positions})
        prices = _get_latest_prices(ts_codes, market)
        pre_closes = _get_pre_closes(ts_codes, market)
        pos_stops = _load_position_stops([p.position_id for p in positions])
        plan_exits = _load_plan_exits(ts_codes, market)  # ts_code -> (stop_loss, take_profit)
        names = _load_security_names(ts_codes, market)
        # 止损接线：settings.risk_params.stop_loss_pct 全局覆盖（设置页百分数 8.0 →
        # 小数 0.08，risk_params 内换算）；每轮读一次（跨进程实时生效），不可用时为
        # None → 各持仓回退策略级硬编码（policy.stop_loss_pct，现行 0.04~0.07）。
        # 仅影响"无已存止损价"的兜底现算：pos_stops/plan_exits 的历史止损价不变。
        from zhice_os_v2.application.services.risk_params import settings_stop_loss_override
        _sl_override = settings_stop_loss_override()

        stop_loss_triggered: list[str] = []
        take_profit_triggered: list[str] = []
        trailing_updated: list[dict] = []
        blocked: list[dict] = []
        time_stop_triggered: list[str] = []
        chan_exit_triggered: list[str] = []
        replay_created = 0
        replay_service = ReplayService(self._db)
        learning_service = LearningService(self._db)
        _cal: dict | None = None  # 惰性加载：仅 max_hold_days 策略首次用到时加载

        for position in positions:
            price = prices.get(position.ts_code)
            if price is None or price <= 0:
                blocked.append({"ts_code": position.ts_code, "reason": "无行情(价≤0)"})
                # V1 数据陈旧告警（WARN_LOW 频控；持仓标的无行情值得知道）。
                # Task 12：标的级业务消息——alert_data_stale 只落 v2_alert，不进
                # 企业微信 webhook。
                try:
                    from zhice_os_v2.application.services.alert_service import (
                        alert_data_stale,
                    )
                    alert_data_stale(position.ts_code, "盘中快照缺失/价≤0")
                except Exception:
                    pass
                continue
            current = Decimal(str(price))
            pre_close = pre_closes.get(position.ts_code)

            # T+1 铁律：当日开仓的批次当日不可卖（止盈/止损都锁定；移动止损价仍可上移）
            if not profile.same_day_sell_allowed and _is_today(position.opened_at, profile):
                blocked.append({
                    "ts_code": position.ts_code,
                    "reason": f"T+1锁(当日开仓 {position.opened_at:%Y-%m-%d} 不可卖)",
                })
                continue

            # 按持仓所属策略取出场参数（对齐云端 exit_policies；未命中走默认口径）
            policy = get_exit_policy_for_version(position.strategy_version_id, self._db)
            # 兜底止损的止损幅度：settings 覆盖优先，回退策略硬编码（见轮首 _sl_override 注释）
            _policy_stop_loss = _sl_override if _sl_override is not None \
                else policy.stop_loss_pct
            # 计划出场价按 (ts_code, 持仓所属 sv) 精确匹配——取不到本策略活跃计划则
            # 一律走持仓自身 policy 现算兜底（open_price×(1±pct)），杜绝跨策略/作废计划污染。
            plan_stop, plan_tp = plan_exits.get(
                (position.ts_code, position.strategy_version_id), (None, None))
            stop = pos_stops.get(position.position_id) or plan_stop or \
                (position.open_price * (Decimal("1") - _policy_stop_loss)
                 if position.open_price else None)
            take_profit = plan_tp or (
                position.open_price * (Decimal("1") + policy.take_profit_pct)
                if position.open_price else None)

            # 止盈优先（V1 口径：先判止盈再判止损）
            if take_profit is not None and current >= take_profit:
                sellable, why = can_sell(
                    float(current), pre_close, position.ts_code,
                    names.get(position.ts_code), market=market)
                if not sellable:
                    blocked.append({"ts_code": position.ts_code, "reason": f"止盈触发但{why}"})
                else:
                    self._close_position(position, current, exit_kind="TAKE_PROFIT")
                    take_profit_triggered.append(position.ts_code)
                    replay = replay_service.build_replay_case(position)
                    if replay:
                        learning_service.record_from_replay(replay)
                        replay_created += 1
                continue  # 已平仓/已记录，无需再移动止损

            # 移动止损（云端语义）：启用且浮盈≥activate → 止损上移至 现价×(1−trail_pct)，只上移不下移
            if (policy.use_trailing and position.open_price and position.open_price > 0):
                gain = (current - position.open_price) / position.open_price
                if gain >= policy.trailing_activate_pct:
                    new_stop = current * (Decimal("1") - policy.trailing_trail_pct)
                    old_stop = stop if stop is not None else Decimal("0")
                    if new_stop > old_stop:
                        if _save_position_stop(position.position_id, new_stop):
                            stop = new_stop
                            trailing_updated.append({
                                "position_id": position.position_id,
                                "ts_code": position.ts_code,
                                "old_stop": old_stop,
                                "new_stop": new_stop,
                            })

            # 止损触发（用更新后的 stop）
            if stop is not None and current <= stop:
                sellable, why = can_sell(
                    float(current), pre_close, position.ts_code,
                    names.get(position.ts_code), market=market)
                if not sellable:
                    blocked.append({"ts_code": position.ts_code, "reason": f"止损触发但{why}"})
                else:
                    self._close_position(position, current, exit_kind="STOP_LOSS")
                    stop_loss_triggered.append(position.ts_code)
                    # V1 告警接线：止损成交即记（WARN_HIGH，300s 频控）。
                    # Task 12：止损是普通用户业务消息——alert_stop_loss 只落 v2_alert
                    # （用户定向推送走 outbox/成交扇出，与本告警出口无关），绝不进
                    # 企业微信 webhook。
                    try:
                        from zhice_os_v2.application.services.alert_service import (
                            alert_stop_loss,
                        )
                        alert_stop_loss(position.ts_code, float(current), float(stop))
                    except Exception:
                        pass
                    replay = replay_service.build_replay_case(position)
                    if replay:
                        learning_service.record_from_replay(replay)
                        replay_created += 1
                continue  # 止损已触发（可成交或受阻），不再检查时间止损

            # 时间止损（云端语义）：正常止盈/止损未触发，持有达 max_hold_days 交易日即了结
            if policy.max_hold_days:
                if _cal is None:
                    _cal = _load_trading_calendar(market)
                if _time_stop_hit(_cal, position.opened_at, profile.trading_date(),
                                  policy.max_hold_days):
                    sellable, why = can_sell(
                        float(current), pre_close, position.ts_code,
                        names.get(position.ts_code), market=market)
                    if sellable:
                        self._close_position(position, current, exit_kind="TIME_STOP")
                        time_stop_triggered.append(position.ts_code)
                        replay = replay_service.build_replay_case(position)
                        if replay:
                            learning_service.record_from_replay(replay)
                            replay_created += 1
                    else:
                        blocked.append({"ts_code": position.ts_code,
                                        "reason": f"时间止损触发但{why}"})
                    continue

            # 动态出场（CHAN_ZS）：仅 CHAN_V7 策略（policy.dynamic_stop="CHAN_ZS"），
            # 其余策略 dynamic_stop=None 不进入此分支，绝不影响 CHAN 及其他持仓。
            # 只在止损/止盈/时间止损均未触发时才执行（上方各分支均含 continue）。
            if policy.dynamic_stop == "CHAN_ZS" and position.open_price:
                try:
                    import pandas as _pd

                    from zhice_os_v2.domain.strategy.strategies.chan.dynamic_exit import (
                        evaluate_position_pivot_exit,
                    )
                    _bars = fetch_db_bars(position.ts_code, "day", limit=300)
                    if _bars:
                        _df = _pd.DataFrame([{
                            "dt": _pd.Timestamp(b["time"]),
                            "open": b["open"],
                            "high": b["high"],
                            "low": b["low"],
                            "close": b["close"],
                            "volume": b["volume"],
                        } for b in _bars])
                        if evaluate_position_pivot_exit(
                                position.ts_code, _df, float(current)):
                            sellable, why = can_sell(
                                float(current), pre_close, position.ts_code,
                                names.get(position.ts_code), market=market)
                            if sellable:
                                self._close_position(
                                    position, current, exit_kind="CHAN_EXIT")
                                chan_exit_triggered.append(position.ts_code)
                                replay = replay_service.build_replay_case(position)
                                if replay:
                                    learning_service.record_from_replay(replay)
                                    replay_created += 1
                            else:
                                blocked.append({
                                    "ts_code": position.ts_code,
                                    "reason": f"中枢下沿出场触发但{why}",
                                })
                except Exception:
                    logger.warning(
                        "chan_zs_exit failed ts=%s", position.ts_code, exc_info=True)

        return {
            "market": market,
            "checked": len(positions),
            "stop_loss_triggered": stop_loss_triggered,
            "take_profit_triggered": take_profit_triggered,
            "trailing_updated": trailing_updated,
            "blocked": blocked,
            "replay_created": replay_created,
            "time_stop_triggered": time_stop_triggered,
            "chan_exit_triggered": chan_exit_triggered,
            "entries": entry_result,
        }

    def _run_entry_loop(self, market: str, profile) -> list[dict]:
        """分步入场循环（V1 intra_day_workflow._run_entry_loop 移植，非 CRYPTO）。

        当日已批准(APPROVED)未成交的授权 → E1 价格带/E2 日内走强/E3 动能延续
        三条件全满足才触发买入；每计划最多 MAX_ENTRIES_PER_PLAN 次成交（首建+一次
        加仓，加仓不向下摊薄）；熔断激活全部暂停；can_buy 拦停牌/涨停封死。
        E3 参照价 = 先读旧快照再拉新（顺序保持 V1）。
        """
        from datetime import UTC, datetime

        from zhice_os_v2.api.routers.positions import _get_pre_closes
        from zhice_os_v2.domain.execution.entry_signal import evaluate_entry
        from zhice_os_v2.domain.execution.market_rules import can_buy

        try:
            from zhice_os_v2.api.routers.circuit_breaker import is_tripped
            if is_tripped():
                return []  # 熔断激活：暂停一切新开仓（卖点监控不受影响）
        except Exception as e:
            logger.error("circuit breaker check failed in entry_loop, halting as fail-closed: %s", e)
            return []  # fail-CLOSED: 熔断检查失败时不允许新开仓

        today = datetime.now(UTC).astimezone(profile.tz).date()
        session = None
        try:
            from zhice_os_v2.config.settings import get_settings
            if get_settings().persistence_backend != "sql":
                return []  # 内存后端（单测）不查真库授权（此前直连导致测试读到生产数据）

            from zhice_os_v2.infrastructure.db.session import get_session

            session = get_session()
            approvals = self._query_entry_approvals(session, market, today, profile)
            if not approvals:
                return []
            ts_codes = sorted({a.ts_code for a in approvals})
            prices, snap_times = self._snapshot_price_time(ts_codes, market)
            pre_closes = _get_pre_closes(ts_codes, market)
            day_opens = self._day_opens(ts_codes, market)
            # E3 参照价（V1 语义：上一监控周期的价格）：上一拍快照价——用分钟线上一根
            # 收盘近似（快照表只存 current 行，无历史拍）。若无分钟线回退现价(=E3 恒过，
            # 与此前行为一致不更严)。
            prev_prices = self._prev_tick_prices(ts_codes, market) or prices

            entered: list[dict] = []
            now_hhmm = datetime.now(UTC).astimezone(profile.tz).strftime("%H:%M")
            sim_auto = _sim_auto(market)
            if not sim_auto:
                return []
            entry_counts = self._today_entry_counts(market, today)
            # 加仓不摊薄参照：今日该标的已有 BUY 成交 → 上次成交价（v2_fill_record）
            last_fills = self._today_last_fill_prices(market, today)
            # C0 持仓数上限盘中复检（P1-G）：盘前 size_new_position 只在建计划时查一次
            # distinct 只数，但盘前只建计划+授权不落仓 → 同一轮多个不同新候选各自看到
            # distinct<上限全放行 → 盘中入场循环全执行 → 顶穿 per-sv≤DEFAULT_MAX_POSITIONS。
            # 此处按 sv 建运行时 distinct 快照，逐授权复检；每成功建仓即把标的并入该 sv
            # 集合，使同一轮后续授权能感知刚建的仓。只判 distinct 只数（与盘前 C0 同源），
            # 不重跑 C1/C2/C3；加仓到已持标的（ts_code 已在集合内）不受此闸限制。
            from zhice_os_v2.application.services.position_sizing_service import (
                DEFAULT_MAX_POSITIONS,
            )
            held_by_sv: dict[str, set[str]] = {}
            for _p in self._db.list_open_positions(market=market):
                held_by_sv.setdefault(str(_p.strategy_version_id), set()).add(_p.ts_code)
            for a in approvals:
                live = prices.get(a.ts_code)
                if not live or live <= 0:
                    continue
                already = entry_counts.get(a.order_intent_id or a.request_id, 0)
                if already >= self.MAX_ENTRIES_PER_PLAN:
                    continue
                # V1 加仓纪律：二次买入不向下摊薄（现价低于上次成交价 → 放弃加仓）
                last_fill = last_fills.get(a.ts_code)
                if already >= 1 and last_fill and float(live) < last_fill:
                    continue
                # C0 盘中复检：新标的（不在该 sv 已持集合内）且该 sv distinct 已达上限
                # → 拦截（防多个不同计划各开一只顶穿总数）。sv 形态与 _auto_execute 落仓
                # 一致地归一（纯数字 → v1-{mkt}-sv-{n}），避免与快照键错配漏检。
                sv_key = str(a.strategy_version_id)
                if sv_key.isdigit():
                    sv_key = _sv_full_id(market, a.strategy_version_id)
                held = held_by_sv.setdefault(sv_key, set())
                if a.ts_code not in held and len(held) >= DEFAULT_MAX_POSITIONS:
                    logger.info(
                        "C0 持仓数上限拦截: market=%s sv=%s ts=%s distinct=%s>=%s",
                        market, sv_key, a.ts_code, len(held), DEFAULT_MAX_POSITIONS)
                    continue
                ok, why = evaluate_entry(
                    live_price=float(live),
                    pre_close=pre_closes.get(a.ts_code),
                    day_open=day_opens.get(a.ts_code),
                    prev_price=prev_prices.get(a.ts_code),
                    now_hhmm=now_hhmm, market=market,
                )
                if not ok:
                    continue
                # V1 can_buy：传快照时间使停牌拦截生效（>30min 未更新买不进）
                buyable, bwhy = can_buy(float(live), pre_closes.get(a.ts_code),
                                        a.ts_code, None,
                                        snapshot_time=snap_times.get(a.ts_code),
                                        market=market)
                if not buyable:
                    continue
                # 撮合基准 = 触发时实时价（曾按盘前 approval.price 成交，价差可达 5%）
                position = self._auto_execute(a, market, profile, live_price=live)
                if position is not None:
                    # 刚建的仓并入该 sv 集合，使同一轮后续授权感知（防一轮内连开多只顶穿）
                    held.add(a.ts_code)
                    entered.append({"ts_code": a.ts_code, "position_id": position.position_id,
                                    "reason": why})
            if entered:
                logger.info("entry_loop market=%s entered=%s", market,
                            [e["ts_code"] for e in entered])
            return entered
        except Exception:
            logger.exception("entry_loop failed market=%s", market)
            return []
        finally:
            if session is not None:
                session.close()

    MAX_ENTRIES_PER_PLAN = 2  # V1: 首建仓 + 一次加仓

    def _snapshot_price_time(self, ts_codes: list[str], market: str) -> tuple[dict, dict]:
        """(现价映射, 快照时间映射)——can_buy 停牌拦截需要 snapshot_time。"""
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        prices: dict[str, float] = {}
        times: dict[str, object] = {}
        try:
            session = get_session()
            try:
                rows = (
                    session.query(orm.LatestSnapshotModel.symbol,
                                  orm.LatestSnapshotModel.price,
                                  orm.LatestSnapshotModel.snapshot_time)
                    .filter(orm.LatestSnapshotModel.market == market,
                            orm.LatestSnapshotModel.symbol.in_(ts_codes))
                    .all()
                )
                for sym, px, st in rows:
                    if px is not None and float(px) > 0:
                        prices[sym] = float(px)
                        times[sym] = st
            finally:
                session.close()
        except Exception:
            pass
        return prices, times

    def _prev_tick_prices(self, ts_codes: list[str], market: str) -> dict[str, float]:
        """上一监控周期价格（E3 参照）：取最新 1 分钟 bar 的前一根收盘。

        快照表只有 current 行无历史，用分钟线倒数第二根近似"上一拍"。
        无分钟线返回空 dict（调用方回退现价 → E3 恒过，不比之前更严）。
        """
        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        out: dict[str, float] = {}
        try:
            session = get_session()
            try:
                for sym in ts_codes:
                    rows = (
                        session.query(orm.BarMinuteModel.close)
                        .filter(orm.BarMinuteModel.market == market,
                                orm.BarMinuteModel.symbol == sym)
                        .order_by(orm.BarMinuteModel.bar_time.desc())
                        .limit(2)
                        .all()
                    )
                    if len(rows) >= 2 and rows[1][0] is not None:
                        out[sym] = float(rows[1][0])
            finally:
                session.close()
        except Exception:
            pass
        return out

    def _today_last_fill_prices(self, market: str, today) -> dict[str, float]:
        """今日各标的最后一次 BUY 成交价（加仓不摊薄参照）。"""
        from datetime import datetime

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        lo = datetime.combine(today, datetime.min.time())
        out: dict[str, float] = {}
        try:
            session = get_session()
            try:
                rows = (
                    session.query(orm.FillRecordModel.ts_code,
                                  orm.FillRecordModel.fill_price,
                                  orm.FillRecordModel.fill_time)
                    .join(orm.ExecutionRecordModel,
                          orm.FillRecordModel.execution_id == orm.ExecutionRecordModel.execution_id)
                    .join(orm.OrderRequestModel,
                          orm.ExecutionRecordModel.request_id == orm.OrderRequestModel.request_id)
                    .join(orm.OrderIntentModel,
                          orm.OrderRequestModel.intent_id == orm.OrderIntentModel.intent_id)
                    .filter(orm.FillRecordModel.market == market,
                            orm.OrderIntentModel.intent_side == "BUY",
                            orm.FillRecordModel.fill_time >= lo)
                    .order_by(orm.FillRecordModel.fill_time.asc())
                    .all()
                )
                for sym, px, _t in rows:  # asc 扫描 → 最后一条即最新成交
                    if px is not None:
                        out[sym] = float(px)
            finally:
                session.close()
        except Exception:
            pass
        return out

    def _day_opens(self, ts_codes: list[str], market: str) -> dict[str, float]:
        """当日开盘价（今日日K open；无今日K回退快照现价=视为走强）。"""
        from datetime import UTC, datetime

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        profile = get_profile(market)
        today = datetime.now(UTC).astimezone(profile.tz).date()
        out: dict[str, float] = {}
        try:
            session = get_session()
            try:
                rows = (
                    session.query(orm.BarDailyModel.symbol, orm.BarDailyModel.open)
                    .filter(orm.BarDailyModel.market == market,
                            orm.BarDailyModel.trade_date == today,
                            orm.BarDailyModel.symbol.in_(ts_codes))
                    .all()
                )
                out = {r[0]: float(r[1]) for r in rows if r[1]}
            finally:
                session.close()
        except Exception:
            pass
        return out

    def _query_entry_approvals(self, session, market: str, today, profile):
        """盘中入场候选授权：当日 APPROVED+BUY，且对应计划未作废（重做B第二重纵深）。

        outerjoin v2_trade_plan：plan_id 唯一后 order_intent_id == plan_id 同形态命中，
        排除 plan_status IN (CANCELLED/EXPIRED/REJECTED)——冲突败者/过期/驳回计划的授权
        即便漏回退（第一重 conflict_resolver 未覆盖）也不入场。无对应计划的历史授权
        （outerjoin 空侧）仍放行，保持既有行为。
        """
        from datetime import datetime

        from sqlalchemy import or_ as _or

        from zhice_os_v2.infrastructure.db import models as orm
        _DEAD_PLAN = ("CANCELLED", "EXPIRED", "REJECTED")
        return (
            session.query(orm.ApprovalRequestModel)
            .outerjoin(
                orm.TradePlanModel,
                orm.TradePlanModel.plan_id == orm.ApprovalRequestModel.order_intent_id,
            )
            .filter(
                orm.ApprovalRequestModel.market == market,
                orm.ApprovalRequestModel.status == "APPROVED",
                orm.ApprovalRequestModel.direction == "BUY",
                orm.ApprovalRequestModel.created_at
                >= datetime.combine(today, datetime.min.time(), tzinfo=profile.tz),
                _or(orm.TradePlanModel.plan_id.is_(None),
                    orm.TradePlanModel.plan_status.notin_(_DEAD_PLAN)),
            )
            .all()
        )

    def _today_entry_counts(self, market: str, today) -> dict[str, int]:
        """当日每 plan 的 BUY 成交次数（v2_order_intent 计数，防超 MAX_ENTRIES）。"""
        from datetime import datetime

        from zhice_os_v2.infrastructure.db import models as orm
        from zhice_os_v2.infrastructure.db.session import get_session

        lo = datetime.combine(today, datetime.min.time())
        try:
            session = get_session()
            try:
                rows = (
                    session.query(orm.OrderIntentModel.plan_id,
                                  )
                    .filter(orm.OrderIntentModel.market == market,
                            orm.OrderIntentModel.intent_side == "BUY",
                            orm.OrderIntentModel.created_at >= lo)
                    .all()
                )
                from collections import Counter
                return dict(Counter(r[0] for r in rows if r[0]))
            finally:
                session.close()
        except Exception:
            return {}

    def _close_position(self, position, close_price: Decimal, exit_kind: str = "MANUAL") -> None:
        """现价平仓（SimulatedBroker 语义：直接按现价成交，费用简化不计）。"""
        from zhice_os_v2.domain.ontology.objects import utc_now

        qty = position.open_qty
        position.status = PositionStatus.CLOSED
        position.close_qty = qty
        position.close_price = close_price
        position.closed_at = utc_now()
        self._db.save_position(position)
        if position.open_price:
            _persist_position_pnl(
                position.position_id,
                (close_price - position.open_price) * qty,
                (close_price - position.open_price) / position.open_price,
            )
        # 平仓成交落库（fill_id=fill-close-{position_id} 防重），带卖出理由与实现盈亏
        realized = None
        if position.open_price:
            realized = (close_price - position.open_price) * qty
        self._record_fill(
            market=position.market,
            ts_code=position.ts_code,
            side="SELL",
            qty=qty,
            price=close_price,
            position=position,
            fill_time=position.closed_at,
            sell_reason=_EXIT_KIND_TEXT.get(exit_kind, f"平仓离场：{exit_kind}"),
            realized_pnl=realized,
            exit_kind=exit_kind,
        )

    def _record_fill(
        self,
        market: str,
        ts_code: str,
        side: str,
        qty: Decimal,
        price: Decimal,
        approval=None,
        position=None,
        fill_time=None,
        sell_reason: str | None = None,
        realized_pnl: Decimal | None = None,
        exit_kind: str | None = None,
        notify: bool = True,
    ) -> None:
        """V2 成交记录落库：fill + 简易 execution/order_request/order_intent（保 join 链完整）。

        建仓：fill_id=fill-{order_id}（position_id 去掉 'pos-' 前缀即 order_id）；
        平仓：fill_id=fill-close-{position_id}（防重）。
        幂等：唯一键冲突（fill_id / execution_id / request_id / intent_id）直接跳过。
        失败只记 log 不影响交易主流程，但落 CRITICAL 告警（603311 案：静默吞
        异常致流水永久丢失无从发现）。
        notify=False：静默补写历史缺口，不推 telegram（防假历史成交轰炸）。

        Telegram 通知（Phase C Task 8 三态）：broadcast（默认）原 notify_fill 广播
        路径一字不变；shadow/directed 经 notification_routing.route_fill 与成交
        同事务入 outbox（exit_kind 供 STOP_LOSS/TAKE_PROFIT 事件映射）。
        """
        try:
            from zhice_os_v2.config.settings import get_settings
            if get_settings().persistence_backend != "sql":
                return  # 内存后端（单测）不写 Postgres

            from sqlalchemy.dialects.postgresql import insert as pg_insert

            from zhice_os_v2.domain.ontology.objects import utc_now
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session

            is_open = side == "BUY"
            if is_open and position is not None:
                order_id = position.position_id.removeprefix("pos-")
            elif not is_open and position is not None:
                order_id = f"close-{position.position_id}"
            else:
                return
            # id 列限 varchar(64)：signal 引擎的 position_id 带 sv+分钟桶前缀,平仓 order_id
            # 加 fill-/intent-/req-/exec- 前缀后常超 64 → insert 静默失败(被下方 except 吞),
            # 平仓成交写不进 → 交易记录只有买入没卖出。超限时用稳定哈希尾缀压到 ≤64,
            # 同 (prefix,order_id) 结果确定 → 四表 join(fill.execution_id=exec-… 等)仍一致。
            def _fit(prefix: str) -> str:
                s = f"{prefix}{order_id}"
                if len(s) <= 64:
                    return s
                import hashlib
                h = hashlib.md5(s.encode()).hexdigest()[:10]
                keep = 64 - len(prefix) - 11  # 11 = '-' + 10位hash
                return f"{prefix}{order_id[:keep]}-{h}"

            fill_id = _fit("fill-")
            execution_id = _fit("exec-")
            request_id = _fit("req-")
            intent_id = _fit("intent-")
            now = fill_time or utc_now()
            amount = (price or Decimal("0")) * (qty or Decimal("0"))

            # intent 关联：建仓挂 plan（approval.order_intent_id=plan_id）；平仓挂原 plan
            plan_id = ""
            sv_id = ""
            # 运行时归属（Phase C Task 8）：上下文有则随 intent 落库（owned 持仓经
            # 系统离场链平仓时，close-intent 也可经 OwnershipResolver.for_fill 解析）；
            # legacy 两者恒 None，写 NULL 与现状一致。
            ctx_usid = None
            ctx_acct = None
            if approval is not None:
                plan_id = approval.order_intent_id or ""
                sv_id = str(approval.strategy_version_id)
                ctx_usid = getattr(approval, "user_strategy_id", None) or None
                ctx_acct = getattr(approval, "account_id", None) or None
            elif position is not None:
                sv_id = position.strategy_version_id or ""
                ctx_usid = getattr(position, "user_strategy_id", None) or None
                ctx_acct = getattr(position, "account_id", None) or None

            from zhice_os_v2.application.services.notification_routing import notify_mode
            mode = notify_mode()
            routed_owner = None

            session = get_session()
            try:
                def _upsert(model, conflict_col: str, values: dict):
                    stmt = pg_insert(model).values(**values).on_conflict_do_nothing(
                        index_elements=[conflict_col]
                    )
                    session.execute(stmt)

                _upsert(orm.OrderIntentModel, "intent_id", {
                    "intent_id": intent_id,
                    "plan_id": plan_id,
                    "strategy_version_id": sv_id,
                    "user_strategy_id": ctx_usid,
                    "account_id": ctx_acct,
                    "market": market,
                    "ts_code": ts_code,
                    "intent_type": "OPEN" if is_open else "CLOSE",
                    "intent_side": side,
                    "intent_qty": qty,
                    "intent_price": price,
                    "status": "FILLED",
                    "created_at": now,
                })
                _upsert(orm.OrderRequestModel, "request_id", {
                    "request_id": request_id,
                    "intent_id": intent_id,
                    "market": market,
                    "broker_channel": "SIMULATED",
                    "idempotency_key": order_id,
                    "status": "SUBMITTED",
                    "created_at": now,
                })
                _upsert(orm.ExecutionRecordModel, "execution_id", {
                    "execution_id": execution_id,
                    "request_id": request_id,
                    "market": market,
                    "broker_order_id": f"SIM-{order_id}",
                    "execution_status": "FILLED",
                    "created_at": now,
                })
                _upsert(orm.FillRecordModel, "fill_id", {
                    "fill_id": fill_id,
                    "execution_id": execution_id,
                    "market": market,
                    "ts_code": ts_code,
                    "broker_trade_id": f"SIM-{order_id}",
                    "fill_price": price,
                    "fill_qty": qty,
                    "fill_amount": amount,
                    "fill_time": now,
                    "sell_reason": sell_reason,
                    "realized_pnl": realized_pnl,
                    "created_at": now,
                })
                # 定向通知同事务入队（Phase C Task 8）：与成交行同生共死。
                # broadcast（默认）不进分支——原路径零改动；任何异常吞掉，
                # 绝不影响成交落库与提交。
                if mode != "broadcast":
                    try:
                        from zhice_os_v2.application.services.notification_routing import (
                            route_fill,
                        )
                        routed_owner = route_fill(
                            session,
                            fill_id=fill_id, side=side, ts_code=ts_code, price=price,
                            qty=qty, amount=amount, market=market, sv_id=sv_id,
                            reason=(getattr(approval, "reason", None)
                                    if approval is not None else None),
                            sell_reason=sell_reason, realized_pnl=realized_pnl,
                            fill_time=now, exit_kind=exit_kind,
                            position=position, approval=approval,
                        )
                    except Exception:
                        logger.debug(
                            "fill notification routing failed: fill_id=%s", fill_id,
                            exc_info=True,
                        )
                    # 订阅者信号扇出（P2）：内置策略成交 → 其活跃订阅者入队 SIGNAL
                    # （payload 仅信号要素，无持仓/盈亏）；与成交同事务。best-effort。
                    try:
                        from zhice_os_v2.application.services.notification_routing import (
                            route_subscription_signal,
                        )
                        route_subscription_signal(
                            session, fill_id=fill_id, sv_id=sv_id, side=side,
                            ts_code=ts_code, price=price, market=market,
                            exit_kind=exit_kind,
                            reason=(getattr(approval, "reason", None)
                                    if approval is not None else None),
                            fill_time=now,
                        )
                    except Exception:
                        logger.debug(
                            "subscription signal routing failed: fill_id=%s", fill_id,
                            exc_info=True,
                        )
                session.commit()
            finally:
                session.close()
            # Telegram 成交回报（best-effort，notify_fill 内部永不抛）。
            # broadcast：原路径一字不变；shadow：新旧并行观察——仅 owner 已解析的
            # 成交照发广播（legacy 无归属成交不广播，route_fill 已记日志）；
            # directed：只走 outbox，绝不广播（owner 缺失时 route_fill 已落
            # owner_missing 告警，绝不广播兜底）。
            if notify and (
                mode == "broadcast" or (mode == "shadow" and routed_owner is not None)
            ):
                from zhice_os_v2.application.services.telegram_notifier import notify_fill
                notify_fill(
                    side=side, ts_code=ts_code, price=price, qty=qty, amount=amount,
                    market=market, sv_id=sv_id,
                    reason=getattr(approval, "reason", None) if approval is not None else None,
                    sell_reason=sell_reason, realized_pnl=realized_pnl, fill_time=now,
                )
        except Exception:
            logger.exception(
                "record_fill failed: market=%s ts_code=%s side=%s position=%s",
                market, ts_code, side, getattr(position, "position_id", None),
            )
            # Bug A 修复（603311 案）：此前静默吞掉 → 持仓已平、流水永久缺失且
            # 无从发现。落 CRITICAL 告警留痕（send_alert 永不抛；DB 同挂时其内部
            # 降级为日志，此处仅防导入级意外）。
            try:
                from zhice_os_v2.application.services.alert_service import send_alert

                send_alert(
                    "CRITICAL",
                    f"成交流水写入失败: {market} {ts_code} {side}",
                    f"position={getattr(position, 'position_id', None)} qty={qty} "
                    f"price={price}；持仓/交易已生效但流水缺失，需对账补写",
                )
            except Exception:
                logger.error("record_fill alert dispatch failed", exc_info=True)

    def run_post_market(self, market: str) -> dict:
        """盘后工作流：只出日报（对齐 V1 盘后语义=复盘/归因/快照，不碰仓位）。

        「日终清仓」是 V2 初建时的自作主张（V1 无此逻辑，2026-08-24 查证），
        全市场停用（用户指令）：持仓出场只走 策略信号 exit / run_intraday
        止盈止损。V1 导入的历史持仓（v1-{market}-pos-*）本就只读永不自动平。
        另：V1 Step9b 市场环境计算落表（供次日盘前路由）。
        """
        regime = None
        try:
            from zhice_os_v2.application.services.regime_service import compute_and_save
            regime = compute_and_save(market)
        except Exception:
            logger.debug("market_env compute failed market=%s", market, exc_info=True)
        report = ReportingService(self._db).daily_report(market=market)
        snapshots = self._persist_portfolio_snapshots(market)
        return {"market": market, "closed_positions": [],
                "skipped_v1_positions": 0, "report": report, "regime": regime,
                "snapshots": snapshots}

    def run_plan_expiry(self, market: str, session_date=None) -> dict:
        """盘后计划过期（口径A 同会话）：把 market 下「创建日 < 当前会话日、且无 APPROVED
        授权」的 PENDING 计划置 EXPIRED，其 PENDING 授权一并 expire()。

        - 硬排除 approval=APPROVED（保护已批准/在执行计划）；只动"创建日<当前会话日"
          （今日在途不碰）。幂等（只动 plan_status='PENDING'，重复跑不叠加）。
        - session_date 缺省取运行日（date.today()）；盘后触发 → 今日计划 created_at 日期
          == 当前日，不满足 <，故安全留存，仅清历史积压。
        """
        from datetime import date
        if session_date is None:
            session_date = date.today()
        res = self._db.expire_stale_pending_plans(market, session_date)
        # 日内单语义：过期滞留 APPROVED/EXECUTING（当日未约定无终态收敛，逐日累积撑爆
        # sim_max_positions 致 pre_market 零计划）。使 active 承认恒收敛到今日在途。
        act = self._db.expire_stale_active_approvals(market, session_date)
        res = {**res, **act}
        logger.info(
            "plan_expiry market=%s session=%s plans_expired=%s approvals_expired=%s "
            "active_approvals_expired=%s active_plans_expired=%s",
            market, session_date, res.get("plans_expired"), res.get("approvals_expired"),
            res.get("active_approvals_expired"), res.get("active_plans_expired"))
        return {"market": market, "session_date": str(session_date), **res}

    def _persist_portfolio_snapshots(self, market: str) -> int:
        """盘后按策略账户幂等落库组合快照到 v2_portfolio_snapshot（V1 每日归档口径）。

        复用 AccountStatsService.by_strategy 的账户估值（权益/现金/市值/仓数），每策略
        账户一行；snapshot_id=`{market}_{sv}_{date}` 确定性 id，同日重跑覆盖。fail-open
        不阻断盘后主流程。返回落库行数。
        """
        try:
            import hashlib

            from sqlalchemy.dialects.postgresql import insert as pg_insert

            from zhice_os_v2.config.settings import get_settings
            # 内存后端（单测）不写真库——保证隔离、不污染生产库（get_session 恒连真 PG）。
            if get_settings().persistence_backend != "sql":
                return 0

            from zhice_os_v2.api.routers.positions import _get_latest_trade_date
            from zhice_os_v2.application.services.account_stats_service import (
                AccountStatsService,
            )
            from zhice_os_v2.domain.ontology.objects import utc_now
            from zhice_os_v2.infrastructure.db import models as orm
            from zhice_os_v2.infrastructure.db.session import get_session

            d = _get_latest_trade_date(market)
            if d is None:
                return 0
            accounts = AccountStatsService(self._db).by_strategy(market)
            if not accounts:
                return 0
            session = get_session()
            try:
                for a in accounts:
                    sv = a["strategy_version_id"]
                    snap_id = f"{market.lower()}_{sv}_{d.isoformat()}"
                    if len(snap_id) > 64:
                        h = hashlib.md5(snap_id.encode()).hexdigest()[:10]
                        snap_id = f"{snap_id[:53]}-{h}"[:64]
                    vals = {
                        "total_assets": a["total_equity"],
                        "available_cash": a["available_cash"],
                        "position_market_value": a["market_value"],
                        "position_count": a["position_count"],
                        "created_at": utc_now(),
                    }
                    stmt = (
                        pg_insert(orm.PortfolioSnapshotModel)
                        .values(
                            snapshot_id=snap_id, strategy_version_id=sv, market=market,
                            snapshot_date=d, **vals,
                        )
                        .on_conflict_do_update(index_elements=["snapshot_id"], set_=vals)
                    )
                    session.execute(stmt)
                session.commit()
                return len(accounts)
            finally:
                session.close()
        except Exception:
            logger.exception("portfolio_snapshot persist failed market=%s", market)
            return 0
