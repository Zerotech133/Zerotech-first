# -*- coding: utf-8 -*-
"""strategy 域骨架（第十一域，K-ORG-167）——军团方案仓库路线拍板落 AP。

老田 2026-10-18 三拍板之一：仓库路线选「agent_platform 扩展」而非
digital-legion 新仓——策略接口/registry 并入 AP 第十一域，Paperclip 心跳 /
Codex bridge / Hermes gateway 后续走 adapters/ 对接，不重造幂等账本/
审批门禁/审计/绩效（已有 486 绿基建）。

契约来源：军团总纲 v1.0「契约代码级设计」章（StrategyAgent/Verdict），
与 AP 存量对齐处：
- Verdict.recommendation 五档枚举对齐 shadow_ledger/eval_shadow 口径；
- Verdict stance 对齐控辩审契约（interpreter._infer_role：bull/bear）；
- 红线继承：策略只产建议（Verdict），execution_allowed 恒 False——
  下单归 K-STOCK execution 线，本域不持有任何凭据。
"""
from dataclasses import dataclass, field

RECOMMENDATIONS = ("strong_buy", "buy", "hold", "sell", "strong_sell")
STANCES = ("bull", "bear", "neutral")
CONFIDENCE_MIN, CONFIDENCE_MAX = 0.0, 1.0


class StrategyError(Exception):
    """策略注册/产出契约非法（重复注册/字段越界/枚举外）"""


@dataclass
class Verdict:
    """单策略对单标的的一次立场产出（建议权，无执行权）。"""
    strategy_id: str
    symbol: str
    recommendation: str          # 五档枚举，对齐影子账本口径
    confidence: float            # [0,1]
    stance: str = "neutral"      # bull/bear/neutral，对齐控辩审
    score: float = 0.0
    rationale: str = ""
    trace_id: str = ""           # 关联影子/链路锚点（回放用，可空）
    extras: dict = field(default_factory=dict)

    def validate(self):
        if self.recommendation not in RECOMMENDATIONS:
            raise StrategyError("%s recommendation=%s 不在五档枚举 %s"
                                % (self.strategy_id, self.recommendation,
                                   RECOMMENDATIONS))
        if not (CONFIDENCE_MIN <= self.confidence <= CONFIDENCE_MAX):
            raise StrategyError("%s confidence=%s 越界 [0,1]"
                                % (self.strategy_id, self.confidence))
        if self.stance not in STANCES:
            raise StrategyError("%s stance=%s 不在 %s"
                                % (self.strategy_id, self.stance, STANCES))
        if not self.symbol:
            raise StrategyError("%s symbol 缺失" % self.strategy_id)
        return self


class StrategyAgent:
    """策略插件接口（总纲：策略是插件）。

    子类必须实现 propose(context) -> Verdict；context 为只读数据上下文
    （K线/舆情/基本面快照），策略内部不得写任何域账本（红线）。
    """

    strategy_id = ""             # 全局唯一，注册时校验
    stance = "neutral"

    def propose(self, context):
        raise NotImplementedError("策略插件必须实现 propose(context)")


class StrategyRegistry:
    """策略注册表：注册/查询/罗列。同 ID 重复注册=冲突拒绝（幂等纪律平移）。"""

    def __init__(self):
        self._strategies = {}

    def register(self, agent):
        if not isinstance(agent, StrategyAgent):
            raise StrategyError("仅接受 StrategyAgent 子类实例：%r" % type(agent))
        sid = getattr(agent, "strategy_id", "")
        if not sid:
            raise StrategyError("strategy_id 缺失，拒绝注册")
        if sid in self._strategies:
            raise StrategyError("strategy_id=%s 重复注册（同键异值=漂移拒绝）" % sid)
        if agent.stance not in STANCES:
            raise StrategyError("%s stance=%s 不在 %s" % (sid, agent.stance, STANCES))
        self._strategies[sid] = agent
        return sid

    def get(self, sid):
        if sid not in self._strategies:
            raise StrategyError("strategy_id=%s 未注册" % sid)
        return self._strategies[sid]

    def list_ids(self, stance=None):
        ids = sorted(self._strategies)
        if stance is None:
            return ids
        return [s for s in ids if self._strategies[s].stance == stance]

    def run(self, sid, context):
        """执行单策略并强制产出校验（白名单在出口统一把守，旁路判例）。"""
        v = self.get(sid).propose(context)
        if not isinstance(v, Verdict):
            raise StrategyError("%s propose 返回 %r，非 Verdict"
                                % (sid, type(v).__name__))
        if v.strategy_id != sid:
            raise StrategyError("%s propose 产出 strategy_id=%r 与注册 ID 不符"
                                % (sid, v.strategy_id))
        return v.validate()
