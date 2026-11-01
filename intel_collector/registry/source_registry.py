# -*- coding: utf-8 -*-
"""采集即服务平台 · 源注册表 (registry/source_registry.py)

一源一档：权属、schema、权利范围、SLA、健康度。接入入口即登记。
关键治理约束：每个登记源必须有真实 owner，禁止 UNOWNED（见 res-downloader-py 入库教训）。
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from core.contracts import UseCase


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class SourceSpec:
    source_id: str
    name: str
    category: str                      # open_source_intel / interface / media ...
    owner: str                         # 权属（需田丰归口补 owner，不得 UNOWNED）
    contact: str = ""
    schema_version: str = "1.0"
    # 权利：use_case -> bool；缺省即拒绝
    rights: dict[str, bool] = field(default_factory=dict)
    # 高风险用途需显式批准元数据
    rights_meta: dict[str, dict] = field(default_factory=dict)
    # 内容类型范围（meta/comment/media）
    content_types: list[str] = field(default_factory=list)
    sla: dict = field(default_factory=dict)        # freshness_minutes / completeness_target / max_failures
    enabled: bool = True
    health: dict = field(default_factory=dict)     # consecutive_failures / last_success_at ...
    note: str = ""

    def require_rights(self, *use_cases: str) -> "SourceSpec":
        for uc in use_cases:
            self.rights.setdefault(uc, False)
        return self

    def grant(self, use_case: str, explicit: bool = False) -> "SourceSpec":
        self.rights[use_case] = True
        if use_case in (UseCase.TRAINING.value, UseCase.REDISTRIBUTION.value, UseCase.COMMERCIAL.value):
            self.rights_meta[use_case] = {"explicit_approval": explicit, "granted_at": _now()}
        return self

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SourceSpec":
        return cls(**d)


class SourceRegistry:
    def __init__(self, path: Optional[str] = None):
        self.path = path
        self._specs: dict[str, SourceSpec] = {}
        if path and os.path.exists(path):
            self.load()

    def register(self, spec: SourceSpec) -> SourceSpec:
        # 校验：每个登记源必须有 owner，且不得 UNOWNED
        if not spec.owner or spec.owner.upper() == "UNOWNED":
            raise ValueError(f"源 {spec.source_id} 必须指定真实 owner，禁止 UNOWNED")
        self._specs[spec.source_id] = spec
        self.save()
        return spec

    def get(self, source_id: str) -> Optional[SourceSpec]:
        return self._specs.get(source_id)

    def list(self) -> list[SourceSpec]:
        return list(self._specs.values())

    def update_health(self, source_id: str, *, ok: bool, detail: str = "") -> None:
        spec = self._specs.get(source_id)
        if not spec:
            return
        h = spec.health
        if ok:
            h["consecutive_failures"] = 0
            h["last_success_at"] = _now()
        else:
            h["consecutive_failures"] = h.get("consecutive_failures", 0) + 1
            h["last_failure_at"] = _now()
            h["last_failure_detail"] = detail
            if h["consecutive_failures"] >= spec.sla.get("max_failures", 5):
                spec.enabled = False
                h["auto_disabled_at"] = _now()
        self.save()

    def disable(self, source_id: str, reason: str = "") -> None:
        spec = self._specs.get(source_id)
        if spec:
            spec.enabled = False
            spec.health["disabled_reason"] = reason
            spec.health["disabled_at"] = _now()
            self.save()

    def revoke_rights(self, source_id: str, *use_cases: str, reason: str = "") -> None:
        """反馈环用：合规自动降级——即便曾授权，也撤掉指定高风险用途并记录。"""
        spec = self._specs.get(source_id)
        if not spec:
            return
        changed = False
        for uc in use_cases:
            if spec.rights.get(uc) is True:
                spec.rights[uc] = False
                meta = spec.rights_meta.setdefault(uc, {})
                meta["revoked_at"] = _now()
                if reason:
                    meta["revoke_reason"] = reason
                changed = True
        if changed:
            self.save()

    def set_review_flag(self, source_id: str, reason: str = "") -> None:
        """反馈环用：标记人工复核（连续违约/PII 命中）。"""
        spec = self._specs.get(source_id)
        if not spec:
            return
        spec.health["review_flag"] = reason or "待人工复核"
        spec.health["review_flag_at"] = _now()
        self.save()

    def clear_review_flag(self, source_id: str) -> None:
        """反馈环用：解除复核标记（恢复）。"""
        spec = self._specs.get(source_id)
        if not spec:
            return
        spec.health.pop("review_flag", None)
        spec.health.pop("review_flag_at", None)
        self.save()

    def save(self) -> None:
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: v.to_dict() for k, v in self._specs.items()},
                      f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def load(self) -> None:
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        self._specs = {k: SourceSpec.from_dict(v) for k, v in data.items()}
