# -*- coding: utf-8 -*-
"""指挥部（HQ）包：W-C1 角色契约占位 + W-C2 编排器 MVP（K-ORG-20261101-HQORCH）。"""

from .role_contract import (  # noqa: F401
    ROLES,
    POSITIONS,
    EVIDENCE_KINDS,
    RoleError,
    RoleOpinion,
    dedupe_opinions,
)
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
    "ROLES", "POSITIONS", "EVIDENCE_KINDS", "RoleError", "RoleOpinion",
    "dedupe_opinions", "Orchestrator", "OrchestratorError", "PlanError",
    "StateError", "TaskNode", "normalize_plan", "validate_dag",
    "STATES", "TRANSITIONS",
]
