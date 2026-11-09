# -*- coding: utf-8 -*-
"""HQ 指挥部编排器 MVP（W-C2，K-ORG-20261101-HQORCH）。

一条链：老田思想/目标 + 约束 → 田丰拆解 task DAG → 五角色各出意见
→ 汇总共识/分歧/需升级 → 状态机推进。**只编排，不执行任何真实动作**：
不调用真实模型（planner/role runner 全部可注入，生产接线另行审批），
不启动进程、不写业务账本、不自动执行 devtask/发布/生产变更。

状态机（专项计划 §4 W-C2）::

    draft → debate → consensus | escalated → decided → executing → done | rolled_back

- 非法跳转抛 ``StateError``；
- ``consensus`` 判定（W-C3 口径的 MVP 子集）：田丰/黄忠/关羽三席均在场且
  support|conditional，无 oppose，无 escalate，无 P0 风险，且 **无 L2 任务**；
  否则 ``escalated``（L2 强制升级，宁可升级不可假共识）；
- 幂等/重跑：同一 goal+constraints+plan 的 ``run_id`` 稳定；重复 run 返回
  同一报告（``reused=True``），不重复派单；``decide`` 同参幂等；
- 快照/恢复：``snapshot()`` / ``from_snapshot()`` 支持断点续跑（不执行动作）。

与既有模块的关系（K-ORG-20261101-HQWIRE 接线）：
- 意见统一由 ``role_contract.normalize_opinion`` 严格校验（唯一契约源）；
- ``run`` 末尾调用 ``consensus.build_consensus`` 产出 ``auto_ready/escalated``
  判定 + blockers，并用 ``ensure_countersign`` 卡“无 verified 证据不算共识”；
  W-C2 旧汇总字段保留（summary 兼容），判定状态以 consensus 引擎为准；
- 不 import mission_planner/worker/approval_control（避免误触真实 LLM/执行）；
  planner 注入接口兼容 ``mission_planner.plan()`` 的 ``{"tasks":[{id,deps,...}]}``；
  后续 W-C4 执行门再接线（另单）。
"""
from __future__ import annotations

import copy
import hashlib
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

try:
    # 唯一契约源 = W-C1 HQROLE 正式 role_contract（本集成分支删除 HQORCH 本地占位）
    from .role_contract import (
        POSITIONS,
        ROLES,
        RoleOpinionError,
        normalize_opinion,
    )
except ImportError:  # 直接以目录导入（测试/脚本场景）
    from role_contract import (  # type: ignore
        POSITIONS,
        ROLES,
        RoleOpinionError,
        normalize_opinion,
    )

try:
    # W-C3 共识引擎（HQCONS）；编排器只调判定，不建卡/不发飞书
    from .consensus import build_consensus, ensure_countersign
except ImportError:  # 直接以目录导入
    from consensus import build_consensus, ensure_countersign  # type: ignore

SCHEMA = "hq_orchestrator/v1"
REPORT_SCHEMA = "hq_orchestrator_report/v1"
STATES = ("draft", "debate", "consensus", "escalated", "decided",
          "executing", "done", "rolled_back")
TRANSITIONS = {
    "draft": {"debate"},
    "debate": {"consensus", "escalated"},
    "consensus": {"decided"},
    "escalated": {"decided"},
    "decided": {"executing"},
    "executing": {"done", "rolled_back"},
    "done": set(),
    "rolled_back": set(),
}
RISK_LEVELS = ("L0", "L1", "L2")
CONSENSUS_REQUIRED_ROLES = ("TIANFENG", "HUANGZHONG", "GUANYU")
CONSENSUS_MIN_SUPPORT = 3
MAX_TASKS = 64


class OrchestratorError(Exception):
    """编排器通用错误。"""


class PlanError(OrchestratorError):
    """task DAG 非法（重复 id/未知依赖/循环依赖/风险级别非法）。"""


class StateError(OrchestratorError):
    """状态机非法跳转/前置条件不满足。"""


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------
def _canonical(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha(obj: Any) -> str:
    return hashlib.sha256(_canonical(obj).encode("utf-8")).hexdigest()


def _norm_constraints(constraints: Any) -> Dict[str, Any]:
    if constraints is None:
        return {}
    if isinstance(constraints, dict):
        return copy.deepcopy(constraints)
    if isinstance(constraints, (list, tuple)):
        return {"list": [str(x) for x in constraints]}
    return {"text": str(constraints)}


def _as_list(value: Any) -> List[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, Iterable):
        return [str(v) for v in value if str(v).strip()]
    return [str(value)]


@dataclass
class TaskNode:
    task_id: str
    title: str
    owner: str = "TIANFENG"
    deps: List[str] = field(default_factory=list)
    acceptance: List[str] = field(default_factory=list)
    risk_level: str = "L0"
    status: str = "pending"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_plan(plan: Any) -> List[TaskNode]:
    """plan → TaskNode 列表（兼容 mission_planner 的 {"tasks":[{id,deps}]}）。"""
    if isinstance(plan, dict):
        tasks = plan.get("tasks")
    else:
        tasks = plan
    if not isinstance(tasks, (list, tuple)) or not tasks:
        raise PlanError("planner 输出为空：需要至少 1 个任务（tasks 非空数组）")
    if len(tasks) > MAX_TASKS:
        raise PlanError("任务数 %d 超上限 %d（编排器 MVP 防巨石）"
                        % (len(tasks), MAX_TASKS))
    nodes: List[TaskNode] = []
    for idx, raw in enumerate(tasks, 1):
        if isinstance(raw, TaskNode):
            nodes.append(copy.deepcopy(raw))
            continue
        if not isinstance(raw, dict):
            raise PlanError("tasks[%d] 不是对象" % idx)
        task_id = str(raw.get("id") or raw.get("task_id") or "").strip()
        if not task_id:
            raise PlanError("tasks[%d] 缺 id" % idx)
        title = str(raw.get("title") or raw.get("name") or task_id).strip()
        owner = str(raw.get("owner") or "TIANFENG").strip()
        deps = _as_list(raw.get("deps") if raw.get("deps") is not None
                        else raw.get("depends_on"))
        acceptance = _as_list(raw.get("acceptance"))
        risk = str(raw.get("risk_level") or raw.get("risk") or "L0").strip().upper()
        if risk not in RISK_LEVELS:
            raise PlanError("tasks[%d] risk_level=%r 风险级别非法，须在 %s"
                            % (idx, risk, list(RISK_LEVELS)))
        nodes.append(TaskNode(task_id=task_id, title=title, owner=owner,
                              deps=deps, acceptance=acceptance,
                              risk_level=risk))
    return nodes


def validate_dag(nodes: List[TaskNode]) -> List[TaskNode]:
    """校验 DAG 并返回拓扑序（同层保持 planner 原顺序）；循环依赖抛 PlanError。"""
    ids = [n.task_id for n in nodes]
    if len(set(ids)) != len(ids):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        raise PlanError("重复 task_id：%s" % dup)
    id_set = set(ids)
    order = {task_id: i for i, task_id in enumerate(ids)}
    for n in nodes:
        for dep in n.deps:
            if dep not in id_set:
                raise PlanError("task %s 依赖未知任务 %s" % (n.task_id, dep))
            if dep == n.task_id:
                raise PlanError("task %s 依赖自身（循环）" % n.task_id)
    # Kahn 拓扑排序
    indeg = {n.task_id: 0 for n in nodes}
    children: Dict[str, List[str]] = {n.task_id: [] for n in nodes}
    for n in nodes:
        for dep in n.deps:
            indeg[n.task_id] += 1
            children[dep].append(n.task_id)
    ready = sorted([tid for tid, d in indeg.items() if d == 0], key=order.get)
    result: List[TaskNode] = []
    by_id = {n.task_id: n for n in nodes}
    while ready:
        tid = ready.pop(0)
        result.append(by_id[tid])
        for child in sorted(children[tid], key=order.get):
            indeg[child] -= 1
            if indeg[child] == 0:
                ready.append(child)
        ready.sort(key=order.get)
    if len(result) != len(nodes):
        cycle = sorted(tid for tid, d in indeg.items() if d > 0)
        raise PlanError("依赖存在循环：%s" % cycle)
    return result


# ---------------------------------------------------------------------------
# 编排器
# ---------------------------------------------------------------------------
class Orchestrator:
    """W-C2 指挥部编排器（纯编排，无真实模型/执行）。

    planner(goal, constraints) -> {"tasks":[...]} 或 [task,...]
    role_runner(role, topic, goal, dag, constraints) -> RoleOpinion|dict
    """

    def __init__(self, planner: Optional[Callable] = None,
                 role_runners: Optional[Dict[str, Callable]] = None, *,
                 max_workers: int = 1):
        self.planner = planner
        self.role_runners: Dict[str, Callable] = dict(role_runners or {})
        self.max_workers = max(1, int(max_workers))
        self._lock = threading.RLock()
        self._state: Optional[str] = None
        self._report: Optional[Dict[str, Any]] = None
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._history: List[Dict[str, str]] = []

    # ---- 注入 ----
    def register_role(self, role: str, runner: Callable) -> None:
        if role not in ROLES:
            raise OrchestratorError("未知角色 %r（五角色：%s）" % (role, list(ROLES)))
        if not callable(runner):
            raise OrchestratorError("role runner 必须 callable")
        self.role_runners[role] = runner

    # ---- 状态机 ----
    @property
    def state(self) -> Optional[str]:
        return self._state

    def _transition(self, target: str, *, reason: str = "") -> None:
        current = self._state or "draft"
        if target not in STATES:
            raise StateError("未知状态 %r" % target)
        allowed = TRANSITIONS.get(current, set())
        if target not in allowed:
            raise StateError("非法状态跳转：%s → %s（允许 %s）"
                             % (current, target, sorted(allowed) or "无（终态）"))
        self._history.append({"from": current, "to": target, "reason": reason})
        self._state = target
        if self._report is not None:
            self._report["state"] = target
            self._report["history"] = copy.deepcopy(self._history)

    def _require(self, *states: str) -> None:
        if self._state not in states:
            raise StateError("当前状态 %r，要求 %s" % (self._state, list(states)))

    # ---- 派单 ----
    def _dispatch(self, topic_id: str, goal: str,
                  nodes: List[TaskNode], constraints: Dict[str, Any]
                  ) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
        dag = [n.to_dict() for n in nodes]
        missing = [r for r in ROLES if r not in self.role_runners]
        opinions: Dict[str, Dict[str, Any]] = {}
        errors: Dict[str, str] = {r: "未注入 runner" for r in missing}

        def _call(role: str, runner: Callable) -> Tuple[str, Dict[str, Any], str]:
            try:
                raw = runner(role, topic_id, goal, dag, copy.deepcopy(constraints))
                if hasattr(raw, "to_dict"):
                    raw = raw.to_dict()
                if not isinstance(raw, dict):
                    raise RoleOpinionError(
                        ["runner 返回 %r（应 RoleOpinion dict）" % type(raw).__name__])
                op = normalize_opinion(raw)          # 唯一契约：HQROLE 严格校验
                if op["role"] != role:
                    raise RoleOpinionError(
                        ["role 不符：返回 %r 派单 %r" % (op["role"], role)])
                if op["topic_id"] != topic_id:
                    raise RoleOpinionError(
                        ["topic_id 不符：返回 %r 当前 %r"
                         % (op["topic_id"], topic_id)])
                return role, op, ""
            except Exception as exc:  # noqa: BLE001 - 单角色失败 fail-visible，不拖垮整轮
                return role, {}, "%s: %s" % (type(exc).__name__, str(exc)[:200])

        active = [(r, self.role_runners[r]) for r in ROLES if r in self.role_runners]
        results: List[Tuple[str, Dict[str, Any], str]] = []
        if self.max_workers > 1 and len(active) > 1:
            with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
                futures = [pool.submit(_call, r, fn) for r, fn in active]
                results = [f.result() for f in futures]
        else:
            results = [_call(role, fn) for role, fn in active]
        for role, payload, err in results:
            if err:
                errors[role] = err
            else:
                opinions[role] = payload
        return opinions, errors

    # ---- 汇总 ----
    def _summarize(self, nodes: List[TaskNode],
                   opinions: Dict[str, Dict[str, Any]],
                   errors: Dict[str, str]) -> Dict[str, Any]:
        position_counts = {p: 0 for p in POSITIONS}
        divergences: List[Dict[str, str]] = []
        conditions: List[str] = []
        risks: List[str] = []
        escalate_reasons: List[str] = []
        for role in ROLES:
            op = opinions.get(role)
            if op is None:
                continue
            position_counts[op["position"]] = position_counts.get(op["position"], 0) + 1
            if op["position"] in ("oppose", "abstain"):
                divergences.append({"role": role, "position": op["position"],
                                    "recommendation": op.get("recommendation", "")})
            if op["position"] == "conditional":
                conditions.extend(op.get("conditions") or [])
            if op.get("escalate"):
                escalate_reasons.append("%s: %s" % (role, op.get("escalate_reason") or ""))
            for risk in op.get("risks") or []:
                risks.append("%s: %s" % (role, risk))
        l2_tasks = [n.task_id for n in nodes if n.risk_level == "L2"]
        if l2_tasks:
            escalate_reasons.append("含 L2 任务，强制人工定版：%s" % l2_tasks)
        if errors:
            escalate_reasons.append("角色意见缺失/非法：%s" % sorted(errors))
        for role in CONSENSUS_REQUIRED_ROLES:
            op = opinions.get(role)
            if op is None:
                escalate_reasons.append("共识必需角色缺失：%s" % role)
            elif op["position"] not in ("support", "conditional"):
                escalate_reasons.append("共识必需角色 %s position=%s"
                                        % (role, op["position"]))
        if position_counts.get("oppose", 0) > 0:
            escalate_reasons.append("存在 oppose（保留反对意见，必须升级）")
        p0_risks = [r for r in risks if "P0" in r]
        if p0_risks:
            escalate_reasons.append("存在 P0 风险：%s" % p0_risks[:5])
        support_like = position_counts.get("support", 0) + position_counts.get("conditional", 0)
        consensus = (not escalate_reasons
                     and support_like >= CONSENSUS_MIN_SUPPORT
                     and not l2_tasks)
        return {
            "consensus": consensus,
            "decision_state": "consensus" if consensus else "escalated",
            "position_counts": position_counts,
            "support_like": support_like,
            "divergences": divergences,
            "conditions": conditions,
            "risks": risks,
            "escalate_reasons": escalate_reasons,
            "l2_tasks": l2_tasks,
            "roles_missing": sorted(errors),
            "roles_total": len(opinions),
        }

    # ---- 共识引擎（W-C3 接线） ----
    @staticmethod
    def _risk_level_for(nodes: List[TaskNode]) -> str:
        """DAG 最高风险级别 → consensus 入参（任一 L2 则 L2 强制升级）。"""
        levels = {n.risk_level for n in nodes}
        if "L2" in levels:
            return "L2"
        if "L1" in levels:
            return "L1"
        return "L0"

    def _evaluate_consensus(self, topic_id: str, nodes: List[TaskNode],
                            opinions: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """调用 HQCONS 判定引擎；角色意见已过 role_contract 严格校验。

        退出证据门槛 ``ensure_countersign``：无 verified 证据的“共识”降级升级。
        引擎异常 fail-closed：返回 escalated + consensus_unavailable blocker，不崩。
        """
        risk = self._risk_level_for(nodes)
        try:
            raw = build_consensus(list(opinions.values()), topic_id=topic_id,
                                  risk_level=risk)
            result = ensure_countersign(raw)
        except Exception as exc:  # noqa: BLE001 - 共识引擎失败必须 fail-closed 可见
            return {
                "ok": False,
                "schema": "hq_consensus/v1",
                "topic_id": topic_id,
                "risk_level": risk,
                "verdict": "escalate",
                "auto_ready": False,
                "escalate": True,
                "decision_id": "",
                "reason": "consensus_unavailable：%s: %s"
                          % (type(exc).__name__, str(exc)[:200]),
                "blockers": [{"code": "consensus_unavailable",
                              "detail": "%s: %s" % (type(exc).__name__,
                                                    str(exc)[:200])}],
            }
        return {"ok": True, "risk_level": risk, **result}

    @staticmethod
    def _apply_consensus(summary: Dict[str, Any],
                         consensus_eval: Dict[str, Any]) -> Dict[str, Any]:
        """以 consensus 判定为权威覆盖旧 summary 的 consensus/decision_state。"""
        out = dict(summary)
        blockers = list(consensus_eval.get("blockers") or [])
        auto_ready = bool(consensus_eval.get("auto_ready"))
        out["consensus"] = auto_ready
        out["decision_state"] = "consensus" if auto_ready else "escalated"
        out["consensus_verdict"] = consensus_eval.get("verdict") or "escalate"
        out["consensus_blockers"] = blockers
        out["consensus_ok"] = bool(consensus_eval.get("ok"))
        reasons = list(out.get("escalate_reasons") or [])
        seen = set(reasons)
        for blocker in blockers:
            text = "%s：%s" % (blocker.get("code"), blocker.get("detail"))
            if text not in seen:
                seen.add(text)
                reasons.append(text)
        if auto_ready:
            reasons = [r for r in reasons if not r.startswith("consensus_unavailable")]
        out["escalate_reasons"] = reasons
        out["blockers"] = blockers
        return out

    # ---- 主流程 ----
    def run(self, goal: str, constraints: Any = None, *,
            force_rerun: bool = False) -> Dict[str, Any]:
        """目标 → DAG → 五角色意见 → 汇总（不执行任何动作）。幂等可重跑。"""
        goal = str(goal or "").strip()
        if not goal:
            raise OrchestratorError("goal 不能为空（输入=老田思想/目标）")
        if self.planner is None:
            raise OrchestratorError("未注入 planner：本编排器不调用真实模型，"
                                    "请注入田丰拆解 callable（测试用 fake）")
        cons = _norm_constraints(constraints)
        with self._lock:
            nodes = validate_dag(normalize_plan(self.planner(goal, copy.deepcopy(cons))))
            plan_canonical = [n.to_dict() for n in nodes]
            run_id = "HQ-" + _sha({"goal": goal, "constraints": cons,
                                   "tasks": plan_canonical})[:12]
            if run_id in self._cache and not force_rerun:
                cached = copy.deepcopy(self._cache[run_id])
                cached["reused"] = True
                return cached
            if self._state not in (None, "done", "rolled_back"):
                raise OrchestratorError("已有进行中的 run（state=%s）；"
                                        "请先 snapshot/reset 或完成当前流程"
                                        % self._state)
            self._state = None
            self._history = []
            self._transition("debate", reason="goal 解析完成，进入五角色讨论")
            opinions, errors = self._dispatch(run_id, goal, nodes, cons)
            summary = self._summarize(nodes, opinions, errors)
            consensus_eval = self._evaluate_consensus(run_id, nodes, opinions)
            summary = self._apply_consensus(summary, consensus_eval)
            report = {
                "schema": REPORT_SCHEMA,
                "topic_id": run_id,
                "run_id": run_id,
                "goal": goal,
                "constraints": cons,
                "tasks": [n.to_dict() for n in nodes],
                "topology": [n.task_id for n in nodes],
                "opinions": opinions,
                "role_errors": errors,
                "summary": summary,
                # W-C3 共识引擎判定（只内存产出；不建飞书卡/不发送）
                "consensus": consensus_eval,
                "decision": None,
                "execution": {"attempted": False, "dry_run": True,
                              "actions": [], "evidence": [],
                              "note": "编排器 MVP 不执行任何真实动作"},
                "state": None,
                "history": [],
                "executed": False,
                "reused": False,
            }
            self._report = report
            self._transition(summary["decision_state"],
                             reason="汇总：%s" % summary["decision_state"])
            report["state"] = self._state
            self._cache[run_id] = copy.deepcopy(report)
            return copy.deepcopy(report)

    # ---- 决议 / 执行状态（不执行真实动作） ----
    def decide(self, choice: str, *, operator: str = "laotian",
               reason: str = "") -> Dict[str, Any]:
        """人工定版（L2/升级必须 human）：只记录决议，不执行。"""
        self._require("consensus", "escalated")
        choice = str(choice or "").strip()
        operator = str(operator or "").strip()
        if not choice or not operator:
            raise OrchestratorError("decide 需要 choice 与 operator（谁定版）")
        if self._report is None:
            raise StateError("无当前 run")
        report = self._report
        summary = report["summary"]
        if summary.get("l2_tasks") and not str(reason or "").strip():
            raise OrchestratorError("含 L2 任务：人工定版必须给 reason（一次一议）")
        existing = report.get("decision")
        if existing:
            if existing.get("choice") == choice and existing.get("operator") == operator:
                return copy.deepcopy(existing)  # 幂等
            raise StateError("本 run 已有决议：%s" % existing)
        decision = {
            "decision_id": "DEC-" + _sha({"run_id": report["run_id"],
                                          "choice": choice,
                                          "operator": operator})[:12],
            "run_id": report["run_id"],
            "choice": choice,
            "operator": operator,
            "reason": str(reason or ""),
            "requires_human": bool(summary.get("l2_tasks")),
        }
        report["decision"] = decision
        self._transition("decided", reason="人工定版 %s" % decision["decision_id"])
        self._cache[report["run_id"]] = copy.deepcopy(report)
        return copy.deepcopy(decision)

    def begin_execution(self, *, operator: str = "laotian",
                        confirmation: str = "") -> Dict[str, Any]:
        """标记进入执行态（**不执行任何动作**；真实执行门属 W-C4 另单）。"""
        self._require("decided")
        report = self._report or {}
        if not report.get("decision"):
            raise StateError("缺少人工决议，不能进入执行态")
        if report["summary"].get("l2_tasks") and not str(confirmation or "").strip():
            raise OrchestratorError("含 L2 任务：进入执行态需 confirmation（L2 人工授权占位）")
        self._transition("executing", reason="进入执行态（dry-run，无真实动作）")
        report["execution"].update({"attempted": True, "operator": operator,
                                    "confirmation": str(confirmation or "")})
        self._cache[report["run_id"]] = copy.deepcopy(report)
        return copy.deepcopy(report["execution"])

    def mark_done(self, evidence: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """执行态 → done（本单只翻转状态，不产生真实产物）。"""
        self._require("executing")
        report = self._report or {}
        report["execution"]["evidence"] = copy.deepcopy(evidence or [])
        self._transition("done", reason="执行完成（状态机层面）")
        self._cache[report["run_id"]] = copy.deepcopy(report)
        return copy.deepcopy(report["execution"])

    def rollback(self, reason: str) -> Dict[str, Any]:
        """执行态 → rolled_back（记录回滚意图；不执行真实回滚动作）。"""
        self._require("executing")
        if not str(reason or "").strip():
            raise OrchestratorError("rollback 必须给 reason")
        report = self._report or {}
        report["execution"]["rollback_reason"] = str(reason)
        self._transition("rolled_back", reason="回滚：%s" % reason)
        self._cache[report["run_id"]] = copy.deepcopy(report)
        return copy.deepcopy(report["execution"])

    # ---- 快照 / 恢复 ----
    def snapshot(self) -> Dict[str, Any]:
        if self._report is None:
            raise StateError("无当前 run 可快照")
        return {
            "schema": SCHEMA,
            "state": self._state,
            "history": copy.deepcopy(self._history),
            "report": copy.deepcopy(self._report),
        }

    @classmethod
    def from_snapshot(cls, snapshot: Dict[str, Any], *,
                      planner: Optional[Callable] = None,
                      role_runners: Optional[Dict[str, Callable]] = None,
                      max_workers: int = 1) -> "Orchestrator":
        if not isinstance(snapshot, dict) or snapshot.get("schema") != SCHEMA:
            raise OrchestratorError("快照 schema 非法（期望 %s）" % SCHEMA)
        state = snapshot.get("state")
        if state not in STATES:
            raise OrchestratorError("快照 state=%r 非法" % state)
        report = snapshot.get("report")
        if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
            raise OrchestratorError("快照 report 非法")
        obj = cls(planner=planner, role_runners=role_runners,
                  max_workers=max_workers)
        obj._state = state
        obj._report = copy.deepcopy(report)
        obj._history = copy.deepcopy(snapshot.get("history") or [])
        obj._cache[report["run_id"]] = copy.deepcopy(report)
        return obj

    def reset(self) -> None:
        with self._lock:
            self._state = None
            self._report = None
            self._history = []
