# -*- coding: utf-8 -*-
"""K-ORG-20261101-HQORCH（W-C2）：指挥部编排器 MVP 测试（全 fake，无真实模型/执行）。

覆盖：单任务/多依赖拓扑、循环依赖拒绝、plan 非法（重复 id/未知依赖/非法风险）、
状态机合法链与非法跳转、L2 强制升级+人工定版、重跑幂等、fake 角色汇总
（共识/条件/反对/异常/缺失）、并发派单顺序、快照恢复、不执行真实动作。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

AP = Path(__file__).resolve().parent.parent
if str(AP) not in sys.path:
    sys.path.insert(0, str(AP))

from command_hq.orchestrator import (  # noqa: E402
    Orchestrator,
    OrchestratorError,
    PlanError,
    StateError,
    normalize_plan,
    validate_dag,
)
from command_hq.role_contract import ROLES  # noqa: E402


def _op(role, topic, position="support", **kw):
    data = {"role": role, "topic_id": topic, "recommendation": "同意",
            "position": position}
    data.update(kw)
    return data


def _runner(topic_out=None, position="support", **kw):
    def run(role, topic, goal, dag, constraints):
        return _op(role, topic_out or topic, position=position, **kw)
    return run


def _planner(tasks):
    def plan(goal, constraints):
        return {"tasks": tasks}

    return plan


def _task(tid, deps=None, risk="L1", **kw):
    data = {"id": tid, "title": "任务-" + tid, "owner": "TIANFENG",
            "deps": deps or [], "acceptance": ["验收-" + tid],
            "risk_level": risk}
    data.update(kw)
    return data


def _orchestrator(tasks, positions=None, planner=None, runners=None):
    positions = positions or {}
    default_runners = {r: _runner(position=positions.get(r, "support")) for r in ROLES}
    if runners is not None:
        default_runners = runners
    return Orchestrator(planner=planner or _planner(tasks),
                        role_runners=default_runners)


# ── DAG 基础 ─────────────────────────────────────────────────────────────

def test_single_task_consensus():
    orch = _orchestrator([_task("T1", risk="L1")])
    report = orch.run("把 M4 会诊跑成一条链", {"budget": "1h"})
    assert report["schema"] == "hq_orchestrator_report/v1"
    assert report["topology"] == ["T1"] and len(report["tasks"]) == 1
    assert report["tasks"][0]["risk_level"] == "L1"
    assert len(report["opinions"]) == 5 and report["role_errors"] == {}
    assert report["summary"]["consensus"] is True
    assert report["summary"]["decision_state"] == "consensus"
    assert orch.state == "consensus"
    # 无真实执行
    assert report["executed"] is False
    assert report["execution"]["attempted"] is False


def test_multi_dependency_topology_order():
    tasks = [_task("C"), _task("A", deps=["C"]), _task("B", deps=["A"])]
    nodes = validate_dag(normalize_plan({"tasks": tasks}))
    assert [n.task_id for n in nodes] == ["C", "A", "B"]
    report = _orchestrator(tasks).run("依赖链")
    assert report["topology"] == ["C", "A", "B"]
    deps = {t["task_id"]: t["deps"] for t in report["tasks"]}
    assert deps == {"C": [], "A": ["C"], "B": ["A"]}


def test_cycle_dependency_rejected():
    tasks = [_task("A", deps=["B"]), _task("B", deps=["A"])]
    with pytest.raises(PlanError, match="循环"):
        validate_dag(normalize_plan({"tasks": tasks}))


def test_plan_validation_errors():
    with pytest.raises(PlanError, match="重复"):
        validate_dag(normalize_plan({"tasks": [_task("A"), _task("A")]}))
    with pytest.raises(PlanError, match="未知任务"):
        validate_dag(normalize_plan({"tasks": [_task("A", deps=["NOPE"])]}))
    with pytest.raises(PlanError, match="风险级别"):
        normalize_plan({"tasks": [_task("A", risk="L9")]})
    with pytest.raises(PlanError, match="空"):
        normalize_plan({"tasks": []})
    with pytest.raises(PlanError, match="缺 id"):
        normalize_plan({"tasks": [{"title": "x"}]})
    with pytest.raises(PlanError, match="依赖自身"):
        validate_dag(normalize_plan({"tasks": [_task("A", deps=["A"])]}))


def test_plan_acceptance_and_owner_defaults():
    node = normalize_plan({"tasks": [{"id": "A", "acceptance": "用例绿"}]})[0]
    assert node.owner == "TIANFENG" and node.acceptance == ["用例绿"]


# ── 汇总：共识 / 分歧 / 升级 ─────────────────────────────────────────────

def test_consensus_requires_core_trio_and_no_oppose():
    runners = {r: _runner() for r in ROLES}
    runners["HUANGZHONG"] = _runner(position="oppose",
                                    conditions=["先补回滚预案再谈通过"])
    orch = Orchestrator(planner=_planner([_task("T1")]), role_runners=runners)
    report = orch.run("会诊")
    s = report["summary"]
    assert s["consensus"] is False and s["decision_state"] == "escalated"
    assert any("oppose" in r for r in s["escalate_reasons"])
    assert s["divergences"][0]["role"] == "HUANGZHONG"


def test_missing_role_escalates():
    runners = {r: _runner() for r in ROLES if r != "ZHUGE"}
    orch = Orchestrator(planner=_planner([_task("T1")]), role_runners=runners)
    report = orch.run("缺角色")
    s = report["summary"]
    assert s["consensus"] is False
    assert "ZHUGE" in s["roles_missing"]
    assert any("ZHUGE" in r for r in s["escalate_reasons"])


def test_role_exception_captured_not_raised():
    def boom(role, topic, goal, dag, constraints):
        raise RuntimeError("fake-role-crash")

    runners = {r: _runner() for r in ROLES}
    runners["ZHUGE"] = boom
    orch = Orchestrator(planner=_planner([_task("T1")]), role_runners=runners)
    report = orch.run("角色异常")
    assert "ZHUGE" in report["role_errors"]
    assert "fake-role-crash" in report["role_errors"]["ZHUGE"]
    assert report["summary"]["consensus"] is False


def test_role_wrong_role_or_topic_rejected():
    runners = {r: _runner() for r in ROLES}
    runners["ZHAOYUN"] = _runner(topic_out="HQ-WRONG-TOPIC")
    report = Orchestrator(planner=_planner([_task("T1")]),
                          role_runners=runners).run("错 topic")
    assert "ZHAOYUN" in report["role_errors"]
    assert "topic_id" in report["role_errors"]["ZHAOYUN"]


def test_conditional_opinions_collect_conditions_and_consensus():
    positions = {"ZHUGE": "conditional", "GUANYU": "conditional"}
    runners = {}
    for role in ROLES:
        if role in positions:
            runners[role] = _runner(position="conditional",
                                    conditions=["需补证据链-" + role])
        else:
            runners[role] = _runner()
    report = Orchestrator(planner=_planner([_task("T1")]),
                          role_runners=runners).run("条件通过")
    s = report["summary"]
    assert s["consensus"] is True
    assert len(s["conditions"]) == 2
    assert s["position_counts"]["conditional"] == 2


def test_l2_forces_escalate_even_all_support():
    orch = _orchestrator([_task("T1", risk="L2")])
    report = orch.run("涉及生产发布")
    s = report["summary"]
    assert s["consensus"] is False
    assert s["l2_tasks"] == ["T1"]
    assert any("L2" in r for r in s["escalate_reasons"])
    assert orch.state == "escalated"


# ── 状态机 ───────────────────────────────────────────────────────────────

def test_state_machine_legal_chain_and_illegal_jumps():
    orch = _orchestrator([_task("T1")])
    with pytest.raises(StateError):
        orch.decide("A")                     # draft 不能直接决议
    report = orch.run("状态机")
    assert orch.state == "consensus"
    with pytest.raises(StateError):
        orch.begin_execution()               # 未决议
    with pytest.raises(StateError):
        orch.mark_done()                     # 未执行
    decision = orch.decide("A｜执行", reason="共识")
    assert decision["decision_id"].startswith("DEC-")
    assert orch.state == "decided"
    orch.begin_execution()
    assert orch.state == "executing"
    with pytest.raises(StateError):
        orch.decide("B")                     # 已决议/执行阶段
    orch.mark_done([{"kind": "test", "ref": "pytest", "verified": True,
                     "digest": "sha256:deadbeef"}])
    assert orch.state == "done"
    with pytest.raises(StateError):
        orch.rollback("too late")            # done 终态
    assert report["executed"] is False


def test_rollback_path_and_requires_reason():
    orch = _orchestrator([_task("T1")])
    orch.run("回滚路径")
    orch.decide("A")
    orch.begin_execution()
    with pytest.raises(OrchestratorError, match="reason"):
        orch.rollback("")
    orch.rollback("发现回滚需求")
    assert orch.state == "rolled_back"


def test_escalated_can_be_decided_by_human():
    orch = _orchestrator([_task("T1", risk="L2")])
    orch.run("L2 会诊")
    with pytest.raises(OrchestratorError, match="reason"):
        orch.decide("B")                     # L2 人工定版必须给 reason
    d = orch.decide("B｜附条件", operator="laotian", reason="老田窗口批准（占位）")
    assert d["requires_human"] is True
    with pytest.raises(OrchestratorError, match="confirmation"):
        orch.begin_execution()               # L2 执行态需人工授权占位
    orch.begin_execution(operator="laotian", confirmation="L2-ACK-PLACEHOLDER")
    assert orch.state == "executing"


# ── 幂等 / 重跑 / 快照 ───────────────────────────────────────────────────

def test_rerun_idempotent_same_inputs():
    orch = _orchestrator([_task("T1"), _task("T2", deps=["T1"])])
    r1 = orch.run("同题重跑", {"deadline": "11-14"})
    r2 = orch.run("同题重跑", {"deadline": "11-14"})
    assert r2["reused"] is True
    for key in ("run_id", "topic_id", "topology",
                "tasks", "summary", "opinions"):
        assert r2[key] == r1[key], key
    # 不同输入=另起一单（同一编排器持有单 run，需新实例/reset）
    other = _orchestrator([_task("T1"), _task("T2", deps=["T1"])]).run(
        "同题重跑", {"deadline": "11-15"})
    assert other["reused"] is False
    assert other["run_id"] != r1["run_id"]


def test_run_deterministic_across_instances():
    tasks = [_task("T1")]
    a = _orchestrator(tasks).run("确定性")
    b = _orchestrator(tasks).run("确定性")
    assert a["run_id"] == b["run_id"]
    assert [t["task_id"] for t in a["tasks"]] == [t["task_id"] for t in b["tasks"]]
    assert a["summary"] == b["summary"]


def test_snapshot_resume():
    orch = _orchestrator([_task("T1")])
    orch.run("断点续跑")
    decision = orch.decide("A")
    snap = orch.snapshot()
    assert snap["state"] == "decided"
    restored = Orchestrator.from_snapshot(
        snap, planner=_planner([_task("T1")]),
        role_runners={r: _runner() for r in ROLES})
    assert restored.state == "decided"
    assert restored._report["decision"] == decision
    restored.begin_execution()
    restored.mark_done()
    assert restored.state == "done"


def test_snapshot_validation():
    with pytest.raises(OrchestratorError, match="schema"):
        Orchestrator.from_snapshot({"schema": "wrong"})
    orch = _orchestrator([_task("T1")])
    with pytest.raises(StateError, match="快照"):
        orch.snapshot()


# ── 并发派单 ─────────────────────────────────────────────────────────────

def test_concurrent_dispatch_keeps_role_order():
    def slow_runner(role, topic, goal, dag, constraints):
        time.sleep(0.02)
        return _op(role, topic)

    runners = {r: slow_runner for r in ROLES}
    orch = Orchestrator(planner=_planner([_task("T1")]),
                        role_runners=runners, max_workers=5)
    report = orch.run("并发")
    assert list(report["opinions"]) == list(ROLES)
    assert report["summary"]["consensus"] is True


# ── 边界 ─────────────────────────────────────────────────────────────────

def test_missing_planner_and_empty_goal():
    with pytest.raises(OrchestratorError, match="planner"):
        Orchestrator().run("没 planner")
    orch = _orchestrator([_task("T1")])
    with pytest.raises(OrchestratorError, match="goal"):
        orch.run("   ")


def test_mid_run_conflict_rejected():
    orch = _orchestrator([_task("T1")])
    orch.run("第一单")
    with pytest.raises(OrchestratorError, match="进行中"):
        orch.run("第二单（不同输入）")
