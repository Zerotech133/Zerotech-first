"""Phase B Task 6：owner-scoped 交易读 API 与跨用户隔离。

铁律：/my/* 只按 JWT current_user 限定资源；非 owner 一律 **404**（not-found 等价，
不泄露存在性）；legacy（归属列为 NULL）资源经 /my 一律不可见；请求体/查询塞 user_id
一律无效（owner 恒取自 JWT）。

测试拓扑（内存后端）：
- 交易实体（plan/position/replay/approval）落 get_db()（InMemoryDatabase，进程内单例，
  测试与 app 共享同一实例）。
- 运行实例落隔离 sqlite（monkeypatch user_strategy_repository.get_session 覆盖
  conftest 的 _guard_real_pg），app 的 UserStrategyService 由此读到 alice/bob 的实例，
  据此把 user_strategy_id → owner。
"""
from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest
from factories.multi_user import make_account, make_instance
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from zhice_os_v2.api.dependencies import get_db
from zhice_os_v2.api.main import app
from zhice_os_v2.application.services.token_service import create_token
from zhice_os_v2.domain.approval.approval import ApprovalRequest
from zhice_os_v2.domain.planning.trade_plan import TradePlan
from zhice_os_v2.domain.portfolio.position import PositionLot
from zhice_os_v2.domain.replay.replay import ReplayCase
from zhice_os_v2.infrastructure.db.base import Base

client = TestClient(app)


def _headers(user):
    return {"Authorization": f"Bearer {create_token(user.user_id, user.username, str(user.role))}"}


def _seed_plan(db, plan_id, user_strategy_id, account_id):
    db.save_trade_plan(
        TradePlan(
            plan_id=plan_id,
            strategy_version_id="sv-1",
            market="US",
            ts_code="AAPL",
            direction="BUY",
            qty=Decimal("10"),
            ref_price=Decimal("100"),
            user_strategy_id=user_strategy_id,
            account_id=account_id,
        )
    )


def _seed_position(db, position_id, user_strategy_id, account_id):
    db.save_position(
        PositionLot(
            position_id=position_id,
            strategy_version_id="sv-1",
            market="US",
            ts_code="AAPL",
            direction="BUY",
            open_qty=Decimal("10"),
            open_price=Decimal("100"),
            user_strategy_id=user_strategy_id,
            account_id=account_id,
        )
    )


def _seed_replay(db, replay_id, user_strategy_id, account_id):
    db.save_replay(
        ReplayCase(
            replay_id=replay_id,
            strategy_version_id="sv-1",
            market="US",
            ts_code="AAPL",
            pnl=Decimal("10"),
            pnl_pct=Decimal("0.01"),
            holding_period_seconds=3600,
            exit_reason="TAKE_PROFIT",
            user_strategy_id=user_strategy_id,
            account_id=account_id,
        )
    )


def _seed_approval(db, request_id, user_strategy_id, account_id):
    db.save_approval(
        ApprovalRequest(
            request_id=request_id,
            order_intent_id=f"oi-{request_id}",
            strategy_version_id="sv-1",
            market="US",
            ts_code="AAPL",
            direction="BUY",
            qty=10,
            price=100.0,
            reason="test",
            user_strategy_id=user_strategy_id,
            account_id=account_id,
        )
    )


@pytest.fixture
def iso_env(monkeypatch, users):
    """建两用户的运行实例（sqlite）+ 各自交易实体 + legacy 实体（get_db 内存）。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)

    import zhice_os_v2.infrastructure.repositories.user_strategy_repository as inst_repo_mod
    monkeypatch.setattr(inst_repo_mod, "get_session", lambda: Session())
    repo = inst_repo_mod.UserStrategyRepository()

    va = SimpleNamespace(strategy_version_id="sv-alice", market="US")
    vb = SimpleNamespace(strategy_version_id="sv-bob", market="US")
    acc_a = make_account(users.alice, market="US")
    acc_b = make_account(users.bob, market="US")
    inst_a = make_instance(users.alice, va, acc_a, enabled=True)
    inst_b = make_instance(users.bob, vb, acc_b, enabled=True)
    repo.create(inst_a)
    repo.create(inst_b)

    db = get_db()
    # alice 资源
    _seed_plan(db, "plan-a", inst_a.user_strategy_id, acc_a.account_id)
    _seed_position(db, "pos-a", inst_a.user_strategy_id, acc_a.account_id)
    _seed_replay(db, "rep-a", inst_a.user_strategy_id, acc_a.account_id)
    _seed_approval(db, "apr-a", inst_a.user_strategy_id, acc_a.account_id)
    # bob 资源
    _seed_plan(db, "plan-b", inst_b.user_strategy_id, acc_b.account_id)
    _seed_position(db, "pos-b", inst_b.user_strategy_id, acc_b.account_id)
    _seed_replay(db, "rep-b", inst_b.user_strategy_id, acc_b.account_id)
    _seed_approval(db, "apr-b", inst_b.user_strategy_id, acc_b.account_id)
    # legacy（NULL 归属）
    _seed_plan(db, "plan-legacy", None, None)
    _seed_position(db, "pos-legacy", None, None)
    _seed_replay(db, "rep-legacy", None, None)
    _seed_approval(db, "apr-legacy", None, None)

    return SimpleNamespace(inst_a=inst_a, inst_b=inst_b, acc_a=acc_a, acc_b=acc_b)


_TYPE_TO_ALICE_ID = {
    "plans": "plan-a",
    "positions": "pos-a",
    "replays": "rep-a",
    "approvals": "apr-a",
}


# ── A/B 隔离：bob 读 alice 资源 → 404（不泄露存在性）────────────────────
@pytest.mark.parametrize("rtype", ["plans", "positions", "replays", "approvals"])
def test_bob_cannot_read_alice_resource(iso_env, users, rtype):
    rid = _TYPE_TO_ALICE_ID[rtype]
    r = client.get(f"/api/v1/my/{rtype}/{rid}", headers=_headers(users.bob))
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == {"code": "common.not_found"}


@pytest.mark.parametrize("rtype", ["plans", "positions", "replays", "approvals"])
def test_alice_can_read_own_resource(iso_env, users, rtype):
    rid = _TYPE_TO_ALICE_ID[rtype]
    r = client.get(f"/api/v1/my/{rtype}/{rid}", headers=_headers(users.alice))
    assert r.status_code == 200, r.text
    assert r.json()["user_strategy_id"] == iso_env.inst_a.user_strategy_id


@pytest.mark.parametrize("rtype", ["plans", "positions", "replays", "approvals"])
def test_legacy_null_resource_invisible_via_my(iso_env, users, rtype):
    legacy_id = {"plans": "plan-legacy", "positions": "pos-legacy",
                 "replays": "rep-legacy", "approvals": "apr-legacy"}[rtype]
    r = client.get(f"/api/v1/my/{rtype}/{legacy_id}", headers=_headers(users.alice))
    assert r.status_code == 404, r.text


# ── list 泄露：alice 的 /my/{type} 只含本人、不含 bob、不含 legacy ────────
@pytest.mark.parametrize(
    ("rtype", "id_key"),
    [("plans", "plan_id"), ("positions", "position_id"),
     ("replays", "replay_id"), ("approvals", "request_id")],
)
def test_list_only_returns_own(iso_env, users, rtype, id_key):
    r = client.get(f"/api/v1/my/{rtype}", headers=_headers(users.alice))
    assert r.status_code == 200, r.text
    ids = {row[id_key] for row in r.json()}
    own = _TYPE_TO_ALICE_ID[rtype]
    assert own in ids
    # 不含 bob、不含 legacy
    assert not (ids & {"plan-b", "pos-b", "rep-b", "apr-b",
                       "plan-legacy", "pos-legacy", "rep-legacy", "apr-legacy"})


# ── 请求塞 user_id override 无效（owner 恒 JWT）──────────────────────────
def test_query_user_id_override_is_ignored_on_get(iso_env, users):
    # bob 带 ?user_id=u-alice 仍不能读 alice 的 plan
    r = client.get(
        "/api/v1/my/plans/plan-a?user_id=u-alice", headers=_headers(users.bob)
    )
    assert r.status_code == 404, r.text


def test_query_user_id_override_is_ignored_on_list(iso_env, users):
    # bob 带 ?user_id=u-alice 的 list 仍只返回 bob 自己的
    r = client.get("/api/v1/my/plans?user_id=u-alice", headers=_headers(users.bob))
    assert r.status_code == 200, r.text
    ids = {row["plan_id"] for row in r.json()}
    assert ids == {"plan-b"}


def test_query_user_id_override_is_ignored_on_approval_get(iso_env, users):
    # approval 读同样只信 JWT owner，不能通过查询参数越权读取 alice 的请求
    r = client.get(
        "/api/v1/my/approvals/apr-a?user_id=u-alice", headers=_headers(users.bob)
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"] == {"code": "common.not_found"}


# ── 未认证拒绝 ──────────────────────────────────────────────────────────
def test_my_trading_requires_auth(iso_env):
    r = client.get("/api/v1/my/plans/plan-a")
    assert r.status_code == 401
