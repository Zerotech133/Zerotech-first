"""交易计划服务。"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2.application.services.risk_params import effective_stop_loss_pct
from zhice_os_v2.domain.candidate.candidate import Candidate
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.strategy.exit_policies import get_exit_policy_for_version
from zhice_os_v2.domain.strategy.spec import RiskSpec
from zhice_os_v2.infrastructure.repositories.ports import PersistencePort


class TradePlanService:
    def __init__(self, db: PersistencePort) -> None:
        self._db = db

    def create_from_candidate(
        self,
        candidate: Candidate,
        risk: RiskSpec,
        ref_price: Decimal,
        qty: Decimal,
    ) -> TradePlan:
        policy = get_exit_policy_for_version(candidate.strategy_version_id, self._db)
        # 止损接线：settings.risk_params.stop_loss_pct（设置页百分数，如 8.0）
        # 覆盖策略级硬编码（exit_policies 内为小数，如 0.05=5%）；
        # 读取失败/未配置 → effective_stop_loss_pct 回退 policy.stop_loss_pct（现行值）。
        stop_loss_pct = effective_stop_loss_pct(Decimal(str(policy.stop_loss_pct)))
        take_profit_pct = Decimal(str(policy.take_profit_pct))
        plan = TradePlan(
            plan_id=f"plan-{candidate.candidate_id}",
            strategy_version_id=candidate.strategy_version_id,
            market=candidate.market,
            ts_code=candidate.ts_code,
            direction="BUY",
            qty=qty,
            ref_price=ref_price,
            stop_loss=ref_price * (Decimal("1") - stop_loss_pct),
            take_profit=ref_price * (Decimal("1") + take_profit_pct),
            # 仓位比例透传（此前 risk 参数被忽略、字段恒走 dataclass 默认 0.05，
            # 风控 MAX_POSITION_RATIO 拿不到策略 spec 值）：spec.risk.max_position_ratio
            # → plan.max_position_ratio，RiskService.check_plan 消费（落库列已存在）。
            max_position_ratio=Decimal(str(risk.max_position_ratio)),
        )
        self._db.save_trade_plan(plan)
        return plan
