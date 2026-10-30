# -*- coding: utf-8 -*-
"""开源核心：类型定义（无第三方依赖）。"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Bar:
    date: str  # YYYYMMDD
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


@dataclass
class Snapshot:
    code: str
    name: str = ""
    as_of: str = ""  # YYYYMMDD
    bars: List[Bar] = field(default_factory=list)
    fundamentals: List[Dict[str, Any]] = field(default_factory=list)  # end_date/...
    valuation: List[Dict[str, Any]] = field(default_factory=list)     # trade_date/ps/pe/pb
    events: List[Dict[str, Any]] = field(default_factory=list)        # date/category/title/source
    meta: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CheckResult:
    name: str
    passed: bool
    score: Optional[float] = None
    issues: List[str] = field(default_factory=list)


@dataclass
class VerdictResult:
    rating: str
    score: float
    confidence: float
    horizon: str = ""
    thesis: str = ""
    key_signals: List[str] = field(default_factory=list)
    triggers: List[str] = field(default_factory=list)
    invalidations: List[str] = field(default_factory=list)
    factors: Dict[str, float] = field(default_factory=dict)
    policy_version: str = "demo-1"
    disclaimer: str = "研究分析，不构成投资建议"
