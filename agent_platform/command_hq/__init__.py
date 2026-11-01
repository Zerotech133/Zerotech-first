# -*- coding: utf-8 -*-
"""指挥部（Command HQ）五角色智能体包（2026-11-01 K-ORG-20261101-HQROLE）。

本包当前交付 W-C1（专项计划 §3/§4）：

- ``role_contract``：RoleOpinion v1 契约（校验/规范化/序列化/去重；无证据禁止 verified）；
- ``roles``：田丰/诸葛亮/赵云/黄忠/关羽只读适配器（复用 org/registry 身份与
  engine/router 绑定；只出意见，不执行、不调模型）。

后续编排器/共识/飞书卡/执行门（W-C2..W-C4）不在本包本批范围。
"""
from .role_contract import (  # noqa: F401
    DIGEST_RE,
    EVIDENCE_KINDS,
    POSITIONS,
    REQUIRED_FIELDS,
    ROLES,
    SCHEMA,
    RoleOpinionError,
    dedupe_key,
    dedupe_opinions,
    deserialize_opinion,
    make_opinion,
    normalize_opinion,
    opinion_fingerprint,
    serialize_opinion,
    validate_evidence,
    validate_opinion,
)
from .roles import (  # noqa: F401
    ADAPTER_KEYS,
    EXECUTES,
    READ_ONLY,
    ROLE_ORDER,
    ROLE_SPECS,
    RoleAdapter,
    adapters,
    all_role_bindings,
    compose_prompt,
    guanyu,
    huangzhong,
    load_identity,
    load_router_bindings,
    role_binding,
    tianfeng,
    zhaoyun,
    zhuge,
)

__all__ = [
    "ADAPTER_KEYS", "DIGEST_RE", "EVIDENCE_KINDS", "EXECUTES", "POSITIONS",
    "READ_ONLY", "REQUIRED_FIELDS", "ROLES", "ROLE_ORDER", "ROLE_SPECS",
    "RoleAdapter", "RoleOpinionError", "SCHEMA", "adapters", "all_role_bindings",
    "compose_prompt", "dedupe_key", "dedupe_opinions", "deserialize_opinion",
    "guanyu", "huangzhong", "load_identity", "load_router_bindings",
    "make_opinion", "normalize_opinion", "opinion_fingerprint", "role_binding",
    "serialize_opinion", "tianfeng", "validate_evidence", "validate_opinion",
    "zhaoyun", "zhuge",
]
