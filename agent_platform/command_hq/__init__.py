# -*- coding: utf-8 -*-

# W-C2 编排器（K-ORG-20261101-HQORCH；本集成分支以 HQROLE 正式契约为唯一契约源）
from .orchestrator import (  # noqa: F401
    Orchestrator,
    OrchestratorError,
    PlanError,
    StateError,
    TaskNode,
    normalize_plan,
    validate_dag,
    STATES,
    TRANSITIONS,
)

__all__ = [
    # W-C1（HQROLE）
    "ADAPTER_KEYS", "DIGEST_RE", "EVIDENCE_KINDS", "EXECUTES", "POSITIONS",
    "READ_ONLY", "REQUIRED_FIELDS", "ROLES", "ROLE_ORDER", "ROLE_SPECS",
    "RoleAdapter", "RoleOpinionError", "SCHEMA", "adapters", "all_role_bindings",
    "compose_prompt", "dedupe_key", "dedupe_opinions", "deserialize_opinion",
    "guanyu", "huangzhong", "load_identity", "load_router_bindings",
    "make_opinion", "normalize_opinion", "opinion_fingerprint", "role_binding",
    "serialize_opinion", "tianfeng", "validate_evidence", "validate_opinion",
    "zhaoyun", "zhuge",
    # W-C2（HQORCH）
    "Orchestrator", "OrchestratorError", "PlanError", "StateError",
    "TaskNode", "normalize_plan", "validate_dag", "STATES", "TRANSITIONS",
]
