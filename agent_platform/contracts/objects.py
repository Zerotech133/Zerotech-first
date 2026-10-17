# -*- coding: utf-8 -*-
"""M1 统一对象模型补齐：WorkItem / Artifact / Approval。

总体架构（docs/audit/2026-10-17_老田个人AI组一次成型总体架构.md）统一业务主链：
    Goal → Project → Task → WorkItem → Artifact → Review → Decision → Learning

本模块补齐 M0 契约（contracts/__init__.py：IntelItem/Evidence/ResearchReport/
Verdict/OrderIntent/GateDecision）尚未覆盖的三个对象：

- WorkItem  工作项：某业务域内一件可交付的工作；status 为**域内原生状态**
            （如 GZH 的 draft/reviewed/published），禁止跨域映射；
- Artifact  交付物：WorkItem 的产物指针 + 内容指纹（fact_hash=sha256），
            只记录路径与哈希，不复制内容；
- Approval  审批记录：一次受控状态转移的批准凭据（谁、何时、从哪到哪、
            为什么），append-only，幂等键唯一。

纪律（延续 M0 判例）：
- 只依赖标准库；to_dict/from_dict 与 JSON 边界互操作；
- 无源 ID 不伪造：workitem_id 必须能从领域真源 ID 派生或追溯；
- 生成、审查、批准、发布分离：Approval 只记录"批准"这一件事，
  不代替执行，也不代替审查。
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from . import Contract, new_id
except ImportError:  # 独立加载（importlib / 直跑）时回退路径导入
    _AGENT_PLATFORM = str(Path(__file__).resolve().parents[1])
    if _AGENT_PLATFORM not in sys.path:
        sys.path.insert(0, _AGENT_PLATFORM)
    from contracts import Contract, new_id


def fact_hash_of(content: bytes) -> str:
    """交付物内容指纹：sha256 hex。"""
    return hashlib.sha256(content).hexdigest()


@dataclass
class WorkItem(Contract):
    """业务域工作项投影。

    source/source_ref 指向领域真源（如 gzh.drafts_index + 草稿 id）；
    status 永远是域内原生值，接线层不做语义翻译。
    """

    workitem_id: str = field(default_factory=new_id)
    domain: str = ""                 # gzh / academy / research / dev / ppt / personal
    source: str = ""                 # 领域真源标识，如 gzh.drafts_index
    source_ref: str = ""             # 真源内 ID，如草稿 id
    title: str = ""
    status: str = ""                 # 域内原生状态，原样保留
    owner: str = ""                  # 当前负责 Agent/人（org.json 身份）
    project_id: str = ""             # 关联统一 Project（可为空）
    payload: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = self.schema_version
        return data


@dataclass
class Artifact(Contract):
    """交付物指针：路径 + 指纹，不复制内容。

    M3-P1 补全（登记层用）：content_hash 为唯一去重键（空时由
    ArtifactStore 回退 fact_hash）；revision/supersedes 构成可回滚
    supersede 链；source_system/source_ref/produced_by 回答总纲
    「来源、负责人」两问。既有只读投影方（gzh/invest/devtask）
    只用旧字段，新增字段全部带默认值，尾部追加不破坏构造。
    """

    artifact_id: str = field(default_factory=new_id)
    workitem_id: str = ""
    kind: str = ""                   # md / html / report / code ...
    path: str = ""                   # 相对仓库根路径
    fact_hash: str = ""              # sha256(content)
    version: str = "1"
    missing: bool = False            # 真源声明存在但文件缺失时显式标记
    content_hash: str = ""           # M3：唯一去重键 sha256；空则回退 fact_hash
    revision: str = "r1"             # supersede 链代次（r1/r2/...）
    source_system: str = ""          # 产线来源系统，如 gzh.drafts / pitch.decks
    source_ref: str = ""             # 来源内 ID，如草稿 id
    produced_by: str = ""            # 负责人/Agent（org.json 身份）
    supersedes: str = ""             # 被替代的旧 artifact_id（空=首版）

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = self.schema_version
        return data

    def effective_hash(self) -> str:
        """去重键：content_hash 优先，回退 fact_hash（M2 投影同源）。"""
        return self.content_hash or self.fact_hash


@dataclass
class Approval(Contract):
    """审批凭据：一次受控状态转移的批准记录（append-only）。"""

    approval_id: str = field(default_factory=new_id)
    target_type: str = ""            # workitem / task / skill / prompt ...
    target_id: str = ""              # 被批对象（如 workitem_id 或 gzh 草稿 id）
    from_status: str = ""
    to_status: str = ""
    decision: str = "approved"       # approved / rejected（记录裁决，不记录中间态）
    decided_by: str = ""             # 批准人（用户/授权 Agent）
    reason: str = ""
    decided_at: str = ""             # ISO8601 带时区
    idempotency_key: str = ""        # 由调用方给确定性键，如 gzh:<id>:draft>reviewed
    content_hash: str = ""           # M3-P3：批准时点的内容指纹（批 A 发 B 拒绝锚点）

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["schema_version"] = self.schema_version
        return data

    @classmethod
    def from_dict(cls, payload):  # type: (Any) -> "Approval"
        known = {f for f in cls.__dataclass_fields__}
        values = {k: v for k, v in dict(payload).items() if k in known}
        return cls(**values)


def approval_to_json(ap: Approval) -> str:
    return json.dumps(ap.to_dict(), ensure_ascii=False, sort_keys=True)


__all__ = ["WorkItem", "Artifact", "Approval", "fact_hash_of",
           "approval_to_json", "new_id", "Optional"]
