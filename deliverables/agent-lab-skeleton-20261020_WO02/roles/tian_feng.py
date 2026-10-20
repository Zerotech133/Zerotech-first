"""中台·文枢席（田丰）骨架——问题归口、分级与整改派单。

老田 2026-10-20 校准的监督整改线：诸葛亮、赵云监督督促各业务板块，
发现问题反馈田丰；田丰统一归口（登记、去重、分级），安排研发团队
实施补齐。监督者不直接派研发；整改一律走研发工单（无单不写码、
过评审门合并）。

老田 2026-10-20 裁定（方案B·白名单扩员）：田丰进总线 principal 白名单，
仅**阻断类**可 level=principal 直发本人（带消息类别，可按类型审计）；
非阻断类的升级/请示改走 command 层（经归集层 os_commander 归集呈报，
不直发本人）。阻断类直发本人分两类：整改工单类（已派工单、工单受阻，
请本人放行）与升级请示类（归口无法收敛，请本人裁决）——两类在总线账本
按 message_class 可区分可审计。

签准/交易/生产变更类问题不在本席整改范围，不经整改线，须由监督席转
approval_gate 待签 + risk_guard 预警双动作，由本人闭环。

TODO(领域实现): 分级细则（阈值/佐证规则）与送达接线——当前分级为
「来源声明 + 佐证非空」的最小实现，佐证缺失则降级为升级请示（不直发本人）。
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any, Dict, List, Optional

from roles.base_agent import BaseAgent, Report, TaskState
from roles.rectification_line import (
    PREFIX_ESCALATION_REQUEST,
    PREFIX_REMEDIATION_TICKET,
    PREFIX_TRIAGE_NOTICE,
    SEVERITY_BLOCKING,
    SEVERITY_ESCALATION,
    SEVERITY_NORMAL,
    normalize_severity,
)
from roles.zhuge_liang import LEGAL_TRANSITIONS  # 只读 import，不改其文件

# 问题来源限定为监督席；平台自报"无需整改"不构成问题关闭依据。
ISSUE_SOURCES = ("zhuge_liang", "zhao_yun", "risk_guard")

# 本席直发本人的两类消息类别（与总线登记一致）：
# - remediation_ticket：整改工单类——阻断项已派研发工单且工单受阻，请本人放行；
# - escalation_request：升级请示类——阻断项归口无法收敛，请本人裁决。
PRINCIPAL_CLASS_REMEDIATION = "remediation_ticket"
PRINCIPAL_CLASS_ESCALATION = "escalation_request"
PRINCIPAL_CLASSES = (PRINCIPAL_CLASS_REMEDIATION, PRINCIPAL_CLASS_ESCALATION)


class MeetingError(ValueError):
    """会商纪律类错误（伪造来源、越权归口等），属 ValueError 语义。"""


def issue_fingerprint(issue: Dict[str, Any]) -> str:
    """问题内容规范化哈希：同一平台同类问题视为重复。

    TODO(可替换策略): 归一化降噪（同义词合并、证据指纹加权）待定稿。
    """
    canonical = json.dumps(
        {
            "platform": str(issue.get("platform", "")).strip(),
            "subject": str(issue.get("subject", "")).strip(),
        },
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class TianFeng(BaseAgent):
    def __init__(self, dev_team_channel: str = "r_and_d", **kw: Any) -> None:
        super().__init__(name="tian_feng", role="command:intake",
                         level="command", **kw)
        self.dev_team_channel = dev_team_channel
        self.issue_board: List[Dict[str, Any]] = []          # 内存问题台账
        self._fingerprint_index: Dict[str, str] = {}         # 哈希 → issue_id
        self.dev_tasks: Dict[str, Dict[str, Any]] = {}       # 研发工单登记
        self._issue_seq = 0

    # ---- 输入：问题单（来自诸葛/赵云/守门预警） ----

    def intake_issue(self, issue: Dict[str, Any]) -> Dict[str, Any]:
        """登记问题单：来源校验 → 内容哈希去重 → 分级 → 落台账。

        来源不在 ISSUE_SOURCES 内视为伪造，抛 MeetingError（ValueError）。
        severity 为来源声明的分级线索（一般整改/升级请示/阻断），归口席
        据此复核后才作数（见 grade_issue）——未经归口不得分级、不得直发本人。
        TODO(领域实现): 分级细则（阈值/佐证规则）与改判入口；问题单须附
        证据（事件、指标、材料指纹），不接受口头描述（当前以证据非空
        作为最低佐证要求）。
        """
        source = issue.get("source")
        if source not in ISSUE_SOURCES:
            raise MeetingError(
                f"伪造问题单来源被拒: {source!r}（合法来源: {ISSUE_SOURCES}）")

        fp = issue_fingerprint(issue)
        if fp in self._fingerprint_index:
            existing_id = self._fingerprint_index[fp]
            for item in self.issue_board:
                if item["issue_id"] == existing_id:
                    return dict(item, duplicate=True)

        self._issue_seq += 1
        record = {
            "issue_id": f"ISS-{self._issue_seq:04d}",
            "source": source,
            "platform": issue.get("platform"),
            "subject": issue.get("subject"),
            # 分级线索：来源声明的严重度；归口复核见 grade_issue。
            "severity": normalize_severity(issue.get("severity")),
            "evidence": issue.get("evidence"),
            "fingerprint": fp,
            "status": "open",
            "ts": time.time(),
        }
        self.issue_board.append(record)
        self._fingerprint_index[fp] = record["issue_id"]
        return dict(record, duplicate=False)

    # ---- 归口复核分级：分级权在本席（未经归口不得分级） ----

    def grade_issue(self, issue_id: str) -> Dict[str, Any]:
        """归口复核分级：一般整改 / 升级请示 / 阻断。

        分级口径（TODO(领域实现) 细则待定稿）：以归口登记时随单声明的
        severity 为线索；「阻断」须有佐证（证据非空）方成立，缺佐证则
        降级为升级请示——宁可走 command 层，也不直发本人。
        """
        issue = self._find_issue(issue_id)
        declared = normalize_severity(issue.get("severity"))
        evidence = issue.get("evidence") or {}
        has_evidence = bool(evidence)
        if declared == SEVERITY_BLOCKING and not has_evidence:
            effective = SEVERITY_ESCALATION
            reason = "阻断声明缺佐证，降级为升级请示（不直发本人）"
        else:
            effective = declared
            reason = ""
        graded = {
            "issue_id": issue_id,
            "declared": declared,
            "effective": effective,
            "reason": reason,
            "has_evidence": has_evidence,
            "ts": time.time(),
        }
        issue["severity"] = effective
        issue["grade"] = graded
        return graded

    # ---- 归口后分级路径：唯一允许本席直发本人的出口 ----

    def build_escalation_report(self, issue_id: str,
                                note: str = "") -> Dict[str, Any]:
        """构造归口后分级路径的上报消息（构造与送达分离，便于审计与离线验证）。

        路由（老田 2026-10-20 裁定·方案B 白名单扩员）：
        - 复核为**阻断类** → level=principal 直发本人，并带消息类别：
          已派研发工单且工单受阻者 = 整改工单类（请本人放行）；
          归口无法收敛者 = 升级请示类（请本人裁决）。两类在总线账本按
          message_class 可区分可审计。
        - 其余（一般整改 / 升级请示）→ level=command 走归集层
          （os_commander 归集呈报），本席不直发本人。

        本方法只构造不发送（不发网络、不写盘）；发送见 escalate_issue。
        """
        issue = self._find_issue(issue_id)
        graded = self.grade_issue(issue_id)
        subject = str(issue.get("subject") or "")
        has_dev_task = any(order.get("issue_ref") == issue_id
                           for order in self.dev_tasks.values())
        if graded["effective"] == SEVERITY_BLOCKING:
            if has_dev_task:
                message_class = PRINCIPAL_CLASS_REMEDIATION
                prefix = PREFIX_REMEDIATION_TICKET
            else:
                message_class = PRINCIPAL_CLASS_ESCALATION
                prefix = PREFIX_ESCALATION_REQUEST
            report = Report(level="principal", source=self.name,
                            subject=f"{prefix} {subject}",
                            body=self._escalation_body(issue, graded, note))
        else:
            message_class = ""
            report = Report(level="command", source=self.name,
                            subject=f"{PREFIX_TRIAGE_NOTICE} {subject}",
                            body=self._escalation_body(issue, graded, note))
        return {
            "issue_id": issue_id,
            "route": "principal" if report.level == "principal" else "command",
            "level": report.level,
            "message_class": message_class,
            "grade": graded,
            "report": report,
        }

    def escalate_issue(self, issue_id: str, note: str = "") -> Dict[str, Any]:
        """归口后分级路径的送达入口：构造上报消息并经总线送出。

        构造与送达分离：构造见 build_escalation_report（纯函数、可离线审计），
        送达走 BaseAgent.report_up（真实总线投递，失败即闭，closed on failure）。
        """
        built = self.build_escalation_report(issue_id, note=note)
        result = self.report_up(built["report"])
        built["sent"] = result
        return built

    def _escalation_body(self, issue: Dict[str, Any], graded: Dict[str, Any],
                         note: str) -> str:
        """分级上报正文：留可复核的最小事实（分级依据、证据计数、备注）。"""
        evidence = issue.get("evidence") or {}
        lines = [
            f"问题单: {issue.get('issue_id')}",
            f"分级: {graded.get('declared')} → {graded.get('effective')}",
            f"证据条目: {len(evidence) if hasattr(evidence, '__len__') else 0}",
        ]
        if graded.get("reason"):
            lines.append(f"分级说明: {graded['reason']}")
        if note:
            lines.append(f"备注: {note}")
        return "\n".join(lines)

    def _find_issue(self, issue_id: str) -> Dict[str, Any]:
        issue = next((i for i in self.issue_board if i["issue_id"] == issue_id),
                     None)
        if issue is None:
            raise ValueError(f"未知问题单: {issue_id}")
        return issue

    # ---- 整改派单：安排研发团队实施补齐 ----

    def dispatch_dev_task(self, issue_id: str,
                          acceptance: Dict[str, Any]) -> Dict[str, Any]:
        """把问题转为研发整改工单并登记（state=created）。

        验收归口本席：整改完成须经本席核验收口，不接受研发自报完成。
        TODO(领域实现): 无单不写码、过评审门合并——先登记 tasks（created）
        再派发；合并前过既有评审门（_mixin_integrity / check_ports / 测试）；
        工单要素补齐（目标平台/仓库、回归要求）。
        """
        issue = next((i for i in self.issue_board if i["issue_id"] == issue_id),
                     None)
        if issue is None:
            raise ValueError(f"未知问题单: {issue_id}")
        self._issue_seq += 1
        task_id = f"DEV-{issue_id}-{self._issue_seq:04d}"
        order = {
            "task_id": task_id,
            "issue_ref": issue_id,
            "channel": self.dev_team_channel,
            "acceptance": acceptance,
            "state": TaskState.CREATED.value,
            "history": [],
            "created_ts": time.time(),
        }
        self.dev_tasks[task_id] = order
        return order

    # ---- 工单生命周期归口（S3 前置 工位I 追加） ----

    def transition_dev_task(self, task_id: str, to_state: Any,
                            *, by: str = "tian_feng") -> Dict[str, Any]:
        """研发工单七态流转归口：校验合法性并留痕，非法迁移抛 ValueError。

        - 迁移合法性以 roles/zhuge_liang.LEGAL_TRANSITIONS 为唯一口径
          （只读 import）；未知工单、未知任务态、非法迁移（含跨级、
          终态后再迁移）一律抛 ValueError，工单状态不变——与
          ZhugeLiang.transition 既有异常风格一致（closed on failure）。
        - 非田丰本席操作（by != "tian_feng"）拒绝：工单 history 记录
          拒绝留痕（rejected=True，不改状态）后抛 ValueError。
        - 每次合法迁移追加工单 history（ts/from/to/by），与七态既有
          语义同构。
        - 挂账（待老田裁定）：工单状态是否经总线上行留痕——本 API
          不发总线消息，仅在本地工单台账留痕。
        """
        order = self.dev_tasks.get(task_id)
        if order is None:
            raise ValueError(f"未知研发工单: {task_id}")
        if isinstance(to_state, str):
            try:
                to_state = TaskState(to_state)
            except ValueError:
                raise ValueError(f"未知任务态: {to_state}") from None
        if by != "tian_feng":
            # 非田丰本席操作：拒绝并留痕，不改工单状态。
            order.setdefault("history", []).append({
                "from": order.get("state", TaskState.CREATED.value),
                "to": to_state.value, "by": by,
                "rejected": True, "ts": time.time(),
            })
            raise ValueError(
                f"非田丰本席操作被拒: by={by!r} (task={task_id})")
        from_state = TaskState(order["state"])
        if to_state not in LEGAL_TRANSITIONS[from_state]:
            raise ValueError(
                f"非法迁移: {from_state.value} → {to_state.value} (task={task_id})")
        order["state"] = to_state.value
        order.setdefault("history", []).append({
            "from": from_state.value, "to": to_state.value,
            "by": by, "ts": time.time(),
        })
        return order

    # ---- 日常文书职能（保留） ----

    def daily_brief(self) -> Report:
        """每日全局简报：业务进展 + 问题/整改台账摘要。

        TODO(领域实现): 简报字段契约；缺失数据显式标注，不造数。
        """
        raise NotImplementedError

    # ---- 查询（只读） ----

    def issues(self, since: Optional[float] = None) -> List[Dict[str, Any]]:
        """问题台账只读查询。TODO(领域实现): 台账持久化与审计导出。"""
        if since is None:
            return list(self.issue_board)
        return [i for i in self.issue_board if i["ts"] >= since]


# =====================================================================
# 快速结论简报 v0（S3 工位N 追加）：quick_brief —— 情报快线最小实现。
#
# 定位：「交易侧（平台日报）+ 新闻侧（状态点）情报汇集 → 快速结论」
# 的骨架最小实现；真实多源情报接入挂 S3 后续（TODO 见函数 docstring）。
#
# v0 契约：输出字段结构为 v0 版本；任何字段增删必须版本化（升 v1 并
# 同步调用方），不得在 v0 上静默变更结构。
# 纯函数约束：无 IO、无网络、单遍 O(n)；缺失输入如实进 data_gaps，
# 不造数；快为第一设计约束。
# =====================================================================

from datetime import datetime, timezone

QUICK_BRIEF_CONTRACT_VERSION = "v0"

# headline 规则常量（表驱动，模块级暴露可配置；改动只影响 headline 选路，
# 不影响 conclusions/attention/data_gaps 的如实拼装）。
QUICK_BRIEF_RULES: Dict[str, Any] = {
    # True：attention 非空时 headline 风险优先；False：回落 progress 最高规则。
    "attention_first": True,
    # 全空（无任何有效平台情报）时的 headline，如实不造数。
    "empty_headline": "暂无可用情报",
    # 风险优先模板（{count}=留意项数，{first}=首项）。
    "risk_headline": "风险优先：需本人留意 {count} 项，首项——{first}",
    # progress 最高平台模板（{platform}/{progress}）。
    "progress_headline": "进展最快平台 {platform}（progress {progress}）",
    # 无任何数值 progress 可比较时的 headline，如实不排序。
    "no_numeric_headline": "各平台 progress 均缺失或非数值，进展无法排序（如实标注）",
}


def _qb_field(obj: Any, key: str) -> Any:
    """兼容 dict 与属性载体（StatusPoint/对象）两种取字段方式。"""
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _qb_ts_key(ts: Any) -> tuple:
    """ts 排序键：数值与字符串各自成序，混排时保持确定顺序。

    字符串 ts 直接透传比对（字典序，ISO8601 适用），不做日期解析。
    """
    if isinstance(ts, bool):
        return (1, str(ts))
    if isinstance(ts, (int, float)):
        return (0, float(ts))
    return (1, str(ts))


def _qb_progress_num(progress: Any) -> Optional[float]:
    """progress 数值化：仅 int/float 参与大小比较（bool 除外）。

    字符串 progress（如 "70%"）不参与大小比较、如实标注，不猜量纲。
    """
    if isinstance(progress, bool):
        return None
    if isinstance(progress, (int, float)):
        return float(progress)
    return None


def quick_brief(platform_reports: Any, status_points: Any = None) -> Dict[str, Any]:
    """田丰快速结论简报 v0（情报快线）：交易侧+新闻侧情报 → 一句话结论。

    纯函数：无 IO、无网络、单遍 O(n)；快为第一设计约束。
    「交易侧+新闻侧情报汇集→快速结论」骨架最小实现——真实多源情报
    （平台日报聚合、新闻舆情源、多源去重与置信度）挂 S3 后续接入，
    当前仅做单遍拼装，不造数。

    输入：
    - platform_reports：平台日报条目列表（dict 或带属性对象），字段
      platform/doing/progress/attention/blocked/ts，均可缺失（如实处理）；
    - status_points：可选，状态点列表（字段同 StatusPoint），与日报
      同口径按平台合并（同平台 ts 最新者胜）。

    输出 v0 契约（字段增删须版本化，升 v1 并同步调用方）::

        {
          "generated_at": iso8601,
          "headline": 一句话（规则表驱动）,
          "conclusions": [每平台一句话结论],
          "attention": [需本人留意项],
          "data_gaps": [缺失字段如实列出],
        }

    headline 规则（QUICK_BRIEF_RULES 表驱动可配）：
    1. attention 非空且 attention_first → 风险优先模板；
    2. 否则取数值 progress 最高的平台（仅 int/float 参与，字符串不比）；
    3. 无数值 progress 可比 → 如实「进展无法排序」；
    4. 全空 → "暂无可用情报"。

    每平台结论一句话模板拼装（platform+doing+progress），缺失字段
    如实进 data_gaps 并在结论中如实标注，不造数。重复平台取 ts 最新
    （ts 数值/字符串透传比对，混排保持确定顺序）。
    """
    gaps: List[str] = []
    attention: List[str] = []
    latest: Dict[str, tuple] = {}   # platform -> (ts_key, view)
    order: List[str] = []           # 平台首次出现顺序，保证输出确定

    def _feed(idx_label: str, rep: Any) -> None:
        platform = str(_qb_field(rep, "platform") or "").strip()
        if not platform:
            platform = "未命名平台"
            gaps.append(f"{idx_label}.platform 缺失")
        doing = _qb_field(rep, "doing")
        doing_str = str(doing).strip() if doing is not None else ""
        if not doing_str:
            gaps.append(f"{idx_label}.doing 缺失（平台 {platform}）")
            doing_str = "doing 缺失"
        progress = _qb_field(rep, "progress")
        progress_str = str(progress).strip() if progress is not None else ""
        if not progress_str:
            gaps.append(f"{idx_label}.progress 缺失（平台 {platform}）")
            progress_str = "缺失"
        att = _qb_field(rep, "attention")
        att_str = str(att).strip() if att is not None else ""
        if att_str:
            attention.append(f"平台 {platform} 需留意：{att_str}")
        if _qb_field(rep, "blocked"):
            attention.append(f"平台 {platform} 自报 blocked")
        key = _qb_ts_key(_qb_field(rep, "ts"))
        view = {
            "platform": platform,
            "doing": doing_str,
            "progress": progress_str,
            "progress_num": _qb_progress_num(progress),
        }
        cur = latest.get(platform)
        if cur is None:
            latest[platform] = (key, view)
            order.append(platform)
        elif key >= cur[0]:
            latest[platform] = (key, view)

    for idx, rep in enumerate(platform_reports or []):
        _feed(f"platform_reports[{idx}]", rep)
    for idx, sp in enumerate(status_points or []):
        _feed(f"status_points[{idx}]", sp)

    # ---- 每平台一句话结论 + 字符串 progress 如实标注（不参与排序） ----
    conclusions: List[str] = []
    for platform in order:
        view = latest[platform][1]
        conclusions.append(
            f"{view['platform']}：{view['doing']}；progress {view['progress']}")
        if view["progress_num"] is None and view["progress"] != "缺失":
            gaps.append(f"平台 {platform} progress 非数值，"
                        f"不参与进展排序：{view['progress']}")

    # ---- headline 规则表驱动 ----
    if attention and QUICK_BRIEF_RULES.get("attention_first", True):
        headline = QUICK_BRIEF_RULES["risk_headline"].format(
            count=len(attention), first=attention[0])
    else:
        best = None  # (num, platform, view)
        for platform in order:
            view = latest[platform][1]
            num = view["progress_num"]
            if num is not None and (best is None or num > best[0]):
                best = (num, platform, view)
        if best is not None:
            headline = QUICK_BRIEF_RULES["progress_headline"].format(
                platform=best[1], progress=best[2]["progress"])
        elif not latest:
            headline = QUICK_BRIEF_RULES["empty_headline"]
            gaps.append("无任何平台情报输入（platform_reports/status_points 均为空）")
        else:
            headline = QUICK_BRIEF_RULES["no_numeric_headline"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "headline": headline,
        "conclusions": conclusions,
        "attention": list(dict.fromkeys(attention)),
        "data_gaps": list(dict.fromkeys(gaps)),
    }
