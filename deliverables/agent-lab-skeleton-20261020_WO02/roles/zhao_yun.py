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

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional

from roles.base_agent import BaseAgent, Report

# ---- 督办节奏规则占位，真实基线后标定（工位W 表驱动常量） ----
# SUPERVISION_WINDOW_S：督办窗口（秒）——任务自最近一次有效督办起超过
# 该时长视为超窗。占位值 3600，待真实督办基线数据标定后替换。
SUPERVISION_WINDOW_S = 3600
# ESCALATION_AFTER_N：升级窗口——同一任务连续 N 轮督办仍超窗，则生成
# 升级条目与问题单草稿。占位值 2，待真实基线标定后替换。
ESCALATION_AFTER_N = 2


class ZhaoYun(BaseAgent):
    def __init__(self, division_config: Optional[Dict[str, Any]] = None,
                 state_path: Optional[Any] = None, **kw: Any) -> None:
        """构造执行席。

        Args:
            division_config: 分工配置位（待老田裁定），仅登记不产生行为。
            state_path: 督办轮次状态持久化文件路径（编排者 2026-10-20 授权
                的可选参）。None（默认）= 纯内存态，与既有调用方/测试零影响。
                注入时持久化格式（JSON，单对象）::

                    {
                      "version": 1,
                      "round_state": {
                        "<task_id>": {
                          "key": [督办记录数, 最新有效督办ts] | null,
                          "consecutive": 连续超窗轮数(int),
                          "escalated_key": [督办记录数, ts] | null
                        }
                      }
                    }

                写入为原子替换（临时文件 + os.replace），异常不损坏既有
                状态文件；加载时文件损坏/不可解析则重置计数，并在最近
                一次 check_overdue 的 data_gaps 中如实上报（fail-visible，
                不 crash）。``key``/``escalated_key`` 落盘为二元列表，
                加载还原为 ``(记录数, ts)`` 元组。
        """
        super().__init__(name="zhao_yun", role="command:manager",
                         level="command", **kw)
        self.supervision_log: Dict[str, list] = {}
        # 分工配置位（待老田裁定）：仅登记，不据此产生行为。
        self.division_config = division_config
        # 督办轮次状态（原 check_overdue 内懒初始化的私有登记，上移以便持久化）：
        # task_id -> {"key": (记录数, 最新有效ts), "consecutive": 连续超窗轮数,
        #             "escalated_key": 已升级过的轮次标识}
        self._cadence_round_state: Dict[str, Dict[str, Any]] = {}
        self.state_path: Optional[Path] = (
            Path(state_path) if state_path is not None else None)
        self._state_load_gap: Optional[str] = None
        if self.state_path is not None:
            self._load_cadence_state()

    # ---- 轮次状态持久化（state_path 注入时生效；None 纯内存） ----

    @staticmethod
    def _parse_round_key(value: Any) -> Optional[tuple]:
        """把落盘的轮次标识（[记录数, ts] 二元列表）还原为元组；null→None。"""
        if value is None:
            return None
        if (not isinstance(value, list) or len(value) != 2
                or isinstance(value[0], bool) or isinstance(value[1], bool)
                or not isinstance(value[0], int)
                or not isinstance(value[1], (int, float))):
            raise ValueError(f"轮次标识格式非法: {value!r}")
        return (int(value[0]), float(value[1]))

    def _load_cadence_state(self) -> None:
        """启动时从 state_path 加载轮次状态（进程重启计数延续）。

        文件不存在视为首次运行；不可读/损坏/不可解析时不 crash——重置
        计数并登记 _state_load_gap，由最近一次 check_overdue 如实进
        data_gaps（fail-visible）。
        """
        assert self.state_path is not None
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # 首次运行，无既有状态
        except OSError as exc:
            self._state_load_gap = (
                f"督办轮次状态文件不可读，已重置计数: {exc}")
            return
        try:
            data = json.loads(raw)
            if (not isinstance(data, dict)
                    or not isinstance(data.get("round_state"), dict)):
                raise ValueError("顶层结构须为 {'round_state': {task_id: ...}}")
            state: Dict[str, Dict[str, Any]] = {}
            for task_id, entry in data["round_state"].items():
                if not isinstance(entry, dict):
                    raise ValueError(f"轮次条目须为对象: task={task_id}")
                consecutive = entry.get("consecutive")
                if (not isinstance(consecutive, int)
                        or isinstance(consecutive, bool) or consecutive < 0):
                    raise ValueError(
                        f"consecutive 须为非负整数: task={task_id}")
                state[task_id] = {
                    "key": self._parse_round_key(entry.get("key")),
                    "consecutive": consecutive,
                    "escalated_key": self._parse_round_key(
                        entry.get("escalated_key")),
                }
        except (ValueError, TypeError, AttributeError) as exc:
            self._state_load_gap = (
                f"督办轮次状态文件损坏/不可解析，已重置计数: {exc}")
            return
        self._cadence_round_state = state

    def _save_cadence_state(self) -> None:
        """把轮次状态原子写回 state_path（临时文件 + os.replace）。

        写入异常（磁盘故障/序列化失败等）向上抛出，由 check_overdue
        捕获后如实进 data_gaps——既有状态文件不会被半写损坏。
        """
        assert self.state_path is not None
        payload = {
            "version": 1,
            "round_state": {
                task_id: {
                    "key": (list(st["key"])
                            if st.get("key") is not None else None),
                    "consecutive": st.get("consecutive", 0),
                    "escalated_key": (list(st["escalated_key"])
                                      if st.get("escalated_key") is not None
                                      else None),
                }
                for task_id, st in self._cadence_round_state.items()
            },
        }
        tmp_path = self.state_path.with_name(self.state_path.name + ".tmp")
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8")
        os.replace(tmp_path, self.state_path)

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
        - 不改写 supervision_log 既有记录；不发总线。state_path=None 时
          纯内存不落盘；state_path 注入时轮次状态在本次判定后原子写回
          落盘（格式见 __init__），启动时自动加载既有状态（进程重启
          连续超窗计数延续）；状态文件损坏/不可解析时重置计数并如实进
          data_gaps（fail-visible）。升级动作（投递 intake_issue /
          上报）仍由调用方决定；
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
        # 轮次状态：__init__ 已初始化（state_path 注入时启动即加载既有
        # 状态，进程重启计数延续）；此处不再 setdefault 懒初始化。
        round_state: Dict[str, Dict[str, Any]] = self._cadence_round_state
        checked = 0
        overdue: list = []
        escalated: list = []
        data_gaps: list = []
        # 启动时发现状态文件损坏/不可解析：计数已重置，如实进 data_gaps
        # （fail-visible，仅上报一次，写回后自愈为干净状态）。
        if self._state_load_gap is not None:
            data_gaps.append({"gap": self._state_load_gap,
                              "state_path": str(self.state_path)})
            self._state_load_gap = None
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
        # state_path 注入时：判定后原子写回轮次状态；写失败如实进
        # data_gaps（不 crash，既有状态文件不损坏——原子替换保证）。
        if self.state_path is not None:
            try:
                self._save_cadence_state()
            except (OSError, TypeError, ValueError) as exc:
                data_gaps.append({
                    "gap": f"督办轮次状态写回落盘失败，内存计数保留: {exc}",
                    "state_path": str(self.state_path)})
        return {"checked": checked, "overdue": overdue,
                "escalated": escalated, "data_gaps": data_gaps}
