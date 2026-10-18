"""会员 / 订阅 应用服务 + 定价配置（P1）。

- MembershipService：发放/续费/有效性判定/到期扫描（到期→暂停在跑自建策略）。
- SubscriptionService：订阅/续订/有效性/活跃订阅者名单（供信号扇出）。
- 定价：存 v2_app_state[key='billing_pricing']（复用 app_state_store，重启不丢，
  DB 失败回退内存）。会员月费默认 9.9 USDT、策略订阅默认 99 USDT，可后台按策略覆盖。

按月 = 30 天（timedelta(days=30*months)），续费叠加：valid_until = max(now, 旧)+N月。
"""
from __future__ import annotations

import logging

from zhice_os_v2.domain.billing.entitlement import (
    Membership,
    is_member_tier_active,
    is_subscription_active,
    membership_effective_status,
    subscription_effective_status,
)
from zhice_os_v2.infrastructure.repositories.membership_repository import (
    MembershipRepository,
    SubscriptionRepository,
)

logger = logging.getLogger(__name__)

_PRICING_KEY = "billing_pricing"
_PRICING_DEFAULT: dict = {
    "membership_monthly_usdt": 9.9,
    "strategy_default_monthly_usdt": 99,
    "strategy_overrides": {},  # {strategy_id: monthly_usdt}
}


# ---------------- 定价配置（v2_app_state） ----------------
def get_pricing() -> dict:
    from zhice_os_v2.api.routers.app_state_store import _load_state

    return _load_state(_PRICING_KEY, _PRICING_DEFAULT)


def set_pricing(patch: dict) -> dict:
    """部分更新定价（仅认识的键），持久化并返回全量。"""
    from zhice_os_v2.api.routers.app_state_store import _save_state

    state = get_pricing()
    if "membership_monthly_usdt" in patch:
        state["membership_monthly_usdt"] = float(patch["membership_monthly_usdt"])
    if "strategy_default_monthly_usdt" in patch:
        state["strategy_default_monthly_usdt"] = float(
            patch["strategy_default_monthly_usdt"]
        )
    if "strategy_overrides" in patch and isinstance(patch["strategy_overrides"], dict):
        overrides = dict(state.get("strategy_overrides") or {})
        for sid, price in patch["strategy_overrides"].items():
            overrides[str(sid)] = float(price)
        state["strategy_overrides"] = overrides
    _save_state(_PRICING_KEY, state)
    return state


def get_membership_price() -> float:
    return float(get_pricing().get("membership_monthly_usdt", 9.9))


def get_strategy_price(strategy_id: str) -> float:
    p = get_pricing()
    overrides = p.get("strategy_overrides") or {}
    if strategy_id in overrides:
        return float(overrides[strategy_id])
    return float(p.get("strategy_default_monthly_usdt", 99))


# ---------------- 会员 ----------------
class MembershipService:
    def __init__(self, repo: MembershipRepository | None = None) -> None:
        self._repo = repo or MembershipRepository()

    def get(self, user_id: str) -> Membership | None:
        return self._repo.get(user_id)

    def get_or_create_free(self, user_id: str) -> Membership:
        return self._repo.get_or_create_free(user_id)

    def is_member(self, user_id: str) -> bool:
        """是否有效付费会员（member 且未过期）。业务写门禁用。free→False。"""
        return is_member_tier_active(self._repo.get(user_id))

    def status_view(self, user_id: str) -> dict:
        """前端展示态：{tier, valid_until, is_member, is_permanent, effective_status}。

        无记录按 free 呈现。is_permanent/effective_status 供自助页区分"永久有效"与
        过期/撤销（permanent 的 valid_until 为 None，旧字段会误显示为"—"）。
        """
        m = self._repo.get(user_id)
        if m is None:
            return {
                "tier": "free", "valid_until": None, "is_member": False,
                "is_permanent": False, "effective_status": "free",
            }
        return {
            "tier": m.tier,
            "valid_until": m.valid_until.isoformat() if m.valid_until else None,
            "is_member": is_member_tier_active(m),
            "is_permanent": m.is_permanent,
            "effective_status": membership_effective_status(m),
        }

    def grant_member(self, user_id: str, months: int) -> Membership:
        """发放/续费付费会员 months 个月。叠加续费：从 max(now, 旧到期) 起算。

        续期计算下沉到 repo.extend_member，在行锁内"读旧→叠加→写"，防并发续费丢更新。
        """
        if months <= 0:
            raise ValueError("billing.invalid_months")
        return self._repo.extend_member(user_id, months)

    def expire_due(self) -> list[str]:
        """到期扫描：付费会员过期 → 暂停其全部在跑自建策略 → 标记 expired。

        返回被处理的 user_id 列表。best-effort：单用户失败不阻断其余。
        """
        from zhice_os_v2.application.services.user_strategy_service import (
            UserStrategyService,
        )
        from zhice_os_v2.config.settings import get_settings
        from zhice_os_v2.domain.security.user import Role, User

        expired = self._repo.list_expired_member_ids()
        if not expired:
            return []
        # db（PersistencePort）：expire_due 只用 list_owned→repo，不触 db 方法，但
        # UserStrategyService 构造需要它——按 settings 选后端，勿硬编（对齐 get_db）。
        if get_settings().persistence_backend == "sql":
            from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import (
                SqlAlchemyDatabase,
            )

            db = SqlAlchemyDatabase()
        else:
            from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

            db = InMemoryDatabase()
        svc = UserStrategyService(db)
        acted: list[str] = []
        for uid in expired:
            try:
                user = User(user_id=uid, username="", role=Role.USER)
                for inst in svc.list_owned(user, enabled=True):
                    try:
                        svc.disable(user, inst.user_strategy_id)
                    except Exception:  # noqa: BLE001
                        logger.exception(
                            "expire_due 停实例失败 uid=%s iid=%s",
                            uid,
                            inst.user_strategy_id,
                        )
                self._repo.mark_expired(uid)
                acted.append(uid)
            except Exception:  # noqa: BLE001 - 单用户失败不阻断整体扫描
                logger.exception("expire_due 处理用户失败 uid=%s", uid)
        return acted


# ---------------- 订阅 ----------------
class SubscriptionService:
    def __init__(self, repo: SubscriptionRepository | None = None) -> None:
        self._repo = repo or SubscriptionRepository()

    def subscribe(self, user_id: str, strategy_id: str, months: int):
        """订阅/续订某策略 months 个月。叠加续订：从 max(now, 旧到期) 起算。

        续期计算下沉到 repo.extend_subscription，在行锁内原子叠加，防并发续订丢更新。
        """
        if months <= 0:
            raise ValueError("billing.invalid_months")
        return self._repo.extend_subscription(user_id, strategy_id, months)

    def is_subscribed(self, user_id: str, strategy_id: str) -> bool:
        return is_subscription_active(self._repo.get(user_id, strategy_id))

    def list_active_subscriber_ids(self, strategy_id: str) -> list[str]:
        return self._repo.list_active_subscriber_ids(strategy_id)

    def list_for_user(self, user_id: str) -> list[dict]:
        out = []
        for s in self._repo.list_for_user(user_id):
            out.append(
                {
                    "strategy_id": s.strategy_id,
                    "valid_until": s.valid_until.isoformat() if s.valid_until else None,
                    "active": is_subscription_active(s),
                    "is_permanent": s.is_permanent,
                    "effective_status": subscription_effective_status(s),
                }
            )
        return out
