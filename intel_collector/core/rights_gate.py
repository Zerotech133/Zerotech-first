# -*- coding: utf-8 -*-
"""采集即服务平台 · 权利闸门 (core/rights_gate.py)

采集即受"源 × 内容类型 × 用途"约束。默认拒绝未显式允许的用途；
训练/外发/商用不随入湖自动开放，必须显式、可追溯批准。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from .contracts import Observation, UseCase


@dataclass
class Decision:
    allowed: bool
    reason: str
    use_case: str


class RightsGate:
    # 高风险用途：即便源登记了 bool=true，也要求显式批准标记，不应被默认继承
    HIGH_RISK = {UseCase.TRAINING.value, UseCase.REDISTRIBUTION.value, UseCase.COMMERCIAL.value}

    def check(self, source_spec, use_case: str) -> Decision:
        if use_case not in UseCase._value2member_map_:
            return Decision(False, f"未知用途 {use_case}", use_case)
        allowed = source_spec.rights.get(use_case, False)
        if not allowed:
            return Decision(False, f"源 {source_spec.source_id} 未授权用途 {use_case}", use_case)
        if use_case in self.HIGH_RISK:
            meta = source_spec.rights_meta.get(use_case, {})
            if not meta.get("explicit_approval"):
                return Decision(False, f"高风险用途 {use_case} 缺乏显式批准", use_case)
        return Decision(True, "通过", use_case)

    def filter_observations(self, observations: Iterable[Observation], use_case: str) -> list[Observation]:
        """按观察上绑定的允许用途过滤（采集时已由闸门绑定）。"""
        out = []
        for o in observations:
            if use_case in o.allowed_use_cases:
                out.append(o)
        return out
