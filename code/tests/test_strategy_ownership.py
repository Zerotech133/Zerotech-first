"""策略归属/可见性/发布申请 service 级测试（内存后端 + sqlite round-trip，无真库）。

RED-first：StrategyCatalogService / owner_user_id / visibility / publication 均未实现。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from zhice_os_v2.application.services.strategy_catalog_service import StrategyCatalogService
from zhice_os_v2.domain.strategy.spec import StrategySpec
from zhice_os_v2.domain.strategy.strategy import Strategy, StrategyStatus, StrategyVersion
from zhice_os_v2.infrastructure.db import models  # noqa: F401
from zhice_os_v2.infrastructure.db.base import Base
from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase


def _sql_catalog() -> tuple[StrategyCatalogService, SqlAlchemyDatabase]:
    """SQL(sqlite) 后端的目录服务，用于真事务原子性测试。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = SqlAlchemyDatabase(Session())
    return StrategyCatalogService(db), db


# ── 服务级：可见性 / owner 校验 / 发布派生（内存后端，经 strategy_catalog fixture）──
def test_catalog_contains_public_and_own_private_only(strategy_catalog, users):
    pub = strategy_catalog.create_public("public-1", market="CN")
    alice_priv = strategy_catalog.create_private(users.alice, "alice-private", market="CN")
    strategy_catalog.create_private(users.bob, "bob-private", market="CN")
    ids = {item.strategy_id for item in strategy_catalog.list_visible(users.alice)}
    assert ids == {pub.strategy_id, alice_priv.strategy_id}


def test_non_owner_cannot_update_private_strategy(strategy_catalog, users):
    alice_priv = strategy_catalog.create_private(users.alice, "alice-private", market="CN")
    with pytest.raises(PermissionError):
        strategy_catalog.update_private(users.bob, alice_priv.strategy_id, description="x")


def test_private_ids_do_not_collide_across_users(strategy_catalog, users):
    """[必修] 同名私有策略跨用户不得互相覆盖：id 独立、owner 各自正确。"""
    alice_s = strategy_catalog.create_private(users.alice, "alpha", market="CN")
    bob_s = strategy_catalog.create_private(users.bob, "alpha", market="CN")
    assert alice_s.strategy_id != bob_s.strategy_id
    alice_loaded = strategy_catalog.get_strategy(alice_s.strategy_id)
    bob_loaded = strategy_catalog.get_strategy(bob_s.strategy_id)
    assert alice_loaded is not None
    assert bob_loaded is not None
    assert alice_loaded.owner_user_id == users.alice.user_id  # 未被 bob 改动
    assert bob_loaded.owner_user_id == users.bob.user_id
    assert alice_loaded.strategy_name == "alpha"  # name 仅存 strategy_name
    # 目录隔离：alice 看不到 bob 的私有
    alice_ids = {s.strategy_id for s in strategy_catalog.list_visible(users.alice)}
    assert bob_s.strategy_id not in alice_ids


def test_approve_publication_rejects_non_pending_request(strategy_catalog, users):
    """[守卫] 对非 PENDING 的 request 重复审核应被拒（不重复派生/翻状态）。"""
    private = strategy_catalog.create_private(users.alice, "delta", market="CN")
    request = strategy_catalog.request_publication(users.alice, private.strategy_id)
    strategy_catalog.approve_publication(request.request_id, actor=users.admin)
    with pytest.raises(ValueError):
        strategy_catalog.approve_publication(request.request_id, actor=users.admin)


def test_approve_publication_resets_source_status(strategy_catalog, users):
    """[终态] 审核通过后源私有策略 publication_status 归位 PRIVATE（visibility 不变）。"""
    private = strategy_catalog.create_private(users.alice, "epsilon", market="CN")
    request = strategy_catalog.request_publication(users.alice, private.strategy_id)
    src_pending = strategy_catalog.get_strategy(private.strategy_id)
    assert src_pending.publication_status == "PENDING_REVIEW"
    strategy_catalog.approve_publication(request.request_id, actor=users.admin)
    src = strategy_catalog.get_strategy(private.strategy_id)
    assert src.publication_status == "PRIVATE"
    assert src.visibility == "PRIVATE"


def test_publish_approval_derives_public_copy(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "alpha", market="US")
    request = strategy_catalog.request_publication(users.alice, private.strategy_id)
    public_version = strategy_catalog.approve_publication(request.request_id, actor=users.admin)
    public = strategy_catalog.get_strategy(public_version.strategy_id)
    assert public.owner_user_id is None
    assert public.visibility == "PUBLIC"
    assert private.visibility == "PRIVATE"
    assert private.strategy_id != public.strategy_id


def test_duplicate_pending_publication_rejected(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "beta", market="CN")
    strategy_catalog.request_publication(users.alice, private.strategy_id)
    with pytest.raises(ValueError):
        strategy_catalog.request_publication(users.alice, private.strategy_id)


def test_request_publication_non_owner_denied(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "gamma", market="CN")
    with pytest.raises(PermissionError):
        strategy_catalog.request_publication(users.bob, private.strategy_id)


# ── [必修] 审核派生单事务原子性（sqlite 真事务，注入失败验证回滚）──────────
def test_approve_publication_atomic_success():
    catalog, db = _sql_catalog()
    from factories.multi_user import make_users

    u = make_users()
    private = catalog.create_private(u.alice, "atomic-ok", market="CN")
    req = catalog.request_publication(u.alice, private.strategy_id)
    version = catalog.approve_publication(req.request_id, actor=u.admin)
    derived_id = version.strategy_id
    # 四项一致落库
    pub = db.get_strategy(derived_id)
    assert pub is not None and pub.visibility == "PUBLIC" and pub.owner_user_id is None
    assert db.get_strategy_version(version.strategy_version_id) is not None
    r = db.get_publication_request(req.request_id)
    assert r.status == "APPROVED"
    assert r.derived_strategy_id == derived_id
    assert r.reviewed_by == u.admin.user_id
    src = db.get_strategy(private.strategy_id)
    assert src.publication_status == "PRIVATE" and src.visibility == "PRIVATE"


def test_approve_publication_atomic_rollback(monkeypatch):
    catalog, db = _sql_catalog()
    from factories.multi_user import make_users

    u = make_users()
    private = catalog.create_private(u.alice, "atomic-fail", market="CN")
    req = catalog.request_publication(u.alice, private.strategy_id)

    def _boom() -> None:
        raise RuntimeError("injected pre-commit failure")

    # 注入：commit 前失败 → 整个事务回滚，无任何派生落库
    monkeypatch.setattr(db, "_pre_commit_publication", _boom)
    with pytest.raises(RuntimeError):
        catalog.approve_publication(req.request_id, actor=u.admin)

    # 回滚断言：无派生 PUBLIC 出现在目录、request 仍 PENDING、源仍 PENDING_REVIEW
    visible_ids = {s.strategy_id for s in catalog.list_visible(u.bob)}
    assert f"{private.strategy_id}-pub" not in visible_ids
    assert db.get_strategy(f"{private.strategy_id}-pub") is None
    assert db.get_publication_request(req.request_id).status == "PENDING"
    assert db.get_strategy(private.strategy_id).publication_status == "PENDING_REVIEW"


# ── [必修] reject 路径 ────────────────────────────────────────────────
def test_reject_publication_resets_source_and_allows_rerequest(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "rej", market="CN")
    req = strategy_catalog.request_publication(users.alice, private.strategy_id)
    rejected = strategy_catalog.reject_publication(req.request_id, actor=users.admin)
    assert rejected.status == "REJECTED"
    assert rejected.reviewed_by == users.admin.user_id
    src = strategy_catalog.get_strategy(private.strategy_id)
    assert src.publication_status == "PRIVATE"
    assert src.visibility == "PRIVATE"
    # 不产生公共副本
    assert strategy_catalog.get_strategy(f"{private.strategy_id}-pub") is None
    # 拒绝后可重新申请（旧申请已 REJECTED，部分唯一索引/服务校验不再挡）
    req2 = strategy_catalog.request_publication(users.alice, private.strategy_id)
    assert req2.request_id != req.request_id
    assert req2.status == "PENDING"


def test_reject_publication_non_pending_guard(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "rej2", market="CN")
    req = strategy_catalog.request_publication(users.alice, private.strategy_id)
    strategy_catalog.reject_publication(req.request_id, actor=users.admin)
    with pytest.raises(ValueError):
        strategy_catalog.reject_publication(req.request_id, actor=users.admin)


# ── [必修] 已发布则拒绝再发布：不 UPSERT 覆盖已被引用的公共版本 spec ─────────
def test_cannot_republish_already_published_source(strategy_catalog, users):
    """源发布成功后再次申请应被拒（early-fail），公共版本 spec 不被覆盖。"""
    private = strategy_catalog.create_private(users.alice, "republish", market="CN")
    req = strategy_catalog.request_publication(users.alice, private.strategy_id)
    v1 = strategy_catalog.approve_publication(req.request_id, actor=users.admin)
    derived_id = v1.strategy_id
    first_spec = strategy_catalog._db.get_strategy_version(f"{derived_id}-v1").spec.model_dump()
    # 源已发布过（存在派生公共副本）：再次 request_publication 被拒
    with pytest.raises(ValueError, match="already_published"):
        strategy_catalog.request_publication(users.alice, private.strategy_id)
    # 公共版本 spec 未被改动
    after = strategy_catalog._db.get_strategy_version(f"{derived_id}-v1").spec.model_dump()
    assert after == first_spec


def test_approve_rejects_republish_of_published_source(strategy_catalog, users):
    """硬安全门：即便异常路径产生了对已发布源的 PENDING 申请，approve 也拒绝覆盖。"""
    from zhice_os_v2.domain.strategy.strategy import StrategyPublicationRequest

    private = strategy_catalog.create_private(users.alice, "republish2", market="CN")
    req = strategy_catalog.request_publication(users.alice, private.strategy_id)
    v1 = strategy_catalog.approve_publication(req.request_id, actor=users.admin)
    first_spec = strategy_catalog._db.get_strategy_version(v1.strategy_version_id).spec.model_dump()
    # 绕过 request 守卫：直接塞一条新 PENDING 申请（模拟异常路径的重复请求）
    strategy_catalog._db.save_publication_request(
        StrategyPublicationRequest(
            request_id="rogue-req",
            source_strategy_id=private.strategy_id,
            requester_user_id=users.alice.user_id,
            status="PENDING",
        )
    )
    with pytest.raises(ValueError, match="already_published"):
        strategy_catalog.approve_publication("rogue-req", actor=users.admin)
    after = strategy_catalog._db.get_strategy_version(v1.strategy_version_id).spec.model_dump()
    assert after == first_spec


# ── [必修] approve 取最新版本 spec 须按 version_no 排序（非插入/created_at 末位）──
def test_approve_uses_highest_version_no_spec(strategy_catalog, users):
    private = strategy_catalog.create_private(users.alice, "verord", market="CN")
    db = strategy_catalog._db
    sid = private.strategy_id
    # 插入顺序：v1(create_private) → v3 → v2，使插入末位(v2)非最高版本号(v3)
    db.save_strategy_version(
        StrategyVersion(
            strategy_version_id=f"{sid}-v3",
            strategy_id=sid,
            version_no="v3",
            market="CN",
            spec=StrategySpec(spec_version=1, market="CN", universe={"tag": "v3"}),
            status=StrategyStatus.DRAFT,
            created_at=datetime(2000, 1, 1, tzinfo=UTC),
        )
    )  # created_at 更早，诱导错序
    db.save_strategy_version(
        StrategyVersion(
            strategy_version_id=f"{sid}-v2",
            strategy_id=sid,
            version_no="v2",
            market="CN",
            spec=StrategySpec(spec_version=1, market="CN", universe={"tag": "v2"}),
            status=StrategyStatus.DRAFT,
            created_at=datetime(2030, 1, 1, tzinfo=UTC),
        )
    )
    req = strategy_catalog.request_publication(users.alice, sid)
    version = strategy_catalog.approve_publication(req.request_id, actor=users.admin)
    assert version.spec.universe == {"tag": "v3"}  # 取最高版本号 v3


# ── 持久化 round-trip：三个新字段真正读写（防 no-op 回归）──────────────
def _sql_db() -> SqlAlchemyDatabase:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return SqlAlchemyDatabase(Session())


def test_strategy_ownership_fields_survive_sql_roundtrip():
    db = _sql_db()
    s = Strategy(
        strategy_id="s-priv",
        strategy_name="私有",
        market="CN",
        status=StrategyStatus.DRAFT,
        owner_user_id="u-alice",
        visibility="PRIVATE",
        publication_status="PRIVATE",
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db.save_strategy(s)
    loaded = db.get_strategy("s-priv")
    assert loaded is not None
    assert loaded.owner_user_id == "u-alice"
    assert loaded.visibility == "PRIVATE"
    assert loaded.publication_status == "PRIVATE"
    # list_strategies 也带这三字段
    listed = {x.strategy_id: x for x in db.list_strategies()}
    assert listed["s-priv"].visibility == "PRIVATE"
    assert listed["s-priv"].owner_user_id == "u-alice"


def test_publication_request_survives_sql_roundtrip():
    from zhice_os_v2.domain.strategy.strategy import StrategyPublicationRequest

    db = _sql_db()
    req = StrategyPublicationRequest(
        request_id="pubreq-1",
        source_strategy_id="s-priv",
        requester_user_id="u-alice",
        status="PENDING",
        created_at=datetime.now(UTC),
    )
    db.save_publication_request(req)
    loaded = db.get_publication_request("pubreq-1")
    assert loaded is not None
    assert loaded.source_strategy_id == "s-priv"
    assert loaded.requester_user_id == "u-alice"
    assert loaded.status == "PENDING"


# ── Phase D Task 4：/my/strategies 版本端点 + 列表版本序列化（HTTP 级）─────
#
# 前端"spec 不可变编辑=创建新版本"依赖 POST /my/strategies/{id}/versions（owner
# 在本人私有策略上新建 vN+1，旧版本行不可变）；"有新版本"提示依赖列表序列化
# 携带 version_count/versions/latest_version_no。

_SPEC = {"spec_version": 1, "market": "US", "universe": {"type": "all"}}


def _auth(user) -> dict[str, str]:
    from zhice_os_v2.application.services.token_service import create_token

    role = user.role.value if hasattr(user.role, "value") else str(user.role)
    return {"Authorization": f"Bearer {create_token(user.user_id, user.username, role)}"}


def test_create_version_endpoint_owner_creates_next_version(strategy_catalog, users):
    """POST /my/strategies/{id}/versions：owner 创建 v2；旧 v1 行不可变；非 owner 404。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_strategy_service
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.strategy_service import StrategyService

    strat = strategy_catalog.create_private(users.alice, "alpha", market="US")
    v1 = next(
        v
        for v in strategy_catalog._db.list_strategy_versions()
        if v.strategy_id == strat.strategy_id
    )

    app.dependency_overrides[get_strategy_service] = lambda: StrategyService(strategy_catalog._db)
    try:
        client = TestClient(app)
        url = f"/api/v1/my/strategies/{strat.strategy_id}/versions"

        resp = client.post(url, headers=_auth(users.alice), json={"spec": _SPEC})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["version_no"] == "v2"
        assert body["strategy_id"] == strat.strategy_id
        assert body["strategy_version_id"] != v1.strategy_version_id

        # 旧版本不可变：v1 的 version_no/spec 原样保留
        v1_after = next(
            v
            for v in strategy_catalog._db.list_strategy_versions()
            if v.strategy_version_id == v1.strategy_version_id
        )
        assert v1_after.version_no == "v1"
        assert v1_after.spec == v1.spec

        # 非 owner → 404（not-found 等价，不泄露存在性）
        resp_bob = client.post(url, headers=_auth(users.bob), json={"spec": _SPEC})
        assert resp_bob.status_code == 404
    finally:
        app.dependency_overrides.pop(get_strategy_service, None)


def test_list_strategies_editable_reflects_ownership(
    strategy_catalog, users, strategy_router_empty_session
):
    """GET /strategies 的 editable：本人私有=True、内置/公共(owner None)=False、ADMIN 恒 True。

    修复"USER 能编辑内置策略"：前端据 editable 隐藏编辑/删除入口。
    """
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.strategy.strategy import StrategyStatus

    priv = strategy_catalog.create_private(users.alice, "alpha", market="US")
    strategy_catalog._create(
        strategy_id="strat-us-builtin",
        name="Builtin US",
        market="US",
        spec=None,
        owner_user_id=None,
        visibility="PUBLIC",
        publication_status="PUBLISHED",
        status=StrategyStatus.ACTIVE,
    )
    app.dependency_overrides[get_db] = lambda: strategy_catalog._db
    try:
        client = TestClient(app)

        def rows_for(user):
            resp = client.get("/api/v1/strategies?market=US", headers=_auth(user))
            assert resp.status_code == 200, resp.text
            return {s["strategy_id"]: s for s in resp.json()}

        a = rows_for(users.alice)
        assert a[priv.strategy_id]["editable"] is True
        assert a["strat-us-builtin"]["editable"] is False
        b = rows_for(users.bob)
        assert priv.strategy_id not in b
        assert b["strat-us-builtin"]["editable"] is False
        adm = rows_for(users.admin)
        assert adm["strat-us-builtin"]["editable"] is True
        assert adm[priv.strategy_id]["editable"] is True
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_create_version_endpoint_spec_validation_and_public_guard(strategy_catalog, users):
    """非法 spec → 400；USER 在公共策略上建版本 → 403（仅 ADMIN 可版本化公共策略）。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_strategy_service
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.strategy_service import StrategyService

    strat = strategy_catalog.create_private(users.alice, "alpha", market="US")
    pub = strategy_catalog.create_public("pub-strategy", market="US")

    app.dependency_overrides[get_strategy_service] = lambda: StrategyService(strategy_catalog._db)
    try:
        client = TestClient(app)

        # 非法 spec（缺 spec_version/universe）→ 400
        resp_bad = client.post(
            f"/api/v1/my/strategies/{strat.strategy_id}/versions",
            headers=_auth(users.alice),
            json={"spec": {"market": "US"}},
        )
        assert resp_bad.status_code == 400, resp_bad.text

        # USER 对公共策略建版本 → 403
        resp_public = client.post(
            f"/api/v1/my/strategies/{pub.strategy_id}/versions",
            headers=_auth(users.alice),
            json={"spec": _SPEC},
        )
        assert resp_public.status_code == 403
    finally:
        app.dependency_overrides.pop(get_strategy_service, None)


def test_list_my_strategies_serializes_versions(strategy_catalog, users):
    """GET /my/strategies 列表带 version_count/versions/latest_version_no/description。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import (
        get_strategy_catalog_service,
        get_strategy_service,
    )
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.strategy_service import StrategyService

    strat = strategy_catalog.create_private(users.alice, "alpha", market="US")
    pub = strategy_catalog.create_public("pub-strategy", market="US")
    strategy_catalog.update_private(users.alice, strat.strategy_id, description="d1")
    version_service = StrategyService(strategy_catalog._db)
    version_service.create_version(
        users.alice, strat.strategy_id, StrategySpec.model_validate(_SPEC)
    )

    app.dependency_overrides[get_strategy_catalog_service] = lambda: strategy_catalog
    app.dependency_overrides[get_strategy_service] = lambda: version_service
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/my/strategies", headers=_auth(users.alice))
        assert resp.status_code == 200, resp.text
        rows = {r["strategy_id"]: r for r in resp.json()}

        mine = rows[strat.strategy_id]
        assert mine["visibility"] == "PRIVATE"
        assert mine["description"] == "d1"
        assert mine["version_count"] == 2
        assert mine["latest_version_no"] == "v2"
        assert [v["version_no"] for v in mine["versions"]] == ["v1", "v2"]
        assert all("strategy_version_id" in v and "status" in v for v in mine["versions"])

        # 公共行同样带版本信息（目录/我的页共用同一序列化）
        assert rows[pub.strategy_id]["version_count"] == 1
        assert rows[pub.strategy_id]["latest_version_no"] == "v1"
    finally:
        app.dependency_overrides.pop(get_strategy_catalog_service, None)
        app.dependency_overrides.pop(get_strategy_service, None)


# ---------- 全局读端点可见性（Phase D T4 复核发现的 Phase A 遗留泄露修复） ----------


def test_global_list_hides_others_private(
    strategy_catalog, users, strategy_router_empty_session
):
    """全局 GET /strategies：bob 视角不得出现 alice 私有；PUBLIC 可见；ADMIN 全见。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app

    priv = strategy_catalog.create_private(users.alice, "alice-secret", "CN")
    app.dependency_overrides[get_db] = lambda: strategy_catalog._db
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/strategies", headers=_auth(users.bob))
        assert resp.status_code == 200
        ids = {row["strategy_id"] for row in resp.json()}
        assert priv.strategy_id not in ids  # bob 看不到 alice 私有

        resp_admin = client.get("/api/v1/strategies", headers=_auth(users.admin))
        ids_admin = {row["strategy_id"] for row in resp_admin.json()}
        assert priv.strategy_id in ids_admin  # ADMIN 全见

        resp_alice = client.get("/api/v1/strategies", headers=_auth(users.alice))
        ids_alice = {row["strategy_id"] for row in resp_alice.json()}
        assert priv.strategy_id in ids_alice  # owner 见本人私有
    finally:
        app.dependency_overrides.pop(get_db, None)


def test_strategy_code_endpoint_hides_others_private(strategy_catalog, users):
    """GET /strategies/{id}/code：他人私有 → 404（不泄露存在性）；owner → 200。"""
    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app

    priv = strategy_catalog.create_private(users.alice, "alice-secret", "CN")
    app.dependency_overrides[get_db] = lambda: strategy_catalog._db
    try:
        client = TestClient(app)
        url = f"/api/v1/strategies/{priv.strategy_id}/code"

        resp_bob = client.get(url, headers=_auth(users.bob))
        assert resp_bob.status_code == 404

        resp_alice = client.get(url, headers=_auth(users.alice))
        assert resp_alice.status_code == 200

        resp_admin = client.get(url, headers=_auth(users.admin))
        assert resp_admin.status_code == 200
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------- Phase D 终审 Important #1：detail/performance 补可见性 ----------
#
# eaeafb1 修了全局列表/源码端点的私有泄露，但漏了两个读端点：
# - GET /strategies/{sv}/detail：账户/持仓/成交/绩效一站式聚合，无可见性过滤——
#   任何 USER 可按他人私有 sv id 读全部敏感数据（还兼容 strategy_id 直传）；
# - GET /strategies/performance：聚合所有版本 replay（含他人私有策略）。
# 口径同 eaeafb1：私有仅 owner/ADMIN，他人 → 404/不可见（不泄露存在性）。
# 注：enforced 阶段 detail 持仓/成交按 strategy_version_id 聚合跨用户实例混显属
# 另一门禁（见 runbook 阶段 D），本组测试只钉"私有可见性"。


def _detail_client(monkeypatch, catalog):
    """detail 端点测试环境：注入目录 db + 隔离 sqlite 会话。

    端点体及其依赖（AccountStatsService→positions 取价/已实现盈亏）直连
    get_session（模块级名字绑定），须分别指到隔离 sqlite 防真库污染。
    """
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.api.routers.positions as pos_mod
    import zhice_os_v2.api.routers.strategy_detail as detail_mod
    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app
    from zhice_os_v2.infrastructure.db.base import Base

    # StaticPool：TestClient 在工作线程跑 app，默认 :memory: 每连接一库会看不到
    # 建好的表（conftest account_service fixture 同范式）。
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    monkeypatch.setattr(detail_mod, "get_session", lambda: Session())
    monkeypatch.setattr(pos_mod, "get_session", lambda: Session())
    app.dependency_overrides[get_db] = lambda: catalog._db
    return TestClient(app), app.dependency_overrides


def test_strategy_detail_hides_others_private(strategy_catalog, users, monkeypatch):
    """GET /strategies/{sv}/detail：他人私有 → 404；owner/ADMIN → 200（不泄露存在性）。"""
    from zhice_os_v2.api.dependencies import get_db

    client, overrides = _detail_client(monkeypatch, strategy_catalog)
    try:
        priv = strategy_catalog.create_private(users.alice, "alice-secret", "CN")
        sv = next(
            v
            for v in strategy_catalog._db.list_strategy_versions()
            if v.strategy_id == priv.strategy_id
        )
        url = f"/api/v1/strategies/{sv.strategy_version_id}/detail"

        resp_bob = client.get(url, headers=_auth(users.bob))
        assert resp_bob.status_code == 404  # 他人私有：404（非 200/403 泄露存在）

        resp_alice = client.get(url, headers=_auth(users.alice))
        assert resp_alice.status_code == 200
        assert resp_alice.json()["strategy_version_id"] == sv.strategy_version_id

        resp_admin = client.get(url, headers=_auth(users.admin))
        assert resp_admin.status_code == 200
    finally:
        overrides.pop(get_db, None)


def test_strategy_detail_strategy_id_alias_respects_visibility(
    strategy_catalog, users, monkeypatch
):
    """detail 兼容 strategy_id 直传（_resolve_version_id 转版本）：可见性判定在转换后。"""
    client, overrides = _detail_client(monkeypatch, strategy_catalog)
    from zhice_os_v2.api.dependencies import get_db

    try:
        priv = strategy_catalog.create_private(users.alice, "alice-alias", "CN")
        url = f"/api/v1/strategies/{priv.strategy_id}/detail"  # 传 strategy_id 非 sv
        assert client.get(url, headers=_auth(users.bob)).status_code == 404
        assert client.get(url, headers=_auth(users.alice)).status_code == 200
    finally:
        overrides.pop(get_db, None)


def test_strategy_performance_excludes_others_private(strategy_catalog, users):
    """GET /strategies/performance：bob 不见 alice 私有策略的绩效聚合；owner/ADMIN 可见。"""
    from decimal import Decimal

    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app
    from zhice_os_v2.domain.replay.replay import ReplayCase

    priv = strategy_catalog.create_private(users.alice, "perf-private", "CN")
    pub = strategy_catalog.create_public("perf-public", "CN")
    db = strategy_catalog._db
    sv_by_sid = {}
    for v in db.list_strategy_versions():
        sv_by_sid.setdefault(v.strategy_id, v.strategy_version_id)
    for i, sid in enumerate((priv.strategy_id, pub.strategy_id)):
        db.save_replay(
            ReplayCase(
                replay_id=f"rp-{i}",
                strategy_version_id=sv_by_sid[sid],
                market="CN",
                ts_code="000001.SZ",
                pnl=Decimal("100"),
                pnl_pct=Decimal("1"),
                holding_period_seconds=60,
                exit_reason="test",
            )
        )

    app.dependency_overrides[get_db] = lambda: db
    try:
        client = TestClient(app)

        def _sv_ids(user) -> set[str]:
            resp = client.get("/api/v1/strategies/performance", headers=_auth(user))
            assert resp.status_code == 200, resp.text
            return {row["strategy_version_id"] for row in resp.json()}

        assert sv_by_sid[priv.strategy_id] not in _sv_ids(users.bob)  # 他人私有不可见
        assert sv_by_sid[priv.strategy_id] in _sv_ids(users.alice)  # owner 可见
        assert sv_by_sid[priv.strategy_id] in _sv_ids(users.admin)  # ADMIN 全见
        assert sv_by_sid[pub.strategy_id] in _sv_ids(users.bob)  # 公共不受影响
    finally:
        app.dependency_overrides.pop(get_db, None)


# ---------- Phase D 终审 Important #3：code 端点外部类型取最新版本 spec ----------
#
# get_strategy_code 对外部（V2_EXTERNAL）策略取 versions[0]——list_strategy_versions
# 的 DB 序=插入序=最旧版，多版本后"新版本预填"拿到的是 v1 旧 spec。须按
# version_no 数值（剥 v/V）取最高（与 catalog._latest_source_version 同口径）。


def test_strategy_code_external_returns_latest_version_spec(strategy_catalog, users):
    """GET /strategies/{id}/code（外部类型）：v1/v2 共存时返回 v2 的 spec 与 id。"""
    import json

    from fastapi.testclient import TestClient

    from zhice_os_v2.api.dependencies import get_db
    from zhice_os_v2.api.main import app
    from zhice_os_v2.application.services.strategy_service import StrategyService

    priv = strategy_catalog.create_private(users.alice, "code-latest", "CN")
    v2 = StrategyService(strategy_catalog._db).create_version(
        users.alice,
        priv.strategy_id,
        StrategySpec.model_validate({**_SPEC, "universe": {"type": "all", "tag": "v2"}}),
    )

    app.dependency_overrides[get_db] = lambda: strategy_catalog._db
    try:
        client = TestClient(app)
        resp = client.get(f"/api/v1/strategies/{priv.strategy_id}/code", headers=_auth(users.alice))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "spec"
        assert body["strategy_version_id"] == v2.strategy_version_id  # 最新 v2
        assert json.loads(body["content"])["universe"].get("tag") == "v2"
    finally:
        app.dependency_overrides.pop(get_db, None)
