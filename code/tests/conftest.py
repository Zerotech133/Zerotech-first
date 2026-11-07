"""Pytest 全局配置：测试跑内存后端 + 关闭密码校验，保证隔离且不依赖外部 PostgreSQL。

必须在导入任何 zhice 模块之前设置环境变量——settings 与 get_db 均为 lru_cache 单例，
一经实例化即固定；conftest 由 pytest 在收集测试前最先导入，故在此顶层设置最稳妥。
- PERSISTENCE_BACKEND=memory：不打真实数据库，进程内内存库随会话新建，天然隔离、可重复。
- ADMIN_PASSWORD=""：auth 测试只验证 token/role 链路，不走密码校验（生产默认仍强制密码）。
"""
import os

os.environ["PERSISTENCE_BACKEND"] = "memory"
os.environ["ADMIN_PASSWORD"] = ""
os.environ["APP_ENV"] = "test"

import pytest


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "real_pg: 允许该测试直连真实 PostgreSQL（默认被 _guard_real_pg 守卫阻断，"
        "仅少数确需真库的 upsert/端到端归档测试豁免）",
    )
    config.addinivalue_line(
        "markers",
        "own_membership: 该测试自行管理会员权益（用真实 MembershipService.is_member，"
        "默认 autouse 会员放行不生效）。用于计费门禁本体测试。",
    )


@pytest.fixture(autouse=True)
def _default_active_membership(request, monkeypatch):
    """集成测试默认视登录用户为“有效付费会员”。

    P1 门禁 require_active_membership 会查 v2_membership（get_session）。绝大多数既有
    集成测试以已授权用户建/跑策略，语义上等价付费会员；若不放行，它们会撞 _guard_real_pg
    或缺会员记录而偏离被测意图（校验/RBAC）。故默认短路 is_member=True——不触 DB。
    会员门禁真实逻辑（free→402 / 发放 / 到期）由 test_billing_membership 单测覆盖。
    标记 @pytest.mark.own_membership 的测试豁免本放行，用真实实现。
    """
    if request.node.get_closest_marker("own_membership"):
        return
    from zhice_os_v2.application.services.membership_service import MembershipService

    monkeypatch.setattr(MembershipService, "is_member", lambda self, uid: True)


@pytest.fixture(autouse=True)
def _guard_real_pg(request, monkeypatch):
    """默认阻断测试直连生产 PG。

    生产代码里若干处硬编码 get_session()（绕过注入的 PersistencePort 直连 SQLAlchemy 真库，
    如 WorkflowService._persist_candidate_pool、decision_review_service 等）。conftest 的
    PERSISTENCE_BACKEND=memory 只隔离 PersistencePort，管不到这些直连点——曾导致
    test_pre_market_align 每跑一次就把假 sv 候选池写进生产库产孤儿。

    本守卫把 get_session 默认替换为 raise，fail-fast 暴露隐藏的真库直连。三种放行：
    - 测试自建 sqlite 并在函数体内 monkeypatch get_session（覆盖本守卫，现有隔离测试的正常路径）；
    - 屏蔽该直连点（如 monkeypatch _persist_candidate_pool 为 no-op）；
    - 确需真 PG 的测试加 @pytest.mark.real_pg 豁免。
    """
    if request.node.get_closest_marker("real_pg"):
        return
    import zhice_os_v2.infrastructure.db.session as sess_mod

    def _blocked(*a, **k):
        raise RuntimeError(
            "测试禁止直连生产 PostgreSQL：某生产代码调用了 get_session() 连真库。"
            "请 monkeypatch get_session 到隔离 sqlite、或屏蔽该直连点、"
            "或给测试加 @pytest.mark.real_pg 豁免。"
        )

    monkeypatch.setattr(sess_mod, "get_session", _blocked)


class _EmptyMappings:
    """空查询结果，供策略目录路由的 ownership/sidecar 可选查询使用。"""

    def mappings(self):
        return self

    def all(self):
        return []

    def __iter__(self):
        return iter(())


class _EmptySession:
    def execute(self, *args, **kwargs):
        return _EmptyMappings()

    def close(self):
        pass


@pytest.fixture
def strategy_router_empty_session(monkeypatch):
    """隔离策略目录路由里的硬编码 get_session() 直连。

    `api/routers/strategy.py:list_strategies` 为读取 ownership/sidecar 侧车数据直接
    import 了 get_session；这些是可选的展示字段，测试可安全用空结果代替，避免测试
    依赖 psycopg/真实 PG。该 patch 只作用于本 fixture 的测试。
    """
    from zhice_os_v2.api.routers import strategy as strategy_router

    monkeypatch.setattr(strategy_router, "get_session", lambda: _EmptySession())


@pytest.fixture
def auth_headers():
    """生成有效的 admin JWT，供需要鉴权的测试使用。"""
    from zhice_os_v2.application.services.token_service import create_token
    token = create_token(user_id="u-admin", username="admin", role="ADMIN")
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def synthetic_users():
    """共享合成用户（内存领域对象，无 DB 副作用）。

    后续任务的策略/账户/实例工厂将以此为 owner 边界。
    """
    from factories.multi_user import make_users
    return make_users()


@pytest.fixture
def users(synthetic_users):
    """别名：计划中多数测试以 `users` fixture 名引用合成用户。"""
    return synthetic_users


@pytest.fixture
def account_service(monkeypatch):
    """UserAccountService（隔离内存 sqlite；monkeypatch get_session 覆盖 _guard_real_pg）。

    与 test_user_management.py 的 user_repo fixture 同范式：建内存 sqlite、create_all
    全表、把仓储模块的 get_session 指向该库；返回持有 repository 的 service。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.infrastructure.repositories.user_account_repository as repo_mod
    from zhice_os_v2.application.services.user_account_service import UserAccountService
    from zhice_os_v2.infrastructure.db.base import Base

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(repo_mod, "get_session", lambda: Session())
    return UserAccountService()


@pytest.fixture
def strategy_catalog():
    """StrategyCatalogService（内存后端，owner-scoped 公共/私有目录）。"""
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    return StrategyCatalogService(InMemoryDatabase())


@pytest.fixture
def _instance_env(monkeypatch, users):
    """用户策略运行实例测试环境。

    账户仓储与实例仓储共用同一隔离 sqlite（get_session monkeypatch 覆盖 _guard_real_pg），
    策略/版本注册进内存 PersistencePort（InMemoryDatabase）。返回持有 db/service/
    account_service 的命名空间，供 instance_service/public_version/*account 等 fixture 共享。
    """
    from types import SimpleNamespace

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    import zhice_os_v2.application.services.account_ledger_service as ledger_mod
    import zhice_os_v2.infrastructure.repositories.user_account_repository as acc_repo_mod
    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    from zhice_os_v2.application.services.user_account_service import UserAccountService
    from zhice_os_v2.application.services.user_strategy_service import (
        UserStrategyService,
    )
    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(acc_repo_mod, "get_session", lambda: Session())
    monkeypatch.setattr(inst_repo_mod, "get_session", lambda: Session())
    # 资金台账与账户/实例同库（B5 删除释放预留/升级切换用例需要同 session 隔离）。
    monkeypatch.setattr(ledger_mod, "get_session", lambda: Session())

    db = InMemoryDatabase()
    account_service = UserAccountService()
    service = UserStrategyService(db=db, account_service=account_service)
    return SimpleNamespace(db=db, service=service, account_service=account_service)


def _register_public_version(db, name, market):
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )

    catalog = StrategyCatalogService(db)
    strat = catalog.create_public(name, market=market)
    return next(
        v
        for v in db.list_strategy_versions()
        if v.strategy_id == strat.strategy_id
    )


@pytest.fixture
def instance_service(_instance_env):
    return _instance_env.service


@pytest.fixture
def public_version(_instance_env):
    return _register_public_version(_instance_env.db, "pub-strategy-1", "US")


@pytest.fixture
def public_version_2(_instance_env):
    return _register_public_version(_instance_env.db, "pub-strategy-2", "US")


@pytest.fixture
def alice_private_version(_instance_env, users):
    from zhice_os_v2.application.services.strategy_catalog_service import (
        StrategyCatalogService,
    )

    catalog = StrategyCatalogService(_instance_env.db)
    strat = catalog.create_private(users.alice, "alpha", market="US")
    return next(
        v
        for v in _instance_env.db.list_strategy_versions()
        if v.strategy_id == strat.strategy_id
    )


@pytest.fixture
def bob_account(_instance_env, users):
    return _instance_env.account_service.create(
        users.bob, market="US", account_type="SHARED_PORTFOLIO", initial_capital=100000
    )


@pytest.fixture
def alice_shared_account(_instance_env, users):
    return _instance_env.account_service.create(
        users.alice,
        market="US",
        account_type="SHARED_PORTFOLIO",
        initial_capital=100000,
    )


@pytest.fixture(autouse=True)
def _reset_singletons():
    """每个测试前重置 api.dependencies 里的 lru_cache 单例（get_db/get_switches 等），
    让每个用例拿到全新内存库，消除跨用例状态泄漏（此前告警/持仓等会累积互相干扰）。"""
    from zhice_os_v2.api import dependencies

    def _clear():
        for name in dir(dependencies):
            fn = getattr(dependencies, name, None)
            if callable(fn) and hasattr(fn, "cache_clear"):
                fn.cache_clear()

    _clear()
    yield
    _clear()
