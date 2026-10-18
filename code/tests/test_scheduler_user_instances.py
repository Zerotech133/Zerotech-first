"""Task 4: 共享候选 fan-out 到用户实例 + 零-ACTIVE 回退三态。

- materialize_plans：纯函数——每实例一份带归属的计划，plan_id 每实例唯一；空实例→[]；不落库。
- 零-ACTIVE 回退：_active_strategy_types 三态（有值 / [] / None）与调用方分支正确。
"""
from __future__ import annotations

from decimal import Decimal

from zhice_os_v2 import scheduler_jobs as sj
from zhice_os_v2.application.services.user_instance_execution_service import (
    UserInstanceExecutionService,
)
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.domain.candidate.candidate import Candidate
from zhice_os_v2.domain.strategy.user_strategy import UserStrategyInstance
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


# ──────────────────────────────────────────────────────────────────────────────
# materialize_plans（纯函数 fan-out）
# ──────────────────────────────────────────────────────────────────────────────
def _instance(uid: str, sv: str = "v1-us-sv-1", acct: str = "acct-1") -> UserStrategyInstance:
    return UserStrategyInstance(
        user_strategy_id=uid,
        user_id="u1",
        strategy_version_id=sv,
        account_id=acct,
        enabled=True,
    )


def _candidate(sv: str = "v1-us-sv-1") -> Candidate:
    return Candidate(
        candidate_id="cand-x",
        strategy_version_id=sv,
        market="US",
        ts_code="AAPL",
        score=1.0,
        reason="r",
    )


def test_shared_candidate_creates_one_plan_per_enabled_instance():
    svc = UserInstanceExecutionService(InMemoryDatabase())
    insts = [
        _instance("ustrat-a", acct="acct-a"),
        _instance("ustrat-b", acct="acct-b"),
    ]
    plans = svc.materialize_plans(_candidate(), insts)
    # 每实例一份，归属正确
    assert {p.user_strategy_id for p in plans} == {"ustrat-a", "ustrat-b"}
    assert {p.account_id for p in plans} == {"acct-a", "acct-b"}
    # plan_id 每实例唯一（同一共享候选 fan-out 不撞 id）
    assert len({p.plan_id for p in plans}) == 2
    # 归属 sv 承自候选
    assert all(p.strategy_version_id == "v1-us-sv-1" for p in plans)


def test_no_enabled_instance_creates_no_plan():
    svc = UserInstanceExecutionService(InMemoryDatabase())
    assert svc.materialize_plans(_candidate(), []) == []


def test_materialize_is_pure_and_does_not_write_db():
    db = InMemoryDatabase()
    svc = UserInstanceExecutionService(db)
    plans = svc.materialize_plans(
        _candidate(), [_instance("ustrat-a")], ref_price=Decimal("10"), qty=Decimal("100")
    )
    # 纯函数：绝不落库
    assert db.get_trade_plan(plans[0].plan_id) is None


def test_plan_id_stable_per_candidate_and_instance():
    svc = UserInstanceExecutionService(InMemoryDatabase())
    p1 = svc.materialize_plans(_candidate(), [_instance("ustrat-a")])[0]
    p2 = svc.materialize_plans(_candidate(), [_instance("ustrat-a")])[0]
    assert p1.plan_id == p2.plan_id  # 幂等：同候选+同实例 id 稳定


# ──────────────────────────────────────────────────────────────────────────────
# 零-ACTIVE 回退：_active_strategy_types 三态
# ──────────────────────────────────────────────────────────────────────────────
class _FakeQuery:
    def __init__(self, rows):
        self._rows = rows

    def filter(self, *a, **k):
        return self

    def all(self):
        return self._rows


class _FakeSession:
    def __init__(self, rows):
        self._rows = rows

    def query(self, *a, **k):
        return _FakeQuery(self._rows)

    def close(self):
        pass


def test_active_strategy_types_returns_values(monkeypatch):
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession([("TREND",), ("CHAN",), ("TREND",)]),
    )
    # 去重保序
    assert sj._active_strategy_types("CN") == ["TREND", "CHAN"]


def test_active_strategy_types_zero_active_returns_empty_list(monkeypatch):
    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session",
        lambda: _FakeSession([]),
    )
    # 零 ACTIVE → []（不是 None）——关键：区别于 DB 失败
    assert sj._active_strategy_types("CN") == []


def test_active_strategy_types_db_failure_returns_none(monkeypatch):
    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(
        "zhice_os_v2.infrastructure.db.session.get_session", _boom
    )
    # DB 失败 → None（调用方据此 fail-open 回退硬编码）
    assert sj._active_strategy_types("CN") is None


# ──────────────────────────────────────────────────────────────────────────────
# 调用方 run_pre_market_job：三态分支
# ──────────────────────────────────────────────────────────────────────────────
def _patch_rpm(monkeypatch, captured):
    def fake_rpm(self, market, trade_date, strategy_types=None, max_candidates=10, **k):
        captured["strategy_types"] = strategy_types
        captured["ran"] = True
        return {"market": market, "candidates": 0, "plans": [], "approvals": []}

    monkeypatch.setattr(WorkflowService, "run_pre_market", fake_rpm)
    monkeypatch.setattr(WorkflowService, "run_plan_expiry", lambda self, market: {})


def test_run_pre_market_job_intersects_active(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(sj, "_active_strategy_types", lambda market: ["TREND", "CHAN"])
    _patch_rpm(monkeypatch, captured)
    sj.run_pre_market_job("CN")
    # 只跑「既配置又 ACTIVE」的交集
    assert captured["strategy_types"] == ["TREND", "CHAN"]


def test_run_pre_market_job_zero_active_skips_no_hardcoded(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(sj, "_active_strategy_types", lambda market: [])
    _patch_rpm(monkeypatch, captured)
    res = sj.run_pre_market_job("CN")
    # 零 ACTIVE → 全跳，绝不回退硬编码
    assert res.get("skipped") == "no_active_strategy"
    assert "ran" not in captured


def test_run_pre_market_job_db_failure_falls_back_to_configured(monkeypatch):
    captured: dict = {}
    monkeypatch.setattr(sj, "_active_strategy_types", lambda market: None)
    _patch_rpm(monkeypatch, captured)
    sj.run_pre_market_job("CN")
    # DB 抖动 → 回退硬编码 configured（保命）
    assert captured["strategy_types"] == sj.PRE_MARKET_STRATEGIES["CN"]
