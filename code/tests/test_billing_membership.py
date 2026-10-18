"""P1 计费/权益：领域谓词 + 会员/订阅服务 + 定价配置 + 到期扫描。

隔离内存 sqlite（同 test_user_management.py 范式）：create_all 建全部表（含
v2_membership / v2_strategy_subscription / v2_app_state / v2_user_strategy_instance），
monkeypatch 各仓储模块的 get_session 到同一 sqlite，覆盖 conftest 的 _guard_real_pg。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

# 本模块测计费门禁本体，用真实 MembershipService.is_member（豁免 conftest 的
# _default_active_membership 放行）。
pytestmark = pytest.mark.own_membership


# ---------------- 纯领域谓词（无 DB） ----------------
def test_free_membership_always_active():
    from zhice_os_v2.domain.billing.entitlement import (
        Membership,
        is_member_tier_active,
        is_membership_active,
    )

    m = Membership(user_id="u1", tier="free", valid_until=None)
    assert is_membership_active(m) is True
    # free 不是“付费会员”，业务写门禁应拒
    assert is_member_tier_active(m) is False


def test_member_tier_active_by_valid_until():
    from zhice_os_v2.domain.billing.entitlement import Membership, is_member_tier_active

    now = datetime.now(UTC)
    future = Membership(user_id="u", tier="member", valid_until=now + timedelta(days=1))
    past = Membership(user_id="u", tier="member", valid_until=now - timedelta(days=1))
    assert is_member_tier_active(future) is True
    assert is_member_tier_active(past) is False
    assert is_member_tier_active(None) is False


def test_subscription_active_by_valid_until():
    from zhice_os_v2.domain.billing.entitlement import (
        StrategySubscription,
        is_subscription_active,
    )

    now = datetime.now(UTC)
    ok = StrategySubscription("u", "s1", valid_until=now + timedelta(days=1))
    gone = StrategySubscription("u", "s1", valid_until=now - timedelta(days=1))
    assert is_subscription_active(ok) is True
    assert is_subscription_active(gone) is False
    assert is_subscription_active(None) is False


# ---------------- 服务（内存 sqlite） ----------------
@pytest.fixture()
def billing_env(monkeypatch):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.api.routers.app_state_store as app_state_mod
    import zhice_os_v2.infrastructure.repositories.membership_repository as mem_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_mod
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(mem_mod, "get_session", lambda: Session())
    monkeypatch.setattr(inst_mod, "get_session", lambda: Session())
    monkeypatch.setattr(app_state_mod, "get_session", lambda: Session())
    # 清空 app_state 内存兜底层，避免跨用例污染定价读取
    app_state_mod._memory_state.clear()
    return Session


def test_register_gets_free_then_gate_blocks(billing_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    svc = MembershipService()
    m = svc.get_or_create_free("u-free")
    assert m.tier == "free"
    # 免费会员不是付费会员 → 业务写门禁判定为 False
    assert svc.is_member("u-free") is False


def test_grant_member_then_is_member(billing_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    svc = MembershipService()
    svc.get_or_create_free("u1")
    svc.grant_member("u1", months=1)
    assert svc.is_member("u1") is True
    view = svc.status_view("u1")
    assert view["tier"] == "member" and view["is_member"] is True
    # B3-6：自助页需 is_permanent/effective_status 区分"永久有效"与过期/撤销。
    assert view["is_permanent"] is False
    assert view["effective_status"] == "active"


def test_grant_member_stacks_renewal(billing_env):
    from zhice_os_v2.application.services.membership_service import MembershipService

    svc = MembershipService()
    m1 = svc.grant_member("u2", months=1)
    m2 = svc.grant_member("u2", months=1)  # 续费叠加，不覆盖
    # 两次各 30 天，第二次从第一次到期起算 → 约 60 天后
    delta_days = (m2.valid_until - m1.valid_until).days
    assert 29 <= delta_days <= 31


def test_expire_due_marks_expired(billing_env):
    from zhice_os_v2.application.services.membership_service import MembershipService
    from zhice_os_v2.infrastructure.repositories.membership_repository import (
        MembershipRepository,
    )

    repo = MembershipRepository()
    # 造一个已过期的付费会员
    repo.set_member("u-exp", valid_until=datetime.now(UTC) - timedelta(days=1))
    assert "u-exp" in repo.list_expired_member_ids()
    acted = MembershipService().expire_due()
    assert "u-exp" in acted
    # status 置 expired；is_member 仍 False（懒判定过期）
    assert MembershipService().is_member("u-exp") is False
    assert repo.get("u-exp").status == "expired"


def test_subscription_flow(billing_env):
    from zhice_os_v2.application.services.membership_service import SubscriptionService

    svc = SubscriptionService()
    svc.subscribe("u3", "strat-A", months=1)
    assert svc.is_subscribed("u3", "strat-A") is True
    assert svc.is_subscribed("u3", "strat-B") is False
    assert "u3" in svc.list_active_subscriber_ids("strat-A")
    assert "u3" not in svc.list_active_subscriber_ids("strat-B")


# ---------------- 定价 ----------------
def test_pricing_defaults_and_override(billing_env):
    from zhice_os_v2.application.services.membership_service import (
        get_membership_price,
        get_strategy_price,
        set_pricing,
    )

    assert get_membership_price() == 9.9
    assert get_strategy_price("any-strat") == 99  # 默认
    set_pricing({"strategy_overrides": {"vip-strat": 199}})
    assert get_strategy_price("vip-strat") == 199  # override 优先
    assert get_strategy_price("other-strat") == 99  # 未覆盖仍回落默认
    set_pricing({"membership_monthly_usdt": 12.5})
    assert get_membership_price() == 12.5


# ---------------- 门禁 e2e（free→402，发放后放行） ----------------
def test_membership_gate_blocks_free_then_allows_member(billing_env):
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.membership_service import MembershipService
    from zhice_os_v2.application.services.token_service import create_token

    c = TestClient(app)
    headers = {"Authorization": f"Bearer {create_token('u-gate', 'gate', 'USER')}"}
    body = {"strategy_name": "门禁测试", "market": "CN"}

    # 免费用户建策略 → 402 billing.membership_required
    r = c.post("/api/v1/my/strategies", headers=headers, json=body)
    assert r.status_code == 402, r.text
    assert r.json()["detail"]["code"] == "billing.membership_required"

    # 发放会员后 → 不再被会员门禁拦（放行进入端点主链）
    MembershipService().grant_member("u-gate", months=1)
    r2 = c.post("/api/v1/my/strategies", headers=headers, json=body)
    assert r2.status_code != 402, r2.text


def test_grantable_strategies_endpoint_no_500(billing_env):
    """B1-5 回归：GET /billing/strategies 真实路径不得 500。

    曾漏传 db：grantable_strategies 调 StrategyCatalogService() 缺必需的 db 参数 →
    TypeError → 500（单测 mock 了该端点故未暴露，部署 smoke 才发现）。
    """
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.token_service import create_token

    c = TestClient(app)
    headers = {"Authorization": f"Bearer {create_token('u-gs', 'gs', 'USER')}"}
    r = c.get("/api/v1/billing/strategies", headers=headers)
    assert r.status_code == 200, r.text
    assert "items" in r.json()

