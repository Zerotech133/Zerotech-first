"""用户与权限模型。"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from zhice_os_v2.domain.ontology.objects import utc_now


class Role(StrEnum):
    ADMIN = "ADMIN"
    USER = "USER"
    TRADER = "TRADER"
    RESEARCHER = "RESEARCHER"
    RISK_MANAGER = "RISK_MANAGER"
    VIEWER = "VIEWER"


@dataclass
class User:
    user_id: str
    username: str
    role: Role = Role.VIEWER
    is_active: bool = True
    locale: str = "zh"
    email: str | None = None
    telegram_chat_id: str | None = None
    # 最近一次改密时刻（3c：iat 早于它的 token 一律 401）。None=从未改密/旧库无值。
    password_changed_at: datetime | None = None
    # 单元 A：会话吊销代数（迁移 0029）。token 携带此值，与数据库当前值不一致即 401。
    auth_version: int = 1
    created_at: datetime = field(default_factory=utc_now)
