"""CN 盘前对齐云端单测：候选池 N=20 / 每策略计划 top-3 / 冲突裁决时序。"""
import pytest

from zhice_os_v2 import scheduler_jobs as sj


@pytest.fixture(autouse=True)
def _block_real_pool_persist(monkeypatch):
    """屏蔽 WorkflowService._persist_candidate_pool 直连真 PG（其内部 get_session 走真库、
    绕过注入的 InMemoryDatabase）。本文件多个测试 monkeypatch 假 sv(sv-cn-chan/v2) 后真跑
    run_pre_market，若不屏蔽会把假 sv 候选池写进生产库产孤儿（与 test_id_uniqueness 同款屏蔽）。
    autouse 覆盖全文件，防未来新增测试再漏。"""
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService._persist_candidate_pool",
        lambda self, market, td, st, picks: None,
    )


def test_pre_market_job_cn_passes_max_candidates_20(monkeypatch):
    captured = {}

    def fake_run_pre_market(self, market, trade_date, strategy_types=None, max_candidates=10, **kw):
        captured["max_candidates"] = max_candidates
        captured["market"] = market
        return {"market": market, "candidates": 0, "plans": [], "approvals": [], "pools": []}

    monkeypatch.setattr(sj, "_active_strategy_types", lambda market: ["TREND"])
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService.run_pre_market",
        fake_run_pre_market,
    )
    # run_pre_market_job 建计划前的 pre-expiry（scheduler_jobs._db → SqlAlchemyDatabase）
    # 经 sqlalchemy_db 顶层绑定的 get_session 直连真 PG（绕过 conftest _guard_real_pg 守卫）。
    # 本用例只验证 max_candidates 透传，与过期逻辑无关，按仓内 monkeypatch 范式屏蔽。
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService.run_plan_expiry",
        lambda self, market: {},
    )
    sj.run_pre_market_job("CN")
    assert captured["max_candidates"] == 20


from datetime import date
from decimal import Decimal

from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def test_pre_market_builds_top3_plans_per_strategy(monkeypatch):
    """一个策略给 5 个候选（不同 score），只应对 score 最高的前 3 个建计划。"""
    import zhice_os_v2.application.services.workflow_service as wf

    picks = [{"ts_code": f"00000{i}.SZ", "score": float(i), "reason": "r"} for i in range(5)]

    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: "sv-cn-chan")
    monkeypatch.setattr(wf, "_strategy_type_status", lambda market, sv: "ACTIVE")
    monkeypatch.setattr(
        "zhice_os_v2.application.services.strategy_registry_service.StrategyRegistryService.run",
        lambda self, st, td, max_candidates=10, market=None: picks,
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.regime_service.route_strategies",
        lambda market, types, status: {"enabled": types, "regime_info": {"regime": "SIDEWAYS"}},
    )
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda codes, market: {p["ts_code"]: 10.0 for p in picks})
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        lambda market: {"cancelled_plan_ids": []},
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.position_sizing_service.size_new_position",
        lambda db, market, sv, code, price: Decimal("100"),
    )
    db = InMemoryDatabase()
    svc = WorkflowService(db)
    result = svc.run_pre_market("CN", date.today(), strategy_types=["CHAN"])
    built = [p for p in result["plans"]]
    assert len(built) == 3
    # 应是 score 最高的 3 个：004/003/002
    built_codes = {p["ts_code"] if isinstance(p, dict) else p.ts_code for p in built}
    assert built_codes == {"000004.SZ", "000003.SZ", "000002.SZ"}


def test_conflict_resolution_runs_after_plans_built(monkeypatch):
    """冲突裁决须在所有计划 create_from_candidate 落库之后调用。"""
    import zhice_os_v2.application.services.workflow_service as wf
    events = []

    picks = [{"ts_code": "000001.SZ", "score": 1.0, "reason": "r"}]
    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: "sv-cn-chan")
    monkeypatch.setattr(wf, "_strategy_type_status", lambda market, sv: "ACTIVE")
    monkeypatch.setattr(
        "zhice_os_v2.application.services.strategy_registry_service.StrategyRegistryService.run",
        lambda self, st, td, max_candidates=10, market=None: picks,
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.regime_service.route_strategies",
        lambda market, types, status: {"enabled": types, "regime_info": {"regime": "SIDEWAYS"}},
    )
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda codes, market: {"000001.SZ": 10.0})
    monkeypatch.setattr(
        "zhice_os_v2.application.services.position_sizing_service.size_new_position",
        lambda db, market, sv, code, price: __import__("decimal").Decimal("100"),
    )
    orig_create = wf.TradePlanService.create_from_candidate
    def spy_create(self, *a, **k):
        events.append("create")
        return orig_create(self, *a, **k)
    monkeypatch.setattr(wf.TradePlanService, "create_from_candidate", spy_create)
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        lambda market: events.append("resolve") or {"cancelled_plan_ids": []},
    )
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase
    WorkflowService = wf.WorkflowService
    WorkflowService(InMemoryDatabase()).run_pre_market("CN", date.today(), strategy_types=["CHAN"])
    assert "create" in events and "resolve" in events
    assert events.index("resolve") > max(i for i, e in enumerate(events) if e == "create")


def test_non_trend_conflict_loser_skipped_by_unique_id(monkeypatch):
    """非 TREND 败者：id 唯一化后 plan_id 无策略后缀（candidate_id 已带 sv），
    cancelled_ids 即落库 plan_id 本身，跳过判定直接命中（回归"后缀双形态导致
    非 TREND 败者跳过失效"缺陷）。

    构造 CHAN_V2 一个计划，让 resolve_plan_conflicts 桩把其唯一 plan_id 放进
    cancelled_plan_ids 并同步把库里该计划置 CANCELLED。断言：不产生 approval、
    状态不被回写成 APPROVED/EXECUTED、plan_status 保持 CANCELLED。
    """
    import zhice_os_v2.application.services.workflow_service as wf
    from zhice_os_v2.domain.planning.trade_plan import PlanStatus
    from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

    td = date.today()
    # id 唯一化后：candidate_id=cand-{sv}-{ts}-{date}，plan_id=plan-{candidate_id}，无后缀
    loser_id = f"plan-cand-sv-cn-chan-v2-000001.SZ-{td.isoformat()}"

    picks = [{"ts_code": "000001.SZ", "score": 1.0, "reason": "r"}]
    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: "sv-cn-chan-v2")
    monkeypatch.setattr(wf, "_strategy_type_status", lambda market, sv: "ACTIVE")
    monkeypatch.setattr(
        "zhice_os_v2.application.services.strategy_registry_service.StrategyRegistryService.run",
        lambda self, st, td, max_candidates=10, market=None: picks,
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.regime_service.route_strategies",
        lambda market, types, status: {"enabled": types, "regime_info": {"regime": "SIDEWAYS"}},
    )
    monkeypatch.setattr("zhice_os_v2.api.routers.positions._get_latest_prices",
                        lambda codes, market: {"000001.SZ": 10.0})
    monkeypatch.setattr(
        "zhice_os_v2.application.services.position_sizing_service.size_new_position",
        lambda db, market, sv, code, price: Decimal("100"),
    )

    db = InMemoryDatabase()

    def fake_resolve(market):
        # 模拟真实 resolve：把败者置 CANCELLED 并返回其落库后缀 id
        db.update_trade_plan_status(loser_id, "CANCELLED")
        return {"cancelled_plan_ids": [loser_id]}
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        fake_resolve,
    )

    result = WorkflowService(db).run_pre_market("CN", td, strategy_types=["CHAN_V2"])

    # 败者被跳过：无授权产出
    assert result["approvals"] == []
    # 状态未被回写覆盖，仍为 CANCELLED
    assert db.get_trade_plan(loser_id) is not None
    assert db.get_trade_plan(loser_id).status == PlanStatus.CANCELLED
