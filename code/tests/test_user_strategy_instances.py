"""用户策略运行实例（v2_user_strategy_instance）测试。

owner 边界：普通用户只能对本人实例创建/启停/更新/查询；他人实例一律 not-found 等价
（LookupError），不泄露存在性。账户绑定：NEW_DEDICATED 建独立子账户；EXISTING_SHARED
只能绑本人账户且市场一致。私有策略仅 owner 可运行。配额/单实例/重复启用规则见各用例。

instance_service fixture 用隔离 sqlite + monkeypatch get_session（账户仓储与实例仓储
共用同一库），策略版本注册进内存 PersistencePort（见 conftest）。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from zhice_os_v2.domain.accounts.user_account import AccountType


# ---------- 计划 Step 1 的三个 RED 用例 ----------
def test_select_public_strategy_creates_dedicated_account(
    instance_service, users, public_version
):
    instance = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    assert instance.user_id == users.alice.user_id
    assert instance.enabled is False
    assert instance.account.account_type == AccountType.STRATEGY_DEDICATED
    assert instance.account.market == public_version.market


def test_shared_account_must_belong_to_same_user(
    instance_service, users, bob_account, public_version
):
    with pytest.raises(LookupError):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_id=bob_account.account_id,
        )


def test_private_strategy_only_owner_can_run(
    instance_service, users, alice_private_version
):
    with pytest.raises(LookupError):
        instance_service.create(
            users.bob,
            strategy_version_id=alice_private_version.strategy_version_id,
            account_mode="NEW_DEDICATED",
            initial_capital=Decimal("100000"),
        )


def test_private_strategy_owner_can_run(
    instance_service, users, alice_private_version
):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=alice_private_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    assert inst.user_id == users.alice.user_id
    assert inst.enabled is False


# ---------- 附加隔离/规则用例 ----------
def test_default_notification_preferences(instance_service, users, public_version):
    from zhice_os_v2.domain.strategy.user_strategy import (
        DEFAULT_NOTIFICATION_PREFERENCES,
    )

    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    assert inst.notification_preferences == DEFAULT_NOTIFICATION_PREFERENCES


def test_partial_unique_blocks_duplicate_active_version(
    instance_service, users, public_version, alice_shared_account
):
    i1 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=Decimal("1000"),
    )
    i2 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=Decimal("1000"),
    )
    instance_service.enable(users.alice, i1.user_strategy_id)
    with pytest.raises(ValueError):
        instance_service.enable(users.alice, i2.user_strategy_id)


def test_shared_quota_sum_cannot_exceed_account(
    instance_service, users, public_version, public_version_2, alice_shared_account
):
    i1 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=Decimal("60000"),
    )
    i2 = instance_service.create(
        users.alice,
        strategy_version_id=public_version_2.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=Decimal("60000"),
    )
    instance_service.enable(users.alice, i1.user_strategy_id)
    # 60000 + 60000 = 120000 > 100000 可分配额度
    with pytest.raises(ValueError):
        instance_service.enable(users.alice, i2.user_strategy_id)


def test_dedicated_account_single_active_instance(
    instance_service, users, public_version, public_version_2
):
    i1 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    instance_service.enable(users.alice, i1.user_strategy_id)
    # 复用同一 STRATEGY_DEDICATED 账户绑第二个实例（不同版本），启用应被拒。
    i2 = instance_service.create(
        users.alice,
        strategy_version_id=public_version_2.strategy_version_id,
        account_id=i1.account_id,
    )
    with pytest.raises(ValueError):
        instance_service.enable(users.alice, i2.user_strategy_id)


def test_cross_user_list_and_mutation_isolation(
    instance_service, users, public_version
):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    # bob 看不到 alice 的实例
    assert instance_service.list_owned(users.bob) == []
    assert [i.user_strategy_id for i in instance_service.list_owned(users.alice)] == [
        inst.user_strategy_id
    ]
    # bob 无法操作 alice 的实例（not-found 等价）
    with pytest.raises(LookupError):
        instance_service.disable(users.bob, inst.user_strategy_id)
    with pytest.raises(LookupError):
        instance_service.enable(users.bob, inst.user_strategy_id)
    with pytest.raises(LookupError):
        instance_service.update_owned(
            users.bob, inst.user_strategy_id, {"auto_execute": True}
        )


def test_enable_disable_roundtrip(instance_service, users, public_version):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    enabled = instance_service.enable(users.alice, inst.user_strategy_id)
    assert enabled.enabled is True
    disabled = instance_service.disable(users.alice, inst.user_strategy_id)
    assert disabled.enabled is False


def test_update_owned_patches_fields(instance_service, users, public_version):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    updated = instance_service.update_owned(
        users.alice,
        inst.user_strategy_id,
        {"auto_execute": True, "execution_mode": "SEMI_AUTO"},
    )
    assert updated.auto_execute is True
    assert str(updated.execution_mode) == "SEMI_AUTO"


def test_list_enabled_for_market(instance_service, users, public_version):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    # 未启用时不在市场枚举内
    assert instance_service.list_enabled_for_market(public_version.market) == []
    instance_service.enable(users.alice, inst.user_strategy_id)
    got = instance_service.list_enabled_for_market(public_version.market)
    assert [i.user_strategy_id for i in got] == [inst.user_strategy_id]


def test_existing_shared_market_must_match(instance_service, users, public_version):
    # public_version 市场为 US；给一个 CN 账户，市场不一致应拒（ValueError）。
    cn_account = instance_service._accounts.create(
        users.alice, market="CN", account_type="SHARED_PORTFOLIO", initial_capital=100000
    )
    with pytest.raises(ValueError):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_id=cn_account.account_id,
        )


# ---------- 修复：并发正确性 / 不变量 backstop ----------
def test_shared_account_enable_requires_quota(
    instance_service, users, public_version, alice_shared_account
):
    """[M2] SHARED_PORTFOLIO 账户上启用的实例必须带 capital_quota；None 保守拒绝。"""
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=None,
    )
    with pytest.raises(ValueError):
        instance_service.enable(users.alice, inst.user_strategy_id)


def test_new_dedicated_with_account_id_is_conflict(
    instance_service, users, public_version, alice_shared_account
):
    """[M3] 显式 NEW_DEDICATED 同时传 account_id 属矛盾组合，须拒绝（消除静默绑定）。"""
    with pytest.raises(ValueError):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_mode="NEW_DEDICATED",
            account_id=alice_shared_account.account_id,
        )


def test_existing_shared_without_account_id_rejected(
    instance_service, users, public_version
):
    """[M3] EXISTING_SHARED 必须带 account_id，否则拒绝。"""
    with pytest.raises(ValueError):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_mode="EXISTING_SHARED",
        )


def test_dedicated_second_version_enable_rejected_backstop(
    instance_service, users, public_version, public_version_2
):
    """[M4] 同一 STRATEGY_DEDICATED 账户第二个不同版本实例 enable 被拒（单实例不变量）。"""
    i1 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    instance_service.enable(users.alice, i1.user_strategy_id)
    i2 = instance_service.create(
        users.alice,
        strategy_version_id=public_version_2.strategy_version_id,
        account_id=i1.account_id,
    )
    with pytest.raises(ValueError):
        instance_service.enable(users.alice, i2.user_strategy_id)


def test_enable_bumps_updated_at(instance_service, users, public_version):
    """[M1] enable 写库时刷新 updated_at。"""
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    enabled = instance_service.enable(users.alice, inst.user_strategy_id)
    assert enabled.updated_at is not None
    assert enabled.updated_at >= inst.updated_at


def test_repo_integrity_error_maps_to_value_error(
    instance_service, users, public_version, alice_shared_account
):
    """[I3] 部分唯一索引冲突（重复启用同版本）→ ValueError 而非裸 IntegrityError/500。

    直接压测仓库单事务：绕过服务预检（no-op validate），触发 DB 层部分唯一索引冲突。
    """
    i1 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=Decimal("1000"),
    )
    i2 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=alice_shared_account.account_id,
        capital_quota=Decimal("1000"),
    )
    instance_service.enable(users.alice, i1.user_strategy_id)
    with pytest.raises(ValueError):
        instance_service._repo.enable_with_checks(
            i2.user_strategy_id,
            users.alice.user_id,
            lambda inst, account, others: None,
        )


# 注：真正的并发（两 worker 同时 enable 绕过预检）需 @pytest.mark.real_pg + 真 PG 的
# SELECT ... FOR UPDATE 行锁串行化验证；SQLite 下 with_for_update 为 no-op，无法在单
# 进程内进程级并发复现，故以上以「单事务 + 部分唯一索引」双保险覆盖逻辑正确性。


# ---------- Task 3: request_upgrade（Phase A 仅记录升级意图，不切换/不平仓）----------
def _add_same_strategy_version(db, base_version, admin):
    """在 base_version 所属策略上，用 admin 建一个同策略的新版本（STAGING、可用）。

    conftest 的 public_version / public_version_2 属于两个不同策略，无法直接互为升级目标
    （request_upgrade 要求同一 strategy）。故此处用 StrategyService.create_version 在
    base_version 的同一策略下派生 v2，作为合法升级目标。
    """
    from zhice_os_v2.application.services.strategy_service import StrategyService
    from zhice_os_v2.domain.strategy.spec import StrategySpec

    svc = StrategyService(db)
    spec = StrategySpec(spec_version=1, market=base_version.market, universe={"type": "all"})
    return svc.create_version(admin, base_version.strategy_id, spec)


def test_request_upgrade_records_pending_intent(instance_service, users, public_version):
    v2 = _add_same_strategy_version(instance_service._db, public_version, users.admin)
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    result = instance_service.request_upgrade(
        users.alice, inst.user_strategy_id, v2.strategy_version_id
    )
    assert result.pending_upgrade_version_id == v2.strategy_version_id
    assert result.upgrade_requested_at is not None
    # Phase A：仅记录意图，运行版本不切换。
    assert result.strategy_version_id == public_version.strategy_version_id


def test_request_upgrade_rejects_cross_strategy(
    instance_service, users, public_version, public_version_2
):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    # public_version_2 属于另一策略 → 拒绝。
    with pytest.raises(ValueError):
        instance_service.request_upgrade(
            users.alice, inst.user_strategy_id, public_version_2.strategy_version_id
        )


def test_request_upgrade_rejects_same_version(instance_service, users, public_version):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    # 目标 == 当前版本 → 拒绝。
    with pytest.raises(ValueError):
        instance_service.request_upgrade(
            users.alice, inst.user_strategy_id, public_version.strategy_version_id
        )


def test_request_upgrade_unknown_target_rejected(
    instance_service, users, public_version
):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    with pytest.raises(ValueError):
        instance_service.request_upgrade(
            users.alice, inst.user_strategy_id, "no-such-version"
        )


def test_request_upgrade_non_owner_not_found(instance_service, users, public_version):
    v2 = _add_same_strategy_version(instance_service._db, public_version, users.admin)
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    # 非 owner → not-found 等价（LookupError），不泄露存在性。
    with pytest.raises(LookupError):
        instance_service.request_upgrade(
            users.bob, inst.user_strategy_id, v2.strategy_version_id
        )


def test_upgrade_endpoint_records_pending_intent_via_api(
    instance_service, users, public_version, public_version_2
):
    """PATCH /my/strategy-instances/{id}/upgrade：记录升级意图（Phase A 服务早已存在，
    Phase D T2 补 HTTP 端点——前端升级 UI 依赖）。owner-scoped/跨策略 400/非 owner 404。
    """
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_user_strategy_service
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.token_service import create_token

    v2 = _add_same_strategy_version(instance_service._db, public_version, users.admin)
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    app.dependency_overrides[get_user_strategy_service] = lambda: instance_service
    try:
        client = TestClient(app)
        h_alice = {"Authorization": f"Bearer {create_token(users.alice.user_id, users.alice.username, 'USER')}"}
        h_bob = {"Authorization": f"Bearer {create_token(users.bob.user_id, users.bob.username, 'USER')}"}
        url = f"/api/v1/my/strategy-instances/{inst.user_strategy_id}/upgrade"

        resp = client.patch(url, headers=h_alice, json={"target_version_id": v2.strategy_version_id})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["pending_upgrade_version_id"] == v2.strategy_version_id
        assert body["upgrade_requested_at"] is not None
        assert body["strategy_version_id"] == public_version.strategy_version_id  # 未切换

        # 非 owner → 404（not-found 等价，不泄露存在性）
        resp_bob = client.patch(url, headers=h_bob, json={"target_version_id": v2.strategy_version_id})
        assert resp_bob.status_code == 404

        # 跨策略目标 → 400
        resp_cross = client.patch(url, headers=h_alice, json={"target_version_id": public_version_2.strategy_version_id})
        assert resp_cross.status_code == 400
    finally:
        app.dependency_overrides.pop(get_user_strategy_service, None)
