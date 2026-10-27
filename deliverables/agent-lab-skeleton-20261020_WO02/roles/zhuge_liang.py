"""中台·统筹席（诸葛亮）骨架——**常任管理席**。

常任管理席定位（老田 2026-10-20 校准）：本席是数字军团常任管理席，充当管理角色、做
正常管理；**监督整改只是管理职能的一个分支**，不是本席职责全部。
职责：目标分解为任务单、指挥各业务板块开展业务工作；监督督促平台执行，
发现问题打成问题单反馈田丰归口（登记去重→研发工单→无单不写码→过评审门合并→
验收归口田丰）。
边界：只出「任务单、问题单与建议」，不代执行、不代签准、不直接安排研发改代码；
**本席永不直发本人**——升级请示交归口席分级，阻断类由归口席以 escalation_request /
remediation_ticket 类直发本人；签准/交易/生产变更类走 approval_gate 待签 +
risk_guard 预警双动作的例外通道，不经整改线。

分工配置位（待老田裁定）：与执行席（赵云）的监督分工尚未定稿，
构造参数 division_config 仅作占位登记，不承载任何具体分工逻辑。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from roles.base_agent import BaseAgent, Report, TaskState
from roles.rectification_line import (PREFIX_ESCALATION_ROUTE,
                                      SEVERITY_ESCALATION,
                                      normalize_severity)

# 合法七态迁移表：created→assigned→doing→review→done；任意→blocked/cancelled。
# blocked 的解阻路径未定稿，暂仅允许 blocked→cancelled（待老田裁定）。
LEGAL_TRANSITIONS: Dict[TaskState, set] = {
    TaskState.CREATED: {TaskState.ASSIGNED, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.ASSIGNED: {TaskState.DOING, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.DOING: {TaskState.REVIEW, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.REVIEW: {TaskState.DONE, TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.DONE: {TaskState.BLOCKED, TaskState.CANCELLED},
    TaskState.BLOCKED: {TaskState.CANCELLED},
    TaskState.CANCELLED: set(),
}


def _state_value(state: Any) -> str:
    return getattr(state, "value", state)


class ZhugeLiang(BaseAgent):
    def __init__(self, division_config: Optional[Dict[str, Any]] = None,
                 **kw: Any) -> None:
        super().__init__(name="zhuge_liang", role="command:manager",
                         level="command", **kw)
        self.open_tasks: Dict[str, Dict[str, Any]] = {}
        # 分工配置位（待老田裁定）：仅登记，不据此产生行为。
        self.division_config = division_config

    # ---- 输入：目标单（来自 os_commander） ----

    def receive_goal(self, goal: Dict[str, Any]) -> List[Dict[str, Any]]:
        """把目标单分解为任务单列表。

        TODO(领域实现): 分解策略可替换——当前为最小实现：按
        goal["platforms"] 逐平台一单一事拆分，不做优先级/依赖编排。
        每张任务单携带：goal_id、承接平台、验收口径（acceptance 透传）；
        state 固定 created，先登记再派发（无单不写码）。
        """
        goal_id = str(goal.get("goal_id", "")).strip()
        tasks: List[Dict[str, Any]] = []
        # TODO(可替换策略): 优先级排序、依赖编排、单量上限等分解策略。
        for platform in goal.get("platforms") or []:
            task_id = f"{goal_id}-{platform}"
            task = {
                "task_id": task_id,
                "goal_id": goal_id,
                "platform": platform,
                "state": TaskState.CREATED.value,
                "acceptance": goal.get("acceptance"),
                "history": [],
                "created_ts": time.time(),
            }
            self.open_tasks[task_id] = task
            tasks.append(task)
        return tasks

    # ---- 输入：平台状态与回执 ----

    def on_platform_status(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """收集平台状态，对照任务单进度做偏差打标。

        发现偏差则构造问题单 dict（source=zhuge_liang）返回，由归口
        （田丰）处置——本席不直接派研发。
        TODO(领域实现): 偏差判定细则（执行偏差/口径不一致/结论矛盾）、
        冲突仲裁建议上报 os_commander、升级窗口。
        """
        task_id = status.get("task_id")
        task = self.open_tasks.get(task_id) if task_id else None
        # TODO(可替换策略): 以下为最小偏差判定，细则待定稿。
        reason: Optional[str] = None
        if task is None:
            reason = "状态点无对应任务单"
        elif status.get("blocked") or status.get("delayed"):
            reason = "平台自报受阻或逾期"
        elif status.get("state") is not None and \
                _state_value(status.get("state")) != task["state"]:
            reason = "平台回报状态与任务单台账不一致"

        if reason is None:
            return {"deviation": False, "issue": None}

        issue = {
            "source": self.name,
            "type": "deviation",
            "reason": reason,
            "task_id": task_id,
            "platform": status.get("platform"),
            "evidence": dict(status),
            "ts": time.time(),
        }
        return {"deviation": True, "issue": issue}

    # ---- 问题反馈：监督整改线 ----

    def report_issue(self, subject: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """监督中发现的问题打成结构化问题单（附证据）反馈田丰归口。

        不直接派研发、不绕过归口。涉及签准/交易/生产变更的例外类别问题，
        走 approval_gate 待签 + risk_guard 预警**双动作**的例外通道（不经整改线）；
        **本席自身永不直发本人**。
        TODO(领域实现): 经总线送达 tian_feng.intake_issue 并留痕。
        """
        issue = {
            "source": self.name,
            "subject": subject,
            "evidence": evidence,
            "ts": time.time(),
        }
        self.report_up(Report(level="command", source=self.name,
                              subject=f"[问题单] {subject}",
                              body="反馈对象: tian_feng; 证据待归口登记"))
        # TODO(领域实现): evidence 结构化并调用 tian_feng.intake_issue
        return issue

    # ---- 升级上报 ----

    def escalate(self, subject: str, body: str, evidence: Dict[str, Any],
                 severity: Any = SEVERITY_ESCALATION) -> Dict[str, Any]:
        """升级请求**交归口席分级**（老田 2026-10-20 方案B 裁定）。

        监督席永不直发本人：本方法固定以 command 层把升级请求送归口席 tian_feng，
        分级判定在归口席；仅阻断类（blocking）由归口席以 remediation_ticket /
        escalation_request 类直发本人。非阻断类由归口席改走 command 层呈报。

        severity 仅为来源声明的分级线索（normal/escalation/blocking），
        **本席无权自行判定阻断类**——那属归口席的判定权。

        TODO(领域实现): 升级分级细则与佐证规则、evidence 写入总线账本供复核，
        不只在正文转述；升级不改变 approval_gate 的签准要求（沉默与"继续"不是签字）。
        """
        sev = normalize_severity(severity)
        report = Report(level="command", source=self.name,
                        subject=f"{PREFIX_ESCALATION_ROUTE} {subject}",
                        body=("归口对象: tian_feng; "
                              f"来源声明严重度: {sev}（阻断类判定权在归口席）; "
                              f"{body}"))
        self.report_up(report)
        return {"subject": subject, "severity_declared": sev,
                "route": "tian_feng", "level": "command",
                "direct_to_principal": False,
                "note": "阻断类仅由归口席直发本人；监督席不直发"}

    # ---- 任务单生命周期 ----

    def transition(self, task_id: str, to_state: TaskState,
                   actor: str) -> Dict[str, Any]:
        """七态流转登记：仅放行合法迁移，非法迁移抛 ValueError。

        TODO(领域实现): actor 权限校验（谁有权推动哪个迁移）。
        """
        task = self.open_tasks.get(task_id)
        if task is None:
            raise ValueError(f"未知任务单: {task_id}")
        if isinstance(to_state, str):
            try:
                to_state = TaskState(to_state)
            except ValueError:
                raise ValueError(f"未知任务态: {to_state}") from None
        from_state = TaskState(task["state"])
        if to_state not in LEGAL_TRANSITIONS[from_state]:
            raise ValueError(
                f"非法迁移: {from_state.value} → {to_state.value} (task={task_id})")
        task["state"] = to_state.value
        task["history"].append({
            "from": from_state.value, "to": to_state.value,
            "actor": actor, "ts": time.time(),
        })
        return task

    # ---- 简报接总线视图（S3 前置波次工位J 追加） ----

    def briefing_from_bus(self, bus: Any, goal_state: Any) -> Dict[str, Any]:
        """从汇报总线只读归集，组装四参调用 compile_briefing 出简报。

        v0 接线（S3 前置工位J）：数据源全部只读，不复制 compile_briefing
        逻辑，投影/计数/口径一律由该纯函数完成——
        - bus.board()：各平台最新状态点（总线已按 agent 取最新），交
          compile_briefing 做 platform/doing/progress/artifact 平台投影；
        - self.open_tasks：本席任务单台账 → 任务态计数；
        - 本席 issues 台账：问题单开/闭环计数，开/闭口径沿用
          compile_briefing 的 ISSUE_OPEN_STATUS / ISSUE_RESOLVED_STATUSES
          常量，不另造口径。台账属性（self.issues）尚未落位
          （S2 挂账），落位前按空清单处理，不造数；
        - goal_state 缺失（None/空）时如实进 data_gaps，不造数。

        只读操作：不 route、不 collect、不写本席任何账本。
        TODO(S3 平台接入): v0 接线；各平台真实状态点接入后，
        doing/progress/artifact 字段按真实平台数据补强。
        """
        status_points = list(bus.board())
        issues = list(getattr(self, "issues", []) or [])
        tasks = [dict(t) for t in self.open_tasks.values()]
        return compile_briefing(goal_state, status_points, issues, tasks)


# =====================================================================
# 简报汇编（S2 工位G 追加）：compile_briefing —— 中台快报最小实现。
#
# v0 契约：本函数输出字段结构为 v0 版本；任何字段增删必须版本化
# （升 v1 并同步调用方），不得在 v0 上静默变更结构。
# 纯函数约束：无 IO、无网络、单遍 O(n)；缺失输入如实进 data_gaps，
# 不造数；时间字段（ts 等）为字符串时直接透传比对，不做日期解析。
# =====================================================================

from datetime import datetime, timezone

BRIEFING_CONTRACT_VERSION = "v0"

# attention 规则表（表驱动，可配置：置 False 即关闭对应规则）。
BRIEFING_ATTENTION_RULES: Dict[str, bool] = {
    "progress_stagnant": True,  # 平台 progress 停滞
    "issue_open": True,         # 问题单未闭环
    "task_blocked": True,       # 任务处于 blocked
}

# 停滞判定：同一平台出现 ≥ STAGNATION_MIN_SAMPLES 个状态点且 progress
# 完全相同，且（数值 ts 可量化时）时间跨度 ≥ STAGNATION_MIN_SPAN_S。
# 取值说明：STAGNATION_MIN_SPAN_S=3600 为占位值，待真实基线标定（TODO）。
STAGNATION_MIN_SAMPLES = 2
STAGNATION_MIN_SPAN_S = 3600.0

# 问题单状态口径：status=="open" 视为未闭环；命中下表视为已闭环；
# 其余（含缺失）不计入任何计数，如实进 data_gaps。
ISSUE_OPEN_STATUS = "open"
ISSUE_RESOLVED_STATUSES = ("resolved", "closed", "done")


def _briefing_field(obj: Any, key: str) -> Any:
    """兼容 dict 与 StatusPoint 两种载体取字段。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _briefing_ts_key(ts: Any) -> tuple:
    """ts 排序键：数值与字符串各自成序，混排时保持确定顺序。

    字符串 ts 直接透传比对（字典序，ISO8601 适用），不做日期解析。
    """
    if isinstance(ts, bool):
        return (1, str(ts))
    if isinstance(ts, (int, float)):
        return (0, float(ts))
    return (1, str(ts))


def compile_briefing(goal_state: Any, status_points: Any,
                     issues: Any, tasks: Any) -> Dict[str, Any]:
    """汇编中台简报（v0 契约，纯函数，单遍 O(n)）。

    输出 v0 契约结构（字段增删须版本化）::

        {
          "goal": str,
          "generated_at": iso8601,
          "platforms": [{"platform", "doing", "progress", "artifact"}],
          "issues_open": int, "issues_resolved": int,
          "tasks_by_state": {state: int},
          "attention": [一句话要点],
          "data_gaps": [缺失字段如实列出],
        }

    规则：
    - platforms 投影按平台去重，同平台保留 ts 最新一条；ts 可为数值或
      字符串，字符串直接透传比对，不做复杂解析。
    - attention 规则表驱动（BRIEFING_ATTENTION_RULES 可配置）：
      progress 停滞 / 问题单未闭环 / 任务 blocked 即列入。
    - 停滞判定：同平台 ≥ STAGNATION_MIN_SAMPLES 个状态点 progress 完全
      相同，且数值 ts 跨度 ≥ STAGNATION_MIN_SPAN_S。该阈值取值为占位
      （3600s），待真实基线标定；字符串 ts 无法量化跨度时如实注明
      「跨度未量化」。
    - 缺失输入如实进 data_gaps，不造数：goal 缺失、状态点缺 platform/
      progress、问题单 status 缺失或未知、任务 state 缺失均逐条列出。
    """
    gaps: List[str] = []
    attention: List[str] = []

    # ---- goal ----
    if isinstance(goal_state, dict):
        goal = str(goal_state.get("goal") or goal_state.get("goal_id") or "").strip()
    else:
        goal = str(goal_state or "").strip()
    if not goal:
        gaps.append("goal 缺失（goal_state 未提供 goal/goal_id）")

    # ---- status_points：平台投影（去重取最新） + 停滞分组，单遍归组 ----
    latest_by_platform: Dict[str, tuple] = {}
    points_by_platform: Dict[str, List[Dict[str, Any]]] = {}
    seen_points: set = set()
    for idx, sp in enumerate(status_points or []):
        platform = str(_briefing_field(sp, "platform") or "").strip()
        doing = str(_briefing_field(sp, "doing") or "")
        progress = str(_briefing_field(sp, "progress") or "")
        artifact = str(_briefing_field(sp, "artifact") or "")
        ts = _briefing_field(sp, "ts")
        if not platform:
            platform = "未命名平台"
        key = _briefing_ts_key(ts)
        sig = (platform, doing, progress, artifact, key)
        if sig in seen_points:  # 重复状态点只计一次
            continue
        seen_points.add(sig)
        if platform == "未命名平台":
            gaps.append(f"status_points[{idx}].platform 缺失，无法归入平台投影")
        if not progress:
            gaps.append(
                f"status_points[{idx}].progress 缺失（平台 {platform}，停滞评估受限）")
        cur = latest_by_platform.get(platform)
        if cur is None or key >= cur[0]:
            latest_by_platform[platform] = (key, {
                "platform": platform, "doing": doing,
                "progress": progress, "artifact": artifact,
            })
        points_by_platform.setdefault(platform, []).append(
            {"progress": progress, "ts": ts})

    platforms = [v[1] for v in latest_by_platform.values()]

    # ---- attention 规则一：progress 停滞 ----
    if BRIEFING_ATTENTION_RULES.get("progress_stagnant"):
        for platform, pts in points_by_platform.items():
            if len(pts) < STAGNATION_MIN_SAMPLES:
                continue
            progresses = {p["progress"] for p in pts}
            if len(progresses) != 1 or not next(iter(progresses)):
                continue
            num_ts = [p["ts"] for p in pts
                      if isinstance(p["ts"], (int, float))
                      and not isinstance(p["ts"], bool)]
            if len(num_ts) == len(pts):
                span = max(num_ts) - min(num_ts)
                if span < STAGNATION_MIN_SPAN_S:
                    continue
                span_note = f"跨度 {span:.0f}s"
            else:
                span_note = "跨度未量化（ts 为字符串，透传比对）"
            attention.append(
                f"平台 {platform} progress 停滞："
                f"连续 {len(pts)} 个状态点无变化（{span_note}）")

    # ---- issues 计数与 attention 规则二：问题单未闭环 ----
    issues_open = 0
    issues_resolved = 0
    open_subjects: List[str] = []
    for idx, issue in enumerate(issues or []):
        raw_status = _briefing_field(issue, "status")
        status = str(raw_status or "").strip().lower()
        if status == ISSUE_OPEN_STATUS:
            issues_open += 1
            subject = str(_briefing_field(issue, "subject") or "").strip()
            if subject:
                open_subjects.append(subject)
        elif status in ISSUE_RESOLVED_STATUSES:
            issues_resolved += 1
        else:
            gaps.append(f"issues[{idx}].status 缺失或未知: {raw_status!r}")
    if BRIEFING_ATTENTION_RULES.get("issue_open"):
        for subject in dict.fromkeys(open_subjects):
            attention.append(f"问题单未闭环：{subject}")

    # ---- tasks 计数与 attention 规则三：任务 blocked ----
    tasks_by_state: Dict[str, int] = {}
    for idx, task in enumerate(tasks or []):
        state = _state_value(_briefing_field(task, "state"))
        state = str(state).strip() if state is not None else ""
        if not state:
            gaps.append(f"tasks[{idx}].state 缺失")
            continue
        tasks_by_state[state] = tasks_by_state.get(state, 0) + 1
    blocked_n = tasks_by_state.get(TaskState.BLOCKED.value, 0)
    if BRIEFING_ATTENTION_RULES.get("task_blocked") and blocked_n:
        attention.append(f"{blocked_n} 个任务处于 blocked")

    return {
        "goal": goal,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "platforms": platforms,
        "issues_open": issues_open,
        "issues_resolved": issues_resolved,
        "tasks_by_state": tasks_by_state,
        "attention": list(dict.fromkeys(attention)),
        "data_gaps": list(dict.fromkeys(gaps)),
    }
