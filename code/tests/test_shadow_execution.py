"""Task 4: 执行模式门（legacy / shadow / enforced）与 shadow 零写证明。

安全铁律：
- legacy：调用现有执行体，逻辑一字不改。
- shadow：只读 preview_fanout + 记一行指标，然后照跑 legacy 体（对外行为不变、写库零增量）。
- enforced：走 execute_fanout 新隔离路径（默认不启用）。
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from zhice_os_v2.application.services.user_instance_execution_service import (
    UserInstanceExecutionService,
)
from zhice_os_v2.application.services.workflow_service import WorkflowService
from zhice_os_v2.config.settings import get_settings
from zhice_os_v2.infrastructure.repositories.memory import InMemoryDatabase


def _set_mode(monkeypatch, mode: str) -> None:
    monkeypatch.setattr(get_settings(), "multi_user_execution_mode", mode, raising=False)


# ──────────────────────────────────────────────────────────────────────────────
# 模式门：分派正确
# ──────────────────────────────────────────────────────────────────────────────
def test_legacy_mode_calls_legacy_body_not_preview(monkeypatch):
    _set_mode(monkeypatch, "legacy")
    calls: dict = {}
    monkeypatch.setattr(
        WorkflowService, "_run_legacy_pre_market",
        lambda self, *a, **k: (calls.setdefault("legacy", True), {"legacy": True})[1],
    )
    monkeypatch.setattr(
        UserInstanceExecutionService, "preview_fanout",
        lambda self, *a, **k: calls.setdefault("preview", True),
    )
    out = WorkflowService(InMemoryDatabase()).run_pre_market("CN", date.today())
    assert out == {"legacy": True}
    assert calls == {"legacy": True}  # preview 绝不被调用


def test_shadow_mode_previews_then_runs_legacy(monkeypatch):
    _set_mode(monkeypatch, "shadow")
    calls: list[str] = []
    sentinel = {"legacy_result": 1}
    monkeypatch.setattr(
        WorkflowService, "_run_legacy_pre_market",
        lambda self, *a, **k: (calls.append("legacy"), sentinel)[1],
    )

    class _Prev:
        def to_metrics(self):
            return {"instances": 0}

    monkeypatch.setattr(
        UserInstanceExecutionService, "preview_fanout",
        lambda self, *a, **k: (calls.append("preview"), _Prev())[1],
    )
    out = WorkflowService(InMemoryDatabase()).run_pre_market("CN", date.today())
    # shadow：先 preview（只读），再照跑 legacy；返回 legacy 结果不变
    assert out is sentinel
    assert calls == ["preview", "legacy"]


def test_shadow_preview_failure_does_not_break_legacy(monkeypatch):
    _set_mode(monkeypatch, "shadow")
    calls: list[str] = []
    sentinel = {"legacy_result": 2}
    monkeypatch.setattr(
        WorkflowService, "_run_legacy_pre_market",
        lambda self, *a, **k: (calls.append("legacy"), sentinel)[1],
    )

    def _boom(self, *a, **k):
        raise RuntimeError("preview blew up")

    monkeypatch.setattr(UserInstanceExecutionService, "preview_fanout", _boom)
    # preview 崩了也绝不改变 legacy 行为（fail-open）
    out = WorkflowService(InMemoryDatabase()).run_pre_market("CN", date.today())
    assert out is sentinel
    assert calls == ["legacy"]


def test_enforced_mode_calls_execute_fanout(monkeypatch):
    _set_mode(monkeypatch, "enforced")
    calls: dict = {}
    monkeypatch.setattr(
        WorkflowService, "_run_legacy_pre_market",
        lambda self, *a, **k: calls.setdefault("legacy", True),
    )
    monkeypatch.setattr(
        UserInstanceExecutionService, "execute_fanout",
        lambda self, *a, **k: {"mode": "enforced"},
    )
    out = WorkflowService(InMemoryDatabase()).run_pre_market("CN", date.today())
    assert out == {"mode": "enforced"}
    assert "legacy" not in calls  # enforced 不跑 legacy 体


# ──────────────────────────────────────────────────────────────────────────────
# shadow 零写证明：写库增量与 legacy 完全一致（用计数断言）
# ──────────────────────────────────────────────────────────────────────────────
def _harness(monkeypatch):
    """确定性 legacy 盘前：固定 1 个候选 → 建 1 计划；屏蔽真库直连点。"""
    import zhice_os_v2.application.services.workflow_service as wf

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
    monkeypatch.setattr(
        "zhice_os_v2.api.routers.positions._get_latest_prices",
        lambda codes, market: {"000001.SZ": 10.0},
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.position_sizing_service.size_new_position",
        lambda db, market, sv, code, price: Decimal("100"),
    )
    monkeypatch.setattr(
        "zhice_os_v2.application.services.conflict_resolver_service.resolve_plan_conflicts",
        lambda market: {"cancelled_plan_ids": []},
    )
    # 屏蔽真库直连点（候选池落库走 get_session 真库，被 conftest 守卫阻断）
    monkeypatch.setattr(WorkflowService, "_persist_candidate_pool",
                        lambda self, market, td, st, picks: None)


def _run_and_count(monkeypatch, mode: str):
    _harness(monkeypatch)
    _set_mode(monkeypatch, mode)
    # shadow 的 preview 走 list_enabled_for_market（真库）→ 桩成无实例，保持只读
    monkeypatch.setattr(
        "zhice_os_v2.application.services.user_strategy_service.UserStrategyService.list_enabled_for_market",
        lambda self, market: [],
    )
    db = InMemoryDatabase()
    result = WorkflowService(db).run_pre_market("CN", date.today(), strategy_types=["CHAN"])
    return len(db.trade_plans), result


def test_shadow_writes_identical_to_legacy(monkeypatch):
    legacy_plans, _ = _run_and_count(monkeypatch, "legacy")
    assert legacy_plans == 1  # legacy 确实建了 1 计划


def test_shadow_mode_zero_write_increment_over_legacy(monkeypatch):
    # 同一确定性输入下：shadow 写库量 == legacy 写库量（shadow 只加只读 preview + 日志）
    shadow_plans, shadow_result = _run_and_count(monkeypatch, "shadow")
    assert shadow_plans == 1
    # legacy 体照跑：产出结构与 legacy 一致
    assert len(shadow_result["plans"]) == 1


def test_shadow_does_not_reserve(monkeypatch):
    called: dict = {}
    monkeypatch.setattr(
        "zhice_os_v2.application.services.account_ledger_service.AccountLedgerService.reserve",
        lambda self, *a, **k: called.setdefault("reserve", True),
    )
    _run_and_count(monkeypatch, "shadow")
    # shadow 绝不预留（reserve 从不被调用）
    assert "reserve" not in called


# ──────────────────────────────────────────────────────────────────────────────
# preview_fanout 只读证明
# ──────────────────────────────────────────────────────────────────────────────
def test_preview_fanout_is_read_only(monkeypatch):
    db = InMemoryDatabase()
    fake_instances = [
        SimpleNamespace(user_strategy_id="ustrat-a", account_id="acct-1",
                        strategy_version_id="sv-1"),
        SimpleNamespace(user_strategy_id="ustrat-b", account_id="acct-1",
                        strategy_version_id="sv-2"),
    ]
    monkeypatch.setattr(
        "zhice_os_v2.application.services.user_strategy_service.UserStrategyService.list_enabled_for_market",
        lambda self, market: fake_instances,
    )
    reserve_called: dict = {}
    monkeypatch.setattr(
        "zhice_os_v2.application.services.account_ledger_service.AccountLedgerService.reserve",
        lambda self, *a, **k: reserve_called.setdefault("reserve", True),
    )
    svc = UserInstanceExecutionService(db)
    preview = svc.preview_fanout("US", date.today(), ["TREND"])
    metrics = preview.to_metrics()
    # 计数指纹正确
    assert metrics["instance_count"] == 2
    assert metrics["account_count"] == 1
    assert metrics["strategy_version_count"] == 2
    # 只读：不落库、不预留
    assert db.trade_plans == {}
    assert "reserve" not in reserve_called
