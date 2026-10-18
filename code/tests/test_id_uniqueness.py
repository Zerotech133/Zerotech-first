"""结构性 id 唯一化回归测试（对齐 docs/superpowers/specs/2026-08-26-structural-id-fix-redo-b）。

真跑 run_pre_market 到缺陷本体（不打桩被测的 id 生成/落库逻辑）：多策略同标的时，
candidate_id/plan_id/order_intent_id/request_id 必须两两唯一且带 sv；save_approval
不得塌行（旧缺陷：candidate_id 不带策略维度 → base plan_id 与 request_id 跨策略共享
→ 双形态并存 + 授权按 request_id upsert 塌成一行）。
"""
import types as _t
from datetime import date
from decimal import Decimal

from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase

_SV_MAP = {"TREND": "sv-cn-trend", "CHAN_V2": "sv-cn-chan-v2"}


def _setup(monkeypatch):
    """两策略（TREND/CHAN_V2）都选同一标的 000001.SZ，各挂不同真实 sv。"""
    import zhice_os_v2.application.services.workflow_service as wf

    picks = [{"ts_code": "000001.SZ", "score": 1.0, "reason": "r"}]
    monkeypatch.setattr(wf, "_sv_for_type", lambda market, st: _SV_MAP[st])
    monkeypatch.setattr(wf, "_strategy_type_status", lambda market, sv: "ACTIVE")
    monkeypatch.setattr(
        "zhice_os_v2.application.services.strategy_registry_service.StrategyRegistryService.run",
        lambda self, st, td, max_candidates=10, market=None: list(picks),
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
    # 候选池落库走真库 session；本测聚焦 plan/approval id，屏蔽以免污染真库。
    monkeypatch.setattr(
        "zhice_os_v2.application.services.workflow_service.WorkflowService._persist_candidate_pool",
        lambda self, market, td, st, picks: None,
    )
    # 风控固定 PASS，确保两策略都进授权队列（本测不评风控结果）。
    monkeypatch.setattr(
        "zhice_os_v2.application.services.risk_service.RiskService.check_plan",
        lambda self, plan, profile=None, price=None: _t.SimpleNamespace(result="PASS", reasons=[]),
    )
    # 不裁决，让两策略计划都存活以比对各自 id（冲突回退另有专测）。
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        lambda market: {"cancelled_plan_ids": []},
    )


def test_multi_strategy_same_symbol_plan_ids_unique_and_sv_tagged(monkeypatch):
    _setup(monkeypatch)
    db = InMemoryDatabase()
    result = WorkflowService(db).run_pre_market(
        "CN", date.today(), strategy_types=["TREND", "CHAN_V2"])

    plans = result["plans"]
    assert len(plans) == 2, f"两策略同标的应各建 1 计划，实得 {plans}"
    plan_ids = {p["plan_id"] for p in plans}
    assert len(plan_ids) == 2, f"plan_id 必须两两唯一（回归塌缩缺陷），实得 {plan_ids}"
    # 每个 plan_id 带各自 sv（唯一化锚）
    assert any("sv-cn-trend" in pid for pid in plan_ids), plan_ids
    assert any("sv-cn-chan-v2" in pid for pid in plan_ids), plan_ids


def test_multi_strategy_same_symbol_approvals_no_row_collapse(monkeypatch):
    _setup(monkeypatch)
    db = InMemoryDatabase()
    WorkflowService(db).run_pre_market(
        "CN", date.today(), strategy_types=["TREND", "CHAN_V2"])

    apprs = db.list_approvals()
    # 同标的两策略各存一行（旧缺陷：request_id=apr-{base} 跨策略共享 → upsert 塌成 1 行）
    assert len(apprs) == 2, f"授权不得塌行，实得 {len(apprs)} 行"
    assert len({a.request_id for a in apprs}) == 2
    assert len({a.order_intent_id for a in apprs}) == 2
    assert {a.strategy_version_id for a in apprs} == {"sv-cn-trend", "sv-cn-chan-v2"}


def _sa_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from zhice_os_v2.infrastructure.db.base import Base
    from zhice_os_v2.infrastructure.repositories.sqlalchemy_db import SqlAlchemyDatabase
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return SqlAlchemyDatabase(sessionmaker(bind=engine)())


def _approval(req_id, oi_id, sv):
    from zhice_os_v2.domain.approval.approval import ApprovalRequest
    return ApprovalRequest(
        request_id=req_id, order_intent_id=oi_id, strategy_version_id=sv,
        market="CN", ts_code="000001.SZ", direction="BUY", qty=100,
        price=10.0, reason="r")


def test_sqlalchemy_save_approval_upsert_semantics():
    """直连真 SqlAlchemyDatabase.save_approval 的 request_id upsert 路径（spec §4）：
    唯一 request_id（修复后带 sv 形态）→ v2_approval_request 存 2 行；相同 request_id
    （旧共享 base 形态）→ upsert 塌成 1 行——刻画塌行缺陷机制本体。"""
    date = "2026-08-26"
    # 修复后：order_intent_id 带 sv → request_id 唯一 → 2 行
    fixed = _sa_db()
    for sv in ("sv-cn-trend", "sv-cn-chan-v2"):
        oid = f"plan-cand-{sv}-000001.SZ-{date}"
        fixed.save_approval(_approval(f"apr-{oid}", oid, sv))
    assert len(fixed.list_approvals()) == 2, "唯一 request_id 必须各存一行"

    # 旧机制对照：base 不带 sv → request_id 跨策略共享 → upsert 覆盖成 1 行
    legacy = _sa_db()
    shared_oid = f"plan-cand-000001.SZ-{date}"
    for sv in ("sv-cn-trend", "sv-cn-chan-v2"):
        legacy.save_approval(_approval(f"apr-{shared_oid}", shared_oid, sv))
    assert len(legacy.list_approvals()) == 1, "共享 request_id 会 upsert 塌成 1 行（旧缺陷）"
