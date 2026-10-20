"""中台·执行席（赵云）骨架——**常任管理席**（督办与交付核验）。

常任管理席定位（老田 2026-10-20 校准）：本席是数字军团常任管理席，充当管理角色、做
正常管理；**监督整改只是管理职能的一个分支**，不是本席职责全部。

老田 2026-10-20 校准：赵云负责监督督促各业务板块的执行与落实，
督办催办、核验交付、收集回执；巡检中发现问题打成问题单，
反馈田丰归口，由田丰安排研发团队补齐——本席不直接派研发、**永不直发本人**。

分工配置位（待老田裁定）：与统筹席（诸葛亮）的监督分工尚未定稿，
构造参数 division_config 仅作占位登记，不承载任何具体分工逻辑。
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

from roles.base_agent import BaseAgent, Report, TaskState

# ---- 督办节奏规则占位，真实基线后标定（工位W 表驱动常量） ----
# SUPERVISION_WINDOW_S：督办窗口（秒）——任务自最近一次有效督办起超过
# 该时长视为超窗。占位值 3600，待真实督办基线数据标定后替换。
SUPERVISION_WINDOW_S = 3600
# ESCALATION_AFTER_N：升级窗口——同一任务连续 N 轮督办仍超窗，则生成
# 升级条目与问题单草稿。占位值 2，待真实基线标定后替换。
ESCALATION_AFTER_N = 2


class ZhaoYun(BaseAgent):
    def __init__(self, division_config: Optional[Dict[str, Any]] = None,
                 **kw: Any) -> None:
        super().__init__(name="zhao_yun", role="command:manager",
                         level="command", **kw)
        self.supervision_log: Dict[str, list] = {}
        # 分工配置位（待老田裁定）：仅登记，不据此产生行为。
        self.division_config = division_config

    # ---- 督办：盯任务单进度 ----

    def supervise(self, task_id: str) -> Dict[str, Any]:
        """督办一张任务单：登记一条督办记录（动作=catalog，即盘点催办）。

        TODO(领域实现): 催办节奏与升级窗口（超时未动→提示诸葛亮、
        连续逾期→升级本人）；督办记录落台账，不替代平台自报。
        """
        record = {
            "task_id": task_id,
            "action": "catalog",
            "ts": time.time(),
            "by": self.name,
        }
        self.supervision_log.setdefault(task_id, []).append(record)
        return record

    # ---- 交付核验：回执收集 ----

    def verify_delivery(self, task_id: str,
                        delivery: Dict[str, Any]) -> Dict[str, Any]:
        """核验交付物并出回执。

        当前仅做结构校验：交付物缺 evidence 字段则不通过，并返回
        问题单草稿 dict（source=zhao_yun，供反馈田丰归口）。
        TODO(领域实现): 核验口径（对齐验收标准、证据链校验）；
        不通过时退回平台，不由本席放行。
        """
        if "evidence" not in delivery:
            issue_draft = {
                "source": self.name,
                "type": "delivery_rejected",
                "task_id": task_id,
                "reason": "交付物缺 evidence 字段，结构校验不通过",
                "delivery": dict(delivery),
                "ts": time.time(),
            }
            return {"passed": False, "issue_draft": issue_draft}
        # TODO(领域实现): 核验口径——对照验收标准核证据链，当前仅结构校验放行。
        return {"passed": True, "task_id": task_id,
                "note": "结构校验通过（核验口径待实现）"}

    # ---- 问题反馈：只反馈，不派研发 ----

    def report_issue(self, subject: str, evidence: Dict[str, Any]) -> Dict[str, Any]:
        """把巡检发现的问题构造成结构化问题单（含 evidence）反馈田丰归口。

        约束：问题单必须附证据；涉及签准/交易/生产变更的例外类别问题，
        走 approval_gate 待签 + risk_guard 预警双动作的例外通道（不经整改线）；
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

    # ---- 督办节奏：超窗判定与升级窗口（表驱动，占位待标定） ----

    def check_overdue(self, now: Optional[float] = None) -> Dict[str, Any]:
        """巡检督办台账（supervision_log），判定超窗任务并产出升级条目。

        督办节奏规则占位，真实基线后标定。判定口径：
        - 超窗：``now - 最近一条有效督办记录 ts > SUPERVISION_WINDOW_S``；
        - 升级：同一任务连续 ESCALATION_AFTER_N 轮督办仍超窗，则生成
          升级条目（task_id / 超窗时长 / 督办次数 / 连续超窗轮数）并附
          问题单草稿——结构对齐 report_issue 产出（source/subject/
          evidence/ts），供田丰 intake_issue 消费；
        - 轮次口径：督办台账发生变化（同一任务新增督办记录）即视为
          新一轮；台账未变化时重复调用不重复计数、不重复升级（幂等，
          同任务同轮不重复升级）；任务回到窗口内则连续计数清零；
        - 不改写 supervision_log 既有记录；不发总线、不落盘，升级
          动作（投递 intake_issue / 上报）由调用方决定；
        - 时间字段缺失或格式异常的记录如实进 data_gaps，不造数；
          无任何有效时间戳的任务跳过超窗判定。

        Args:
            now: 可注入的当前时间戳（测试可控时钟）；None 取当前时间。

        Returns:
            {"checked": 巡检任务数, "overdue": [超窗条目],
             "escalated": [升级条目(含 issue_draft)], "data_gaps": [缺数说明]}
        """
        if now is None:
            now = time.time()
        # 轮次状态为私有登记（懒初始化，不改 __init__ / 既有记录）：
        # task_id -> {"key": (记录数, 最新有效ts), "consecutive": 连续超窗轮数,
        #             "escalated_key": 已升级过的轮次标识}
        round_state: Dict[str, Dict[str, Any]] = self.__dict__.setdefault(
            "_cadence_round_state", {})
        checked = 0
        overdue: list = []
        escalated: list = []
        data_gaps: list = []
        for task_id, records in self.supervision_log.items():
            checked += 1
            valid_ts: list = []
            bad = 0
            for r in records:
                ts = r.get("ts") if isinstance(r, dict) else None
                if isinstance(ts, (int, float)) and not isinstance(ts, bool):
                    valid_ts.append(float(ts))
                else:
                    bad += 1
            if bad:
                data_gaps.append({"task_id": task_id,
                                  "gap": "督办记录 ts 缺失或格式异常",
                                  "bad_records": bad})
            if not valid_ts:
                data_gaps.append({"task_id": task_id,
                                  "gap": "无有效督办时间戳，跳过超窗判定",
                                  "bad_records": bad})
                continue
            ref_ts = valid_ts[-1]  # 最近一次有效督办时间
            key = (len(records), ref_ts)
            state = round_state.setdefault(task_id, {
                "key": None, "consecutive": 0, "escalated_key": None})
            elapsed = now - ref_ts
            if elapsed > SUPERVISION_WINDOW_S:
                overdue.append({"task_id": task_id,
                                "overdue_seconds": elapsed,
                                "supervise_count": len(records)})
                if state["key"] != key:  # 新一轮（台账有新增督办记录）
                    state["consecutive"] += 1
                    state["key"] = key
                if (state["consecutive"] >= ESCALATION_AFTER_N
                        and state["escalated_key"] != key):
                    issue_draft = {
                        "source": self.name,
                        "subject": (f"[督办升级] 任务 {task_id} 连续 "
                                    f"{state['consecutive']} 轮督办超窗"),
                        "evidence": {
                            "task_id": task_id,
                            "overdue_seconds": elapsed,
                            "supervise_count": len(records),
                            "consecutive_overdue": state["consecutive"],
                            "window_s": SUPERVISION_WINDOW_S,
                        },
                        "ts": now,
                    }
                    escalated.append({
                        "task_id": task_id,
                        "overdue_seconds": elapsed,
                        "supervise_count": len(records),
                        "consecutive_overdue": state["consecutive"],
                        "issue_draft": issue_draft,
                    })
                    state["escalated_key"] = key
            elif state["key"] != key:  # 回到窗口内：连续计数清零
                state["consecutive"] = 0
                state["key"] = key
        return {"checked": checked, "overdue": overdue,
                "escalated": escalated, "data_gaps": data_gaps}
