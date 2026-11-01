# -*- coding: utf-8 -*-
"""采集即服务平台 · 分析→采集反馈环 (core/feedback.py)

把"分析层"（质量 SLA / PII 合规）的结论回流到"采集层"（Scheduler / Registry），
形成闭环治理：
  - SLA 违约/滞后/无数据 → 加速补采（EXPEDITE_RETRY）或熔断开闸（OPEN_CIRCUIT）+ 人工复核标记；
  - PII 命中 → 高风险用途自动降级（DOWNGRADE_RIGHTS，即便曾授权也要撤）+ 复核标记。

设计：
  - 纯计算（compute_*）与副作用（apply_*）分离，便于单测；
  - FeedbackEngine 持有 strike 计数等会话态，可接入 CronRunner 每个 tick 调用；
  - 副作用默认只作用于内存态 Registry/Scheduler；落地到 sources.json 由调用方（生产 tick）决定，
    原型态/面板读取只调纯 compute，绝不污染源文件。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from core.quality_sla import SlaReport, STATE_NO_DATA, STATE_STALE, STATE_BREACH
from registry.source_registry import SourceRegistry
from core.scheduler import Scheduler


class FeedbackActionType(str, Enum):
    EXPEDITE_RETRY = "expedite_retry"       # 加速补采：强制下次到期=现在
    OPEN_CIRCUIT = "open_circuit"           # 熔断开闸：禁用源（连续违约达阈值）
    DOWNGRADE_RIGHTS = "downgrade_rights"   # 合规自动降级高风险用途
    FLAG_REVIEW = "flag_review"             # 标记人工复核
    CLEAR_REVIEW = "clear_review"           # 解除复核标记（恢复）
    NOOP = "noop"


# 高风险用途：PII 命中时即便曾授权也要自动降级
HIGH_RISK_USES = ("training", "redistribution", "commercial")


@dataclass
class FeedbackAction:
    source_id: str
    action: FeedbackActionType
    reason: str
    at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "action": self.action.value,
            "reason": self.reason,
            "at": self.at,
            "detail": self.detail,
        }


@dataclass
class SlaFeedbackPolicy:
    # 触发 OPEN_CIRCUIT 的连续违约次数阈值（与 max_failures 解耦，默认 3）
    breach_open_threshold: int = 3
    # 是否对 no_data 也加速补采
    expedite_on_no_data: bool = True
    # 是否对 stale 也加速补采
    expedite_on_stale: bool = True


def compute_sla_feedback(
    sla_reports: dict[str, SlaReport],
    strike_counts: Optional[dict[str, int]] = None,
    policy: Optional[SlaFeedbackPolicy] = None,
) -> tuple[list[FeedbackAction], dict[str, int]]:
    """纯计算：依据 SLA 报告产出反馈动作；返回 (actions, 更新后的 strike 计数)。

    strike_counts 跨调用保持（会话态），用于累计连续违约。仅 breach 累计、其余状态重置。
    """
    policy = policy or SlaFeedbackPolicy()
    strike_counts = dict(strike_counts or {})
    actions: list[FeedbackAction] = []
    for sid, r in sla_reports.items():
        st = r.state
        if st == STATE_BREACH:
            strike_counts[sid] = strike_counts.get(sid, 0) + 1
            actions.append(FeedbackAction(
                sid, FeedbackActionType.EXPEDITE_RETRY,
                f"新鲜度违约(>{r.freshness_minutes * 2}m)，加速补采",
                detail={"age_minutes": r.age_minutes, "strike": strike_counts[sid]},
            ))
            if strike_counts[sid] >= policy.breach_open_threshold:
                actions.append(FeedbackAction(
                    sid, FeedbackActionType.OPEN_CIRCUIT,
                    f"连续违约达 {strike_counts[sid]} 次，熔断开闸自动禁用",
                    detail={"strike": strike_counts[sid]},
                ))
                actions.append(FeedbackAction(
                    sid, FeedbackActionType.FLAG_REVIEW,
                    "连续违约触发熔断，需人工复核采集链路",
                    detail={"strike": strike_counts[sid]},
                ))
                strike_counts[sid] = 0  # 已开闸，重置计数
        elif st == STATE_STALE:
            strike_counts[sid] = 0
            if policy.expedite_on_stale:
                actions.append(FeedbackAction(
                    sid, FeedbackActionType.EXPEDITE_RETRY,
                    f"新鲜度滞后(>{r.freshness_minutes}m)，加速补采",
                    detail={"age_minutes": r.age_minutes},
                ))
        elif st == STATE_NO_DATA:
            strike_counts[sid] = 0
            if policy.expedite_on_no_data:
                actions.append(FeedbackAction(
                    sid, FeedbackActionType.EXPEDITE_RETRY,
                    "无观测，加速补采",
                ))
        else:  # ok
            strike_counts[sid] = 0
    return actions, strike_counts


def compute_pii_feedback(
    pii_counts: dict[str, int],
    high_risk_uses: tuple[str, ...] = HIGH_RISK_USES,
) -> list[FeedbackAction]:
    """纯计算：对 PII 命中的源，自动降级高风险用途并标记复核。"""
    actions: list[FeedbackAction] = []
    for sid, cnt in pii_counts.items():
        if cnt and cnt > 0:
            actions.append(FeedbackAction(
                sid, FeedbackActionType.DOWNGRADE_RIGHTS,
                f"PII 命中 {cnt} 处，自动降级高风险用途 {', '.join(high_risk_uses)}",
                detail={"pii_count": cnt, "revoked": list(high_risk_uses)},
            ))
            actions.append(FeedbackAction(
                sid, FeedbackActionType.FLAG_REVIEW,
                "含 PII，需人工复核合规脱敏有效性",
                detail={"pii_count": cnt},
            ))
    return actions


def apply_actions(
    actions: list[FeedbackAction],
    registry: Optional[SourceRegistry] = None,
    scheduler: Optional[Scheduler] = None,
) -> list[FeedbackAction]:
    """副作用：把反馈动作落到 Registry/Scheduler。返回实际生效的动作（apply 成功才计入）。"""
    applied: list[FeedbackAction] = []
    for a in actions:
        ok = False
        if a.action == FeedbackActionType.EXPEDITE_RETRY and scheduler is not None:
            scheduler.force_due(a.source_id)
            ok = True
        elif a.action == FeedbackActionType.OPEN_CIRCUIT and registry is not None:
            registry.disable(a.source_id, reason=a.reason)
            ok = True
        elif a.action == FeedbackActionType.DOWNGRADE_RIGHTS and registry is not None:
            registry.revoke_rights(a.source_id, *HIGH_RISK_USES, reason=a.reason)
            ok = True
        elif a.action == FeedbackActionType.FLAG_REVIEW and registry is not None:
            registry.set_review_flag(a.source_id, reason=a.reason)
            ok = True
        elif a.action == FeedbackActionType.CLEAR_REVIEW and registry is not None:
            registry.clear_review_flag(a.source_id)
            ok = True
        if ok:
            applied.append(a)
    return applied


class FeedbackEngine:
    """会话态反馈引擎：持有 strike 计数，串联 compute + apply，供 CronRunner 每 tick 调用。"""

    def __init__(self, registry: Optional[SourceRegistry] = None,
                 scheduler: Optional[Scheduler] = None,
                 policy: Optional[SlaFeedbackPolicy] = None,
                 high_risk_uses: tuple[str, ...] = HIGH_RISK_USES):
        self.registry = registry
        self.scheduler = scheduler
        self.policy = policy or SlaFeedbackPolicy()
        self.high_risk_uses = high_risk_uses
        self.strike_counts: dict[str, int] = {}

    def run(self, sla_reports: dict[str, SlaReport], pii_counts: dict[str, int]) -> list[FeedbackAction]:
        sla_actions, self.strike_counts = compute_sla_feedback(
            sla_reports, self.strike_counts, self.policy)
        pii_actions = compute_pii_feedback(pii_counts, self.high_risk_uses)
        all_actions = sla_actions + pii_actions
        apply_actions(all_actions, self.registry, self.scheduler)
        return all_actions
