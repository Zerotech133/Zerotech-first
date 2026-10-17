"""统一研究、决策与模拟执行契约。

本模块只依赖 Python 标准库，不接入业务主链。所有契约均可通过
``to_dict`` / ``from_dict`` 与 JSON 边界互操作，并为缺失的追踪字段生成
默认 ID，便于旧调用方渐进接入。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Type, TypeVar


TraceId = str
RequestId = str
CorrelationId = str
IdempotencyKey = str

# 小写别名保留给按协议字段名书写的调用方；实际值仍为普通字符串。
trace_id = TraceId
request_id = RequestId
correlation_id = CorrelationId
idempotency_key = IdempotencyKey


T = TypeVar("T", bound="Contract")


def new_id() -> str:
    """生成不依赖第三方库的默认契约 ID。"""
    return uuid.uuid4().hex


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@dataclass
class Contract:
    """所有统一契约共享的协议元数据。"""

    schema_version: int = 1
    trace_id: TraceId = field(default_factory=new_id)
    request_id: RequestId = field(default_factory=new_id)
    correlation_id: CorrelationId = field(default_factory=new_id)
    idempotency_key: IdempotencyKey = field(default_factory=new_id)
    caller: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return _jsonable(asdict(self))

    def to_json(self) -> str:
        return json.dumps(
            self.to_dict(), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )

    @classmethod
    def from_dict(cls: Type[T], payload: Mapping[str, Any]) -> T:
        if not isinstance(payload, Mapping):
            raise TypeError("契约必须从 JSON 对象构造")
        known = {item.name for item in fields(cls)}
        values = {key: value for key, value in payload.items() if key in known}
        return cls(**values)

    @classmethod
    def from_json(cls: Type[T], payload: str) -> T:
        return cls.from_dict(json.loads(payload))


@dataclass
class IntelItem(Contract):
    """情报输入：来源、主题和原始内容均保持可序列化。"""

    item_id: str = field(default_factory=new_id)
    title: str = ""
    summary: str = ""
    source: str = ""
    source_ref: str = ""
    observed_at: Optional[str] = None
    content: Dict[str, Any] = field(default_factory=dict)
    tags: List[str] = field(default_factory=list)


@dataclass
class Evidence(Contract):
    """支撑研究结论的可追溯证据。"""

    evidence_id: str = field(default_factory=new_id)
    source: str = ""
    source_ref: str = ""
    statement: str = ""
    observed_at: Optional[str] = None
    strength: Optional[float] = None
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ResearchReport(Contract):
    """策略或研究岗位产出的报告，不承担下单职责。"""

    report_id: str = field(default_factory=new_id)
    subject: str = ""
    strategy: str = ""
    summary: str = ""
    evidence: List[Evidence] = field(default_factory=list)
    confidence: Optional[float] = None
    fact_hash: str = ""
    prompt_version: str = ""
    strategy_version: str = ""

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ResearchReport":
        values = dict(payload)
        evidence = values.get("evidence", [])
        values["evidence"] = [
            item if isinstance(item, Evidence) else Evidence.from_dict(item)
            for item in evidence
        ]
        return super(ResearchReport, cls).from_dict(values)


@dataclass
class Verdict(Contract):
    """汇总结论件；默认 neutral，避免证据不足被误当作放行。"""

    verdict_id: str = field(default_factory=new_id)
    subject: str = ""
    action: str = "neutral"
    rationale: str = ""
    confidence: Optional[float] = None
    evidence_ids: List[str] = field(default_factory=list)
    report_ids: List[str] = field(default_factory=list)
    fact_hash: str = ""
    decision_id: str = field(default_factory=new_id)


@dataclass
class OrderIntent(Contract):
    """模拟订单意图；仅表达计划，不触发真实交易。"""

    order_id: str = field(default_factory=new_id)
    decision_id: str = ""
    symbol: str = ""
    side: str = ""
    quantity: float = 0.0
    order_type: str = "market"
    limit_price: Optional[float] = None
    environment: str = "paper"
    rationale: str = ""
    status: str = "planned"


@dataclass
class GateDecision(Contract):
    """审批门禁结果：True 放行、False 否决、None 缺证/人工核验。"""

    ALLOW = True
    DENY = False
    REVIEW = None

    gate_id: str = field(default_factory=new_id)
    target_id: str = ""
    decision: Optional[bool] = None
    reason: str = ""
    checks: Dict[str, Any] = field(default_factory=dict)
    decided_by: str = ""

    def __post_init__(self) -> None:
        if self.decision is not None and type(self.decision) is not bool:
            raise TypeError("GateDecision.decision 只能是 True、False 或 None")


__all__ = [
    "TraceId",
    "RequestId",
    "CorrelationId",
    "IdempotencyKey",
    "trace_id",
    "request_id",
    "correlation_id",
    "idempotency_key",
    "new_id",
    "Contract",
    "IntelItem",
    "Evidence",
    "ResearchReport",
    "Verdict",
    "OrderIntent",
    "GateDecision",
]
