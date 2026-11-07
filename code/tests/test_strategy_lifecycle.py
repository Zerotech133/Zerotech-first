"""深测 B5：策略域生命周期收口。

覆盖四组深测实证缺陷：
1. 删被实例绑定的策略 → 500（repo delete_strategy 只删两表，实例
   strategy_version_id FK 无级联 IntegrityError）→ 先查绑定 → 409
   {code: strategy.bound_by_instances, instances: [...]}；实例无删除端点的运营
   死锁 → 新增 DELETE /my/strategy-instances/{id}。
2. 实例创建校验失败留孤儿账户（先建账户后校验）→ 校验全部前置，失败零落库。
3. 升级"自动切换"无执行器（pending_upgrade_version_id 只有写入方）→
   apply_ready_upgrades：无 OPEN 持仓即原子切换 + 清 pending + 审计行。
4. 同名策略可无限建 / 空 spec {} 静默换默认 / 广场混入 DRAFT 与空名行。

fixture 形态同 test_user_strategy_instances：策略/版本注册进内存
PersistencePort，账户/实例/台账/审计共用同一隔离 sqlite（conftest
_instance_env monkeypatch get_session）。
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from zhice_os_v2.domain.accounts.user_account import AccountType


# ════════════════════════════════════════════════════════════════════════
# 1. 实例删除（运营死锁解锁）
# ════════════════════════════════════════════════════════════════════════
def test_delete_owned_removes_instance_and_dedicated_account(
    instance_service, users, public_version
):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    result = instance_service.delete_owned(users.alice, inst.user_strategy_id)
    assert result["deleted"] is True
    assert result["account_id"] == inst.account_id  # 专用账户连带删
    # 实例行已删（owner 再载入 → not-found）
    with pytest.raises(LookupError):
        instance_service._load_owned(users.alice, inst.user_strategy_id)
    # 专用账户已删
    assert (
        instance_service._account_repo.get_owned(inst.account_id, users.alice.user_id)
        is None
    )


def test_delete_owned_rejects_enabled_instance(instance_service, users, public_version):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    instance_service.enable(users.alice, inst.user_strategy_id)
    with pytest.raises(ValueError, match="instance.not_disabled"):
        instance_service.delete_owned(users.alice, inst.user_strategy_id)
    # 实例仍在
    got = instance_service._load_owned(users.alice, inst.user_strategy_id)
    assert got.enabled is True


def test_delete_owned_rejects_open_positions(instance_service, users, public_version):
    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus

    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    # 该实例名下一笔 OPEN 持仓 → 拒删
    instance_service._db.save_position(
        PositionLot(
            position_id="p-open-1",
            strategy_version_id=public_version.strategy_version_id,
            market="US",
            ts_code="AAPL",
            direction="LONG",
            open_qty=Decimal("10"),
            open_price=Decimal("100"),
            status=PositionStatus.OPEN,
            opened_at=utc_now(),
            user_strategy_id=inst.user_strategy_id,
            account_id=inst.account_id,
        )
    )
    with pytest.raises(ValueError, match="instance.has_open_positions"):
        instance_service.delete_owned(users.alice, inst.user_strategy_id)


def test_delete_owned_non_owner_not_found(instance_service, users, public_version):
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    with pytest.raises(LookupError):
        instance_service.delete_owned(users.bob, inst.user_strategy_id)


def test_delete_owned_shared_account_kept(instance_service, users, public_version):
    """共享账户仅解绑实例不删账户（用户资产由 /my/accounts 面生命周期管理）。"""
    shared = instance_service._accounts.create(
        users.alice, market="US", account_type=AccountType.SHARED_PORTFOLIO,
        initial_capital=100000,
    )
    inst2 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=shared.account_id,
        capital_quota=Decimal("1000"),
    )
    result = instance_service.delete_owned(users.alice, inst2.user_strategy_id)
    assert result["account_id"] is None  # 共享账户保留
    assert (
        instance_service._account_repo.get_owned(shared.account_id, users.alice.user_id)
        is not None
    )


def test_delete_owned_releases_reserved_cash(instance_service, users, public_version):
    """删除前释放该实例全部 RESERVED 预留（资金台账不变量：reserved_cash 同步回落）。"""
    from zhice_os_v2.application.services.account_ledger_service import (
        AccountLedgerService,
    )

    shared = instance_service._accounts.create(
        users.alice, market="US", account_type=AccountType.SHARED_PORTFOLIO,
        initial_capital=100000,
    )
    i1 = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_id=shared.account_id,
        capital_quota=Decimal("50000"),
    )
    ledger = AccountLedgerService()
    ledger.reserve(
        shared.account_id, i1.user_strategy_id, Decimal("3000"), "idem-b5-del-1"
    )
    before = ledger.get(shared.account_id)
    assert before.reserved_cash == Decimal("3000")

    instance_service.delete_owned(users.alice, i1.user_strategy_id)
    after = ledger.get(shared.account_id)
    # 预留已释放（账户保留、余额不动、reserved 回落）
    assert after.reserved_cash == Decimal("0")
    assert after.cash_balance == before.cash_balance


def test_delete_instance_endpoint(instance_service, users, public_version):
    """DELETE /my/strategy-instances/{id}：200 / enabled 409 {code} / 非 owner 404。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_user_strategy_service
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.token_service import create_token

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
        url = f"/api/v1/my/strategy-instances/{inst.user_strategy_id}"

        # disabled 且无持仓 → 200
        resp = client.delete(url, headers=h_alice)
        assert resp.status_code == 200, resp.text
        assert resp.json()["deleted"] == inst.user_strategy_id

        # 已删（或从未存在）→ 404
        resp2 = client.delete(url, headers=h_alice)
        assert resp2.status_code == 404

        # 非 owner → 404
        inst2 = instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_mode="NEW_DEDICATED",
            initial_capital=Decimal("100000"),
        )
        resp_bob = client.delete(
            f"/api/v1/my/strategy-instances/{inst2.user_strategy_id}", headers=h_bob
        )
        assert resp_bob.status_code == 404

        # enabled → 409 {code: instance.not_disabled}
        instance_service.enable(users.alice, inst2.user_strategy_id)
        resp_en = client.delete(
            f"/api/v1/my/strategy-instances/{inst2.user_strategy_id}", headers=h_alice
        )
        assert resp_en.status_code == 409
        assert resp_en.json()["detail"] == {"code": "instance.not_disabled"}
    finally:
        app.dependency_overrides.pop(get_user_strategy_service, None)


# ════════════════════════════════════════════════════════════════════════
# 2. 孤儿账户：创建校验失败零落库
# ════════════════════════════════════════════════════════════════════════
def _account_count(instance_service, user) -> int:
    return len(instance_service._account_repo.list_by_user(user.user_id))


def test_create_invalid_ratio_leaves_no_account(instance_service, users, public_version):
    before = _account_count(instance_service, users.alice)
    with pytest.raises(ValueError, match="invalid_max_position_ratio"):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_mode="NEW_DEDICATED",
            initial_capital=Decimal("100000"),
            max_position_ratio=Decimal("1.5"),
        )
    assert _account_count(instance_service, users.alice) == before


def test_create_invalid_execution_mode_leaves_no_account(
    instance_service, users, public_version
):
    before = _account_count(instance_service, users.alice)
    with pytest.raises(ValueError):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_mode="NEW_DEDICATED",
            initial_capital=Decimal("100000"),
            execution_mode="BOGUS_MODE",
        )
    assert _account_count(instance_service, users.alice) == before


def test_create_invalid_capital_quota_leaves_no_account(
    instance_service, users, public_version
):
    before = _account_count(instance_service, users.alice)
    with pytest.raises(ValueError):
        instance_service.create(
            users.alice,
            strategy_version_id=public_version.strategy_version_id,
            account_mode="NEW_DEDICATED",
            initial_capital=Decimal("100000"),
            capital_quota="not-a-number",
        )
    assert _account_count(instance_service, users.alice) == before


# ════════════════════════════════════════════════════════════════════════
# 3. 升级自动切换执行器 apply_ready_upgrades
# ════════════════════════════════════════════════════════════════════════
def _add_same_strategy_version(db, base_version, admin):
    from zhice_os_v2.application.services.strategy_service import StrategyService
    from zhice_os_v2.domain.strategy.spec import StrategySpec

    svc = StrategyService(db)
    spec = StrategySpec(spec_version=1, market=base_version.market, universe={"type": "all"})
    return svc.create_version(admin, base_version.strategy_id, spec)


def _mk_pending_instance(instance_service, users, public_version):
    v2 = _add_same_strategy_version(instance_service._db, public_version, users.admin)
    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    instance_service.request_upgrade(users.alice, inst.user_strategy_id, v2.strategy_version_id)
    return inst, v2


def test_apply_ready_upgrades_switches_when_flat(
    instance_service, users, public_version
):
    inst, v2 = _mk_pending_instance(instance_service, users, public_version)
    applied = instance_service.apply_ready_upgrades("US")
    assert [a["user_strategy_id"] for a in applied] == [inst.user_strategy_id]
    assert applied[0]["from_version_id"] == public_version.strategy_version_id
    assert applied[0]["to_version_id"] == v2.strategy_version_id
    # 版本落位 + pending 清空
    got = instance_service._load_owned(users.alice, inst.user_strategy_id)
    assert got.strategy_version_id == v2.strategy_version_id
    assert got.pending_upgrade_version_id is None
    assert got.upgrade_requested_at is None


def test_apply_ready_upgrades_holds_with_open_positions(
    instance_service, users, public_version
):
    from zhice_os_v2.domain.ontology.objects import utc_now
    from zhice_os_v2.domain.portfolio.position import PositionLot, PositionStatus

    inst, v2 = _mk_pending_instance(instance_service, users, public_version)
    instance_service._db.save_position(
        PositionLot(
            position_id="p-hold-1",
            strategy_version_id=public_version.strategy_version_id,
            market="US",
            ts_code="AAPL",
            direction="LONG",
            open_qty=Decimal("10"),
            open_price=Decimal("100"),
            status=PositionStatus.OPEN,
            opened_at=utc_now(),
            user_strategy_id=inst.user_strategy_id,
            account_id=inst.account_id,
        )
    )
    applied = instance_service.apply_ready_upgrades("US")
    assert applied == []
    got = instance_service._load_owned(users.alice, inst.user_strategy_id)
    assert got.strategy_version_id == public_version.strategy_version_id  # 未切换
    assert got.pending_upgrade_version_id == v2.strategy_version_id  # 意图保留


def test_apply_ready_upgrades_clears_missing_target(instance_service, users, public_version):
    inst, v2 = _mk_pending_instance(instance_service, users, public_version)
    # 目标版本被删（不留死 pending）
    instance_service._db.delete_strategy_version(v2.strategy_version_id)
    applied = instance_service.apply_ready_upgrades("US")
    assert applied == []
    got = instance_service._load_owned(users.alice, inst.user_strategy_id)
    assert got.pending_upgrade_version_id is None
    assert got.strategy_version_id == public_version.strategy_version_id


def test_apply_ready_upgrades_idempotent(instance_service, users, public_version):
    _inst, _v2 = _mk_pending_instance(instance_service, users, public_version)
    first = instance_service.apply_ready_upgrades("US")
    assert len(first) == 1
    second = instance_service.apply_ready_upgrades("US")
    assert second == []


def test_apply_ready_upgrades_market_filter(instance_service, users, public_version):
    inst, _v2 = _mk_pending_instance(instance_service, users, public_version)
    # 实例市场 US；以 CN 轮次执行 → 不动
    applied = instance_service.apply_ready_upgrades("CN")
    assert applied == []
    got = instance_service._load_owned(users.alice, inst.user_strategy_id)
    assert got.pending_upgrade_version_id is not None


def test_apply_ready_upgrades_writes_audit(instance_service, users, public_version):
    """切换落一条 access_audit（沿用 v2_access_audit 形态，actor=实例 owner）。"""
    inst, _v2 = _mk_pending_instance(instance_service, users, public_version)
    instance_service.apply_ready_upgrades("US")
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as repo_mod
    from zhice_os_v2.infrastructure.db import models as orm

    s = repo_mod.get_session()
    try:
        row = (
            s.query(orm.AccessAuditModel)
            .filter_by(resource_id=inst.user_strategy_id)
            .order_by(orm.AccessAuditModel.id.desc())
            .first()
        )
        assert row is not None
        assert row.action == "strategy_upgrade_applied"
        assert row.actor_user_id == users.alice.user_id
    finally:
        s.close()


# ════════════════════════════════════════════════════════════════════════
# 4. 策略删除死锁：实例绑定 → 409（全部实例删后可正常删）
# ════════════════════════════════════════════════════════════════════════
def test_strategy_delete_blocked_by_bound_instances(
    instance_service, users, public_version
):
    from zhice_os_v2.application.services.strategy_service import StrategyService

    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    svc = StrategyService(instance_service._db, instance_repo=instance_service._repo)
    with pytest.raises(PermissionError) as exc_info:
        svc.delete(public_version.strategy_id)
    # {code, instances} 形态（args[0]=code，args[1]=实例 id 列表）
    assert exc_info.value.args[0] == "strategy.bound_by_instances"
    assert inst.user_strategy_id in exc_info.value.args[1]

    # 删掉绑定实例后可正常删
    instance_service.delete_owned(users.alice, inst.user_strategy_id)
    svc.delete(public_version.strategy_id)
    assert instance_service._db.get_strategy(public_version.strategy_id) is None


def test_strategy_delete_endpoint_409_with_instance_ids(
    instance_service, users, public_version
):
    """DELETE /strategies/{id} 绑定实例 → 409 {code, instances}（不再 IntegrityError 500）。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_strategy_service
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.strategy_service import StrategyService
    from zhice_os_v2.application.services.token_service import create_token

    inst = instance_service.create(
        users.alice,
        strategy_version_id=public_version.strategy_version_id,
        account_mode="NEW_DEDICATED",
        initial_capital=Decimal("100000"),
    )
    svc = StrategyService(instance_service._db, instance_repo=instance_service._repo)
    app.dependency_overrides[get_strategy_service] = lambda: svc
    try:
        client = TestClient(app)
        h = {"Authorization": f"Bearer {create_token(users.admin.user_id, users.admin.username, 'ADMIN')}"}
        resp = client.delete(f"/api/v1/strategies/{public_version.strategy_id}", headers=h)
        assert resp.status_code == 409, resp.text
        body = resp.json()["detail"]
        assert body["code"] == "strategy.bound_by_instances"
        assert inst.user_strategy_id in body["instances"]
    finally:
        app.dependency_overrides.pop(get_strategy_service, None)


def test_my_strategy_delete_endpoint(instance_service, users, public_version):
    """DELETE /my/strategies/{id}：owner 删本人私有（含版本）；非 owner → 404（不泄露存在性）。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import (
        get_strategy_catalog_service,
        get_strategy_service,
    )
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )
    from zhice_os_v2.application.services.strategy_service import StrategyService
    from zhice_os_v2.application.services.token_service import create_token

    catalog = StrategyCatalogService(instance_service._db)
    strat = catalog.create_private(users.alice, "b5-private", market="US")
    app.dependency_overrides[get_strategy_catalog_service] = lambda: catalog
    app.dependency_overrides[get_strategy_service] = lambda: StrategyService(
        instance_service._db, instance_repo=instance_service._repo
    )
    try:
        client = TestClient(app)
        h_alice = {"Authorization": f"Bearer {create_token(users.alice.user_id, users.alice.username, 'USER')}"}
        h_bob = {"Authorization": f"Bearer {create_token(users.bob.user_id, users.bob.username, 'USER')}"}

        # 非 owner → 404
        resp_bob = client.delete(f"/api/v1/my/strategies/{strat.strategy_id}", headers=h_bob)
        assert resp_bob.status_code == 404

        # owner 无绑定 → 200，策略+版本删净
        resp = client.delete(f"/api/v1/my/strategies/{strat.strategy_id}", headers=h_alice)
        assert resp.status_code == 200, resp.text
        assert instance_service._db.get_strategy(strat.strategy_id) is None
        versions = [
            v for v in instance_service._db.list_strategy_versions()
            if v.strategy_id == strat.strategy_id
        ]
        assert versions == []
    finally:
        app.dependency_overrides.pop(get_strategy_catalog_service, None)
        app.dependency_overrides.pop(get_strategy_service, None)


# ════════════════════════════════════════════════════════════════════════
# 5. 同名 409 / 空 spec 422 / 广场过滤（API 层，内存目录库）
# ════════════════════════════════════════════════════════════════════════
def _api_client():
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.token_service import create_token

    client = TestClient(app)
    h = {"Authorization": f"Bearer {create_token('u-b5', 'b5tester', 'USER')}"}
    return client, h


def test_create_same_owner_same_name_409():
    client, h = _api_client()
    body = {"strategy_name": "dup-name", "market": "CN"}
    r1 = client.post("/api/v1/my/strategies", headers=h, json=body)
    assert r1.status_code == 200, r1.text
    r2 = client.post("/api/v1/my/strategies", headers=h, json=body)
    assert r2.status_code == 409
    assert r2.json()["detail"] == {"code": "strategy.name_taken"}


def test_create_empty_spec_422_not_silently_defaulted():
    """空 spec {} 不得静默换默认 spec——缺必填字段按 spec_invalid 422。"""
    client, h = _api_client()
    r = client.post(
        "/api/v1/my/strategies",
        headers=h,
        json={"strategy_name": "empty-spec", "market": "CN", "spec": {}},
    )
    assert r.status_code == 422
    assert r.json()["detail"] == {"code": "strategy.spec_invalid"}


def test_catalog_filters_draft_public_and_blank_names(
    strategy_router_empty_session,
):
    """公共目录 GET /strategies：滤公共 DRAFT 与空名/纯空白名行（只滤展示不删数据）。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.token_service import create_token
    from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    db = InMemoryDatabase()
    db.save_strategy(Strategy(
        strategy_id="s-ok", strategy_name="正常公共", market="CN",
        strategy_type="V2_EXTERNAL", status=StrategyStatus.ACTIVE,
    ))
    db.save_strategy(Strategy(
        strategy_id="s-draft-pub", strategy_name="公共草稿", market="CN",
        strategy_type="V2_EXTERNAL", status=StrategyStatus.DRAFT,
    ))
    db.save_strategy(Strategy(
        strategy_id="s-blank", strategy_name="   ", market="CN",
        strategy_type="V2_EXTERNAL", status=StrategyStatus.ACTIVE,
    ))
    db.save_strategy(Strategy(
        strategy_id="s-noname", strategy_name="", market="CN",
        strategy_type="V2_EXTERNAL", status=StrategyStatus.ACTIVE,
    ))
    db.save_strategy(Strategy(
        strategy_id="s-own-draft", strategy_name="本人私有草稿", market="CN",
        strategy_type="V2_EXTERNAL", status=StrategyStatus.DRAFT,
        owner_user_id="u-b5", visibility="PRIVATE", publication_status="PRIVATE",
    ))

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)
        h = {"Authorization": f"Bearer {create_token('u-b5', 'b5tester', 'USER')}"}
        resp = client.get("/api/v1/strategies", headers=h)
        assert resp.status_code == 200
        ids = {row["strategy_id"] for row in resp.json()}
        assert "s-ok" in ids
        assert "s-own-draft" in ids  # 本人私有 DRAFT 保留（mine 页管理面）
        assert "s-draft-pub" not in ids  # 公共 DRAFT 滤除
        assert "s-blank" not in ids  # 纯空白名滤除
        assert "s-noname" not in ids  # 空名滤除
        # 不删数据：目录库仍在
        assert db.get_strategy("s-draft-pub") is not None
    finally:
        app.dependency_overrides.pop(get_db, None)


# ════════════════════════════════════════════════════════════════════════
# 6. run_intraday_job 尾部接线：升级自动切换（门禁放行路径内、best-effort）
# ════════════════════════════════════════════════════════════════════════
def _patch_intraday_env(monkeypatch, captured: dict) -> None:
    """打桩日历门禁放行 + WorkflowService.run_intraday（不触真库）。"""
    import zhice_os_v2.scheduler_jobs as sj
    from zhice_os_v2.application.services.workflow_service import WorkflowService

    monkeypatch.setattr(sj, "_market_closed_today", lambda m, today=None: False)
    monkeypatch.setattr(
        WorkflowService,
        "run_intraday",
        lambda self, market: captured.setdefault("intraday", {"market": market, "checked": 0}),
    )


def test_intraday_job_applies_ready_upgrades(monkeypatch):
    """门禁放行路径内执行升级切换：结果挂 upgrades_applied。"""
    import zhice_os_v2.scheduler_jobs as sj
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )

    captured: dict = {}
    _patch_intraday_env(monkeypatch, captured)
    summary = [{"user_strategy_id": "ustrat-x", "from_version_id": "a", "to_version_id": "b"}]
    monkeypatch.setattr(
        UserStrategyService, "apply_ready_upgrades", lambda self, market=None: summary
    )
    result = sj.run_intraday_job("CN")
    assert result["upgrades_applied"] == summary


def test_intraday_job_skips_upgrades_on_closed_market(monkeypatch):
    """门禁拦截时不执行升级切换（不绕过 B2 日历门禁）。"""
    import zhice_os_v2.scheduler_jobs as sj
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )

    captured: dict = {}
    _patch_intraday_env(monkeypatch, captured)
    called: list[str] = []

    def _boom(self, market=None):
        called.append("called")
        raise AssertionError("休市日不应执行升级切换")

    monkeypatch.setattr(sj, "_market_closed_today", lambda m, today=None: True)
    monkeypatch.setattr(UserStrategyService, "apply_ready_upgrades", _boom)
    result = sj.run_intraday_job("CN")
    assert result.get("skipped") == "market_closed"
    assert "upgrades_applied" not in result
    assert called == []


def test_intraday_job_upgrade_failure_does_not_break_main(monkeypatch, caplog):
    """升级切换异常只记日志：intraday 主链结果照常返回。"""
    import logging

    import zhice_os_v2.scheduler_jobs as sj
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )

    captured: dict = {}
    _patch_intraday_env(monkeypatch, captured)
    monkeypatch.setattr(
        UserStrategyService,
        "apply_ready_upgrades",
        lambda self, market=None: (_ for _ in ()).throw(RuntimeError("upgrade boom")),
    )
    with caplog.at_level(logging.ERROR, logger="zhice_os_v2.scheduler_jobs"):
        result = sj.run_intraday_job("CN")
    assert result.get("skipped") is None  # 主链未被拖垮
    assert any("apply_ready_upgrades failed" in r.getMessage() for r in caplog.records)
