# -*- coding: utf-8 -*-
"""采集即服务平台 · 适配器基类 (core/base_adapter.py)

定义 collect() -> Observation[] 的统一契约。加一个新源 = 写一个适配器 + 登记权利，
不重写管线。这是把"脚本"变成"能力"的关键。
"""
from __future__ import annotations

import hashlib
import json
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from .contracts import Observation, SourceClock, Status, _sha256_json


class BaseAdapter(ABC):
    source_id: str

    def __init__(self, source_id: str):
        self.source_id = source_id

    @abstractmethod
    def collect(self, targets: Iterable[str], **kwargs) -> list[Observation]:
        """采集给定目标，返回不可变观察列表。实现需只读、限速、失败可见。"""
        raise NotImplementedError

    # ---- 通用工具：适配器实现可复用 ----

    def make_observation(
        self,
        *,
        entity_type: str,
        entity_id: str,
        payload: dict[str, Any],
        status: Status | str = Status.COMPLETE,
        completeness: Optional[str] = None,
        collected_at: Optional[str] = None,
        platform_published_at: Optional[str] = None,
        available_at: Optional[str] = None,
        snapshot_id: Optional[str] = None,
        source_field: Optional[str] = None,
        allowed_use_cases: tuple[str, ...] = (),
    ) -> Observation:
        collected_at = collected_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if snapshot_id is None:
            basis = json.dumps(
                {"s": self.source_id, "e": entity_type, "i": entity_id, "t": collected_at},
                ensure_ascii=False, sort_keys=True,
            )
            snapshot_id = "snap_" + hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]
        return Observation(
            source_id=self.source_id,
            snapshot_id=snapshot_id,
            entity_type=entity_type,
            entity_id=entity_id,
            clock=SourceClock(
                collected_at=collected_at,
                platform_published_at=platform_published_at,
                available_at=available_at,
            ),
            status=status.value if isinstance(status, Status) else status,
            completeness=completeness,
            payload=payload,
            raw_sha256=_sha256_json(payload),
            allowed_use_cases=tuple(allowed_use_cases),
            source_field=source_field,
        )

    @staticmethod
    def throttle(seconds: float) -> None:
        if seconds and seconds > 0:
            time.sleep(seconds)
