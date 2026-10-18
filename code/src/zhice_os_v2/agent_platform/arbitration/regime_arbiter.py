"""RegimeArbiter — 三步仲裁（架构稿 §4.2 硬规范）。

STEP 1 加权表决 → STEP 2 市场状态分层否决 → STEP 3 LLM 兜底升级。
全部路径落 aw_decision（arbitration_path ∈ WEIGHTED|REGIME_VETO|LLM|DROPPED）。
regime 门直接 import zhice market_regime_permits_entry（只读复用，不重写）。
"""
from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from zhice_os_v2.agent_platform import metrics
from zhice_os_v2.agent_platform.arbitration.llm_arbiter import (
    LlmArbiter,
    MockLlmArbiter,
)
from zhice_os_v2.agent_platform.config import ArbitrationConfig, utc_now
from zhice_os_v2.agent_platform.methods.base import MethodVote, WatchContext

logger = logging.getLogger(__name__)

ARBITRATION_PATHS = ("WEIGHTED", "REGIME_VETO", "LLM", "DROPPED")


@dataclass
class AgentDecision:
    """仲裁产出（aw_decision 一行的事实源对象）。"""

    decision_id: str
    ts_code: str
    market: str
    action: str                        # SELL / HOLD / WAIT
    score_sell: float
    score_hold: float
    votes: list[MethodVote]
    regime_risk_off: bool
    arbitration_path: str              # WEIGHTED | REGIME_VETO | LLM | DROPPED
    status: str = "PENDING_CONFIRM"    # 仅 SELL 送审；HOLD/WAIT 落库即 ARCHIVED
    model_verdict: dict | None = None
    bar_time: datetime | None = None
    fwd_ret_5d: float | None = None
    created_at: datetime | None = None
    note: str = ""
    playbook_id: str | None = None     # T06 打法包归因（None=默认包/旧数据）

    def __post_init__(self) -> None:
        if self.created_at is None:
            self.created_at = utc_now()

    def to_dict(self) -> dict:
        return {
            "decision_id": self.decision_id,
            "ts_code": self.ts_code,
            "market": self.market,
            "bar_time": self.bar_time.isoformat() if self.bar_time else None,
            "action": self.action,
            "score_sell": round(self.score_sell, 4),
            "score_hold": round(self.score_hold, 4),
            "votes_json": [v.to_dict() for v in self.votes],
            "regime_risk_off": self.regime_risk_off,
            "arbitration_path": self.arbitration_path,
            "model_verdict": self.model_verdict,
            "status": self.status,
            "fwd_ret_5d": self.fwd_ret_5d,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "note": self.note,
            "playbook_id": self.playbook_id,
        }


def _new_decision_id() -> str:
    return "aw-" + uuid.uuid4().hex[:20]


@dataclass
class _ScoreResult:
    """STEP1/STEP2 的内部计分结果。"""

    action: str
    score_sell: float
    score_hold: float
    quorum_met: bool
    sum_conf: float
    risk_off: bool


class RegimeArbiter:
    """三步仲裁器。regime_fn 可注入（生产=zhice market_regime_permits_entry）。"""

    def __init__(
        self,
        llm_arbiter: LlmArbiter | None = None,
        cfg: ArbitrationConfig | None = None,
        regime_fn: Callable[[date, str], bool] | None = None,
        position_value_threshold: float = 10000.0,
    ) -> None:
        self.cfg = cfg or ArbitrationConfig()
        self.llm = llm_arbiter or MockLlmArbiter()   # 默认 mock（线上显式注入真 Arbiter）
        self.regime_fn = regime_fn                    # None → 由调用方经 ctx.regime_risk_off 给定
        self.position_value_threshold = float(position_value_threshold)

    # ── 主入口 ────────────────────────────────────────────────────────────
    def arbitrate(self, votes: Sequence[MethodVote], ctx: WatchContext) -> AgentDecision:
        weights = self._weights_of(votes)
        risk_off = self._resolve_risk_off(ctx)
        base = self._weighted_score(votes, weights, risk_off=False)
        veto = self._weighted_score(votes, weights, risk_off=risk_off)

        base_action = self._action_from_scores(base)
        veto_action = self._action_from_scores(veto)

        # STEP 2：risk_off 改变 STEP1 结论 → REGIME_VETO 路径
        # （risk_off 只放大 SELL/萎缩 HOLD；WAIT(法定人数不足) 恒定不参与否决）
        if risk_off and veto_action != base_action and base_action != "WAIT":
            decision = self._make(votes, ctx, veto, veto_action, risk_off,
                                  path="REGIME_VETO",
                                  note=f"risk_off否决: {base_action}→{veto_action} "
                                       f"(SELL×{self.cfg.risk_off_sell_mult}/HOLD×{self.cfg.risk_off_hold_mult})")
            self._emit(decision)
            return decision

        # STEP 1：法定人数不足 → 直接 WAIT（WEIGHTED 路径）
        if not base.quorum_met:
            decision = self._make(votes, ctx, base, "WAIT", risk_off,
                                  path="WEIGHTED",
                                  note=f"法定人数不足: 有效票{self._effective_count(votes)}<"
                                       f"{self.cfg.min_quorum_votes} 或 Σconf={base.sum_conf:.2f}<"
                                       f"{self.cfg.min_sum_conf}")
            self._emit(decision)
            return decision

        # STEP 3 触发判定：平票（|分差|<margin，含相等）/ 高持仓 SELL 复核
        # 计分口径：留痕分数 = 最终生效层（risk_off 时为否决层 veto 分）
        margin = abs(veto.score_sell - veto.score_hold)
        tie = margin < self.cfg.tie_margin
        high_pos = (base_action == "SELL" and ctx.position_value > self.position_value_threshold)
        if not (tie or high_pos):
            decision = self._make(votes, ctx, veto, base_action, risk_off, path="WEIGHTED")
            self._emit(decision)
            return decision

        # STEP 3：LLM 兜底
        verdict = self.llm.arbitrate(list(votes), ctx)
        model_verdict = {
            "action": verdict.action,
            "confidence": round(verdict.confidence, 4),
            "rationale": verdict.rationale,
            "source": verdict.source,
        }
        if verdict.dropped:
            path = "DROPPED"
            final_action = "WAIT"
        else:
            path = "LLM"
            final_action = verdict.action
        note_parts = []
        if tie:
            note_parts.append(f"平票(分差{margin:.3f}<{self.cfg.tie_margin:.2f})")
        if high_pos:
            note_parts.append(f"高持仓复核({ctx.position_value:.0f}>{self.position_value_threshold:.0f})")
        decision = self._make(
            votes, ctx, veto, final_action, risk_off, path=path,
            model_verdict=model_verdict,
            note="; ".join(note_parts),
        )
        self._emit(decision)
        return decision

    # ── STEP 1/2 计分 ─────────────────────────────────────────────────────
    def _weighted_score(self, votes: Sequence[MethodVote], weights: dict[str, float],
                        risk_off: bool) -> _ScoreResult:
        """score(X) = Σ wᵢ×confᵢ×1[dir=X] / Σ wᵢ（仅 SELL/HOLD 参与，WAIT 不计分）。"""
        sell_wsum, hold_wsum, tot_w, sum_conf, eff = 0.0, 0.0, 0.0, 0.0, 0
        for v in votes:
            w = weights.get(v.method_id, 1.0)
            tot_w += w
            if v.direction == "WAIT":
                continue
            eff += 1
            sum_conf += v.confidence
            mult = 1.0
            if risk_off and v.direction == "SELL":
                mult = self.cfg.risk_off_sell_mult
            elif risk_off and v.direction == "HOLD":
                mult = self.cfg.risk_off_hold_mult
            if v.direction == "SELL":
                sell_wsum += w * v.confidence * mult
            elif v.direction == "HOLD":
                hold_wsum += w * v.confidence * mult
        denom = tot_w if tot_w > 0 else 1.0
        quorum = eff >= self.cfg.min_quorum_votes and sum_conf >= self.cfg.min_sum_conf
        return _ScoreResult(
            action="WAIT",
            score_sell=sell_wsum / denom,
            score_hold=hold_wsum / denom,
            quorum_met=quorum,
            sum_conf=sum_conf,
            risk_off=risk_off,
        )

    @staticmethod
    def _action_from_scores(sc: _ScoreResult) -> str:
        if not sc.quorum_met:
            return "WAIT"
        if sc.score_sell > sc.score_hold:
            return "SELL"
        if sc.score_hold > sc.score_sell:
            return "HOLD"
        return "WAIT"   # 严格相等（浮点罕见）→ WAIT

    @staticmethod
    def _effective_count(votes: Sequence[MethodVote]) -> int:
        return sum(1 for v in votes if v.direction != "WAIT")

    def _weights_of(self, votes: Sequence[MethodVote]) -> dict[str, float]:
        """从票上取出票时权重（MethodRegistry.evaluate_all 盖章；缺省 1.0 兜底）。"""
        return {v.method_id: getattr(v, "weight", 1.0) for v in votes}

    # ── 辅助 ──────────────────────────────────────────────────────────────
    def _resolve_risk_off(self, ctx: WatchContext) -> bool:
        """优先 ctx 标志（调用方已判定），否则经 regime_fn 现算（zhice 只读复用）。"""
        if self.regime_fn is not None and ctx.bars_df is not None and len(ctx.bars_df):
            dt = ctx.bars_df["dt"].iloc[-1]
            d = dt.date() if hasattr(dt, "date") else date.today()
            try:
                return not self.regime_fn(d, ctx.market)
            except Exception as exc:
                logger.warning(
                    "regime 判定失败(%s, market=%s, %s: %s) → 回退 ctx 标志",
                    d, ctx.market, type(exc).__name__, exc,
                )
        return bool(ctx.regime_risk_off)

    def _make(self, votes, ctx, sc: _ScoreResult, action: str, risk_off: bool, *,
              path: str, note: str = "", model_verdict: dict | None = None) -> AgentDecision:
        bar_time = None
        if ctx.bars_df is not None and len(ctx.bars_df) and "dt" in ctx.bars_df.columns:
            last_dt = ctx.bars_df["dt"].iloc[-1]
            bar_time = last_dt.to_pydatetime() if hasattr(last_dt, "to_pydatetime") else last_dt
        status = "PENDING_CONFIRM" if action == "SELL" else "ARCHIVED"
        return AgentDecision(
            decision_id=_new_decision_id(),
            ts_code=ctx.ts_code,
            market=ctx.market,
            action=action,
            score_sell=round(sc.score_sell, 6),
            score_hold=round(sc.score_hold, 6),
            votes=list(votes),
            regime_risk_off=risk_off,
            arbitration_path=path,
            status=status,
            model_verdict=model_verdict,
            bar_time=bar_time,
            note=note,
        )

    def _emit(self, decision: AgentDecision) -> None:
        metrics.inc_path(decision.arbitration_path)
