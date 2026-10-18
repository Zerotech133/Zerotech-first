"""会员资格 / 策略订阅：纯领域模型 + 有效性判定（懒过期）。

真实有效性一律以 valid_until 与 now 的比较为准（懒判定），不依赖 status 字段——
status 只是到期扫描写回的展示标记，可能滞后。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from zhice_os_v2.domain.ontology.objects import utc_now


@dataclass
class Membership:
    user_id: str
    tier: str = "free"  # free | member
    valid_until: datetime | None = None  # None=永久（free / is_permanent）
    status: str = "active"  # stored_status: active | expired | revoked（仅展示，可滞后）
    is_permanent: bool = False  # B1：永久会员（valid_until 无意义）
    revoked_at: datetime | None = None  # B1：撤销时刻（非空即已撤销）
    revoked_by_user_id: str | None = None
    version: int = 1  # 乐观锁版本；0=无记录投影（missing）
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class StrategySubscription:
    user_id: str
    strategy_id: str
    valid_until: datetime | None = None  # 永久订阅为 None
    status: str = "active"  # stored_status: active | expired | revoked
    is_permanent: bool = False
    revoked_at: datetime | None = None
    revoked_by_user_id: str | None = None
    version: int = 1
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _as_aware(dt: datetime) -> datetime:
    """sqlite 方言可能读回 naive datetime（存的是 UTC 值）——补 UTC 再比较，
    否则跨时区比较会错判有效性。"""
    from datetime import UTC

    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def missing_membership(user_id: str) -> Membership:
    """无会员行的虚拟投影：free / version 0（首次设置须提交 version=0）。"""
    return Membership(user_id=user_id, tier="free", status="active", version=0)


def membership_effective_status(
    m: Membership | None, now: datetime | None = None
) -> str:
    """会员实时状态：free / active / permanent / expired / revoked（唯一真相）。

    优先级：缺失或 free → free；撤销 → revoked；永久 → permanent；
    valid_until>=now → active；否则 expired。stored status 不参与判定。
    """
    if m is None or m.tier == "free":
        return "free"
    if m.revoked_at is not None:
        return "revoked"
    if m.is_permanent:
        return "permanent"
    if m.valid_until is not None:
        now = now or utc_now()
        if _as_aware(m.valid_until) >= now:
            return "active"
    return "expired"


def subscription_effective_status(
    s: StrategySubscription, now: datetime | None = None
) -> str:
    """订阅实时状态：active / permanent / expired / revoked。"""
    if s.revoked_at is not None:
        return "revoked"
    if s.is_permanent:
        return "permanent"
    if s.valid_until is not None:
        now = now or utc_now()
        if _as_aware(s.valid_until) >= now:
            return "active"
    return "expired"


def is_membership_active(m: Membership | None, now: datetime | None = None) -> bool:
    """会员是否有效（含 free 永久有效）。仅用于历史兼容语义。"""
    status = membership_effective_status(m, now)
    return status in ("free", "active", "permanent")


def is_member_tier_active(m: Membership | None, now: datetime | None = None) -> bool:
    """是否为“有效付费会员”（member 且 active/permanent）。free/过期/撤销 → False。"""
    return membership_effective_status(m, now) in ("active", "permanent")


def is_subscription_active(
    s: StrategySubscription | None, now: datetime | None = None
) -> bool:
    """订阅是否有效：未撤销 且（永久 或 valid_until>=now）。None（无记录）=无效。"""
    if s is None:
        return False
    return subscription_effective_status(s, now) in ("active", "permanent")


def membership_view(
    m: Membership | None, *, user_id: str, now: datetime | None = None
) -> dict[str, object]:
    """会员对外视图：分离 stored_status 与实时 effective_status；缺失投影 version 0。"""
    if m is None:
        m = missing_membership(user_id)
    effective = membership_effective_status(m, now)
    return {
        "tier": m.tier,
        "stored_status": m.status,
        "effective_status": effective,
        "is_active": effective in ("active", "permanent"),
        "is_permanent": m.is_permanent,
        "valid_until": m.valid_until.isoformat() if m.valid_until else None,
        "revoked_at": m.revoked_at.isoformat() if m.revoked_at else None,
        "version": m.version,
    }


def subscription_view(
    s: StrategySubscription, now: datetime | None = None
) -> dict[str, object]:
    """订阅对外视图：分离 stored_status 与实时 effective_status。"""
    effective = subscription_effective_status(s, now)
    return {
        "strategy_id": s.strategy_id,
        "stored_status": s.status,
        "effective_status": effective,
        "is_active": effective in ("active", "permanent"),
        "is_permanent": s.is_permanent,
        "valid_until": s.valid_until.isoformat() if s.valid_until else None,
        "revoked_at": s.revoked_at.isoformat() if s.revoked_at else None,
        "version": s.version,
    }
