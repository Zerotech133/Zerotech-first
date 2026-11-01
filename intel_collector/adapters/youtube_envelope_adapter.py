# -*- coding: utf-8 -*-
"""参考适配器：YouTube 公开语料交付包（系统外 ZIP，只读接入）

证明平台血缘契约与 YouTube envelope 完全兼容：保留 snapshot_id / entity_type /
entity_id / collected_at / status / completeness / raw_sha256 / source_payload_sha256。

真实接入需 source 登记 + 权利审批 + 映射重建（见 lg-97d585b261bb 待田丰签发）；
本适配器仅做 envelope -> Observation 的映射，原型用合成 envelope，不读取 2.8GB 真实包。
重点：YouTube 包未声明训练许可，适配器默认不绑定训练用途。
"""
from __future__ import annotations

from typing import Iterable

from core.base_adapter import BaseAdapter
from core.contracts import Observation, SourceClock, Status, UseCase


class YouTubeEnvelopeAdapter(BaseAdapter):
    """输入：单个 observation envelope dict（来自 ZIP 内 observations/*.jsonl.gz）。"""

    def collect(self, targets: Iterable[dict], **kwargs) -> list[Observation]:
        out: list[Observation] = []
        for env in targets:
            o = self._from_envelope(env)
            if o:
                out.append(o)
        return out

    def _from_envelope(self, env: dict) -> Observation | None:
        sid = env.get("snapshot_id")
        if not sid:
            return None
        info = (env.get("payload") or {}).get("info", {})
        clock = SourceClock(
            collected_at=env.get("collected_at"),
            platform_published_at=info.get("published_at"),
        )
        status = env.get("status", Status.COMPLETE.value)
        # 默认仅授权留存/清洗/受控查询；训练需显式批准，此处不绑定
        allowed = (UseCase.INTERNAL_RETENTION.value, UseCase.CLEANING.value,
                   UseCase.CONTROLLED_QUERY.value)
        return Observation(
            source_id=self.source_id,
            snapshot_id=sid,
            entity_type=env.get("entity_type", "unknown"),
            entity_id=env.get("entity_id", ""),
            clock=clock,
            status=status,
            completeness=env.get("completeness"),
            payload=env.get("payload", {}),
            raw_sha256=env.get("raw_sha256"),
            source_payload_sha256=env.get("source_payload_sha256"),
            allowed_use_cases=allowed,
        )
