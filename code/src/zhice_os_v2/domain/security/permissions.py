"""角色权限矩阵。"""
from __future__ import annotations

from zhice_os_v2.domain.security.user import Role

# 权限字符串：view / trade / approve / research / admin
PERMISSIONS = {
    Role.VIEWER: {"view"},
    Role.USER: {"view", "manage_own"},
    Role.RESEARCHER: {"view", "research"},
    Role.TRADER: {"view", "trade"},
    Role.RISK_MANAGER: {"view", "approve", "risk"},
    Role.ADMIN: {"view", "trade", "approve", "research", "risk", "admin"},
}


def has_permission(role: Role, permission: str) -> bool:
    return permission in PERMISSIONS.get(role, set())
