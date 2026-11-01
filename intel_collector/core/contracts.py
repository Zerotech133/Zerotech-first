# -*- coding: utf-8 -*-
"""采集即服务平台 · 核心契约 (core/contracts.py)

统一观测模型 + 权利范围 + 时间语义。所有适配器产出 Observation，store 幂等落盘。
设计北极星（来自 YouTube 语料治理经验）：
  - 采集必须"又笨又诚实"：交付原始 + 血缘 + 失败可见，绝不伪造、绝不解释、绝不静默丢弃。
  - 原始观察不覆盖、不累加；评论身份 (video_id, comment.id) 由 clean 层处理。
  - 时间至少分开：平台发布时间 / collected_at / available_at / first_seen_at。
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class EntityType(str, Enum):
    DISCOVERY_CHANNEL = "discovery_channel"
    CHANNEL_ENUMERATION = "channel_enumeration"
    VIDEO_METADATA = "video_metadata"
    VIDEO_COMMENTS = "video_comments"
    GUBA_PAGE = "guba_page"
    NEWS_PAGE = "news_page"
    NEWS_ITEM = "news_item"
    XQ_FEED = "xq_feed"
    XQ_COMMENTS_PAGE = "xq_comments_page"


class Status(str, Enum):
    """提取状态分账：失败可见，unavailable 不等于已删除。"""
    COMPLETE = "complete"
    EMPTY = "empty"
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    INCOMPLETE = "incomplete"


class UseCase(str, Enum):
    """按"源 × 内容类型 × 用途"登记。默认拒绝未显式允许的用途。"""

    INTERNAL_RETENTION = "internal_retention"   # 原包内部留存
    CLEANING = "cleaning"                       # 清洗与去重
    INDEXING = "indexing"                       # 文本索引
    CANDIDATE_LABELING = "candidate_labeling"   # 主题/实体候选标签
    CONTROLLED_QUERY = "controlled_query"       # 受控查询
    REDISTRIBUTION = "redistribution"           # 再分发
    COMMERCIAL = "commercial"                   # 商用
    TRAINING = "training"                       # 模型训练


class ContentType(str, Enum):
    """内容类型维度（与 UseCase 正交）：meta / comment / media。"""
    METADATA = "metadata"
    COMMENT = "comment"
    MEDIA = "media"


@dataclass(frozen=True)
class SourceClock:
    """时间语义：绝对不能把采集时间倒填为可用/发布时间。"""

    collected_at: str                                  # 本次采集 UTC
    platform_published_at: Optional[str] = None        # 平台发布时间（如有）
    available_at: Optional[str] = None                 # 消费者可见时间（无可靠凭据则留空）
    first_seen_at: Optional[str] = None                # 首次见到（无可靠凭据则留空）

    def as_of_queryable(self) -> bool:
        # PIT 查询必须排除 available_at 未知的记录
        return self.available_at is not None


@dataclass
class Observation:
    """一条不可变观察。原始证据的基本单元。"""

    source_id: str
    snapshot_id: str
    entity_type: str
    entity_id: str
    clock: SourceClock
    status: str
    completeness: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    raw_sha256: Optional[str] = None
    source_payload_sha256: Optional[str] = None
    # 权利快照：本次观察被允许的消费用途（由 RightsGate 在采集时绑定）
    allowed_use_cases: tuple[str, ...] = ()
    # 文本出处（候选标签用）：title / description / comment；非视频本体
    source_field: Optional[str] = None
    schema_version: str = "1.0"

    def __post_init__(self) -> None:
        if isinstance(self.status, Status):
            self.status = self.status.value
        # raw_sha256 缺失则由 payload 计算（脱敏后的规范 JSON）
        if self.raw_sha256 is None:
            self.raw_sha256 = _sha256_json(self.payload)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["clock"] = asdict(self.clock)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Observation":
        clock = SourceClock(**(d.get("clock") or {}))
        return cls(
            source_id=d["source_id"],
            snapshot_id=d["snapshot_id"],
            entity_type=d["entity_type"],
            entity_id=d["entity_id"],
            clock=clock,
            status=d["status"],
            completeness=d.get("completeness"),
            payload=d.get("payload") or {},
            raw_sha256=d.get("raw_sha256"),
            source_payload_sha256=d.get("source_payload_sha256"),
            allowed_use_cases=tuple(d.get("allowed_use_cases") or ()),
            source_field=d.get("source_field"),
            schema_version=d.get("schema_version", "1.0"),
        )


def _sha256_json(obj: Any) -> str:
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
