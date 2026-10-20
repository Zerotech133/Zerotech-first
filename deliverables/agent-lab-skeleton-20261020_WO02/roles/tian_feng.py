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
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# 模块级 logger（WO-LEGION-S14-01 起用）：心跳对账的补投/失败须在
# 运维日志可见；结构性留痕仍走 data_gaps，两者职责不同——日志给人看，
# data_gaps 进状态契约可被测试断言。
LOG = logging.getLogger(__name__)

# file_lock 为既有跨进程边车锁原语，只读复用，不改其文件。
from roles.base_agent import BaseAgent, Report, TaskState, file_lock
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

# 升级幂等去重键口径（老田 2026-10-20 裁定：「同问题同分级不重发」）：
#   key = (issue_id, 复核后 effective 分级, message_class)
# message_class 计入键的理由：同为阻断类，工单类（已派单·请放行）与请示类
# （无法收敛·请裁决）是两种不同诉求，派单后出口类别发生变化属**新消息**，
# 不算重复投递；两类各自只发一次。分级因补佐证而升级（escalation→blocking）
# 亦属新消息，允许再发一次。
# 需要人工强制重推时走 allow_repeat=True，账本与问题单均留痕，不静默放行。
ESCALATION_DEDUP_RULE = "同问题同分级同出口只发一次；换出口或分级变化视为新消息"

# ---- 台账持久化契约（state_path 注入时生效；TODO(领域实现): 跨进程查询） ----
#
# 契约版本：格式一旦变更必须升版本号。加载时严格比对，不匹配即按
# 「损坏」处理（空台账重置 + data_gaps 留痕），不做跨版本猜测性迁移。
TIANFENG_STATE_CONTRACT_VERSION = "tian_feng_state/v1"

# 台账条目必填字段（「字段缺失」判定口径）。只校验存在性与类型，
# 不改写任何既有键语义：severity_declared 固化、grade_history 快照、
# escalations / escalation_skips 分账，均按原样存取。
_ISSUE_REQUIRED_FIELDS = (
    "issue_id", "fingerprint", "severity", "severity_declared",
    "grade_history", "escalations", "escalation_skips", "status", "ts",
)
_ISSUE_STR_FIELDS = ("issue_id", "fingerprint", "severity",
                     "severity_declared", "status")
_ISSUE_LIST_FIELDS = ("grade_history", "escalations", "escalation_skips")

# 同进程内状态写盘的串行锁（模块级，进程口径）。
# 理由：file_lock 的同进程重入登记表不区分线程，多线程并发进入同一
# 路径会响亮抛 RuntimeError；此锁先做进程内串行，跨进程互斥仍由
# file_lock 的平台锁保证。锁序固定为「实例锁 → 本锁 → file_lock」，
# 全模块只此一处入口，不构成环。
_STATE_WRITE_THREAD_LOCK = threading.Lock()

# ---- 升级送达预写日志（WAL）契约（WO-S12-01） ----
#
# 存在理由（治理口径）：本线是监督整改线，**漏报比重复报危险**。原时序
# 「送达成功 → 记账 → 落盘」在落盘失败时，内存去重账已生效（同进程不重发）
# 但磁盘没记，重启后这条已送达记录丢失、同一升级被再投一次——且这次重复
# 无法与「首次投递」区分。WAL 的目标不是消除重复，而是**让重复变成显式、
# 可审计、下游可判定**（重发带 ``redelivery_of`` 标记）。
#
# 契约要点：
# - 独立文件，**不并入主状态文件**：主状态保持 ``tian_feng_state/v1``
#   原样可读，避免升版迁移风险；WAL 路径由 state_path 派生
#   ``<state名>.wal.jsonl``（例：``tianfeng_state.json`` →
#   ``tianfeng_state.json.wal.jsonl``）。
# - 仅当 ``state_path is not None`` 时启用；为 ``None`` 时全部 WAL 分支
#   直接返回，零 IO、不创建任何文件，与既有纯内存行为完全等价。
# - 格式：JSONL，一行一条记录，``phase`` 区分 ``intent`` / ``committed``。
#   追加写（O_APPEND）+ flush + fsync 才算耐久落盘。
WAL_CONTRACT_VERSION = "tian_feng_wal/v1"
WAL_PHASE_INTENT = "intent"
WAL_PHASE_COMMITTED = "committed"
# 降级送达留痕记录的 phase（WO-LEGION-S15-01）：独立于 WAL 文件的第三条
# 持久化通道，专门承载「intent 没写进去但消息仍发出去了」的降级事件。
WAL_PHASE_DEGRADED = "degraded_send"

# 心跳对账的最小间隔（WO-LEGION-S14-01）。**占位值，待真实基线标定**——
# 与 STAGNATION_MIN_SPAN_S 等既有占位同口径：先给一个保守上限保证行为
# 正确（悬挂最终会被清），基线数据到位后再标定，不假装它是标定结果。
WAL_RECONCILE_MIN_INTERVAL_S = 600.0

# 重发标记：追加在 subject 尾部（**不动开头**——总线按 subject 前缀
# 解析 message_class，前缀必须保持在首位，见 message_bus.classify_message_class），
# 同时写入 body 便于人工审计。只用 Report 既有字段承载，不新增字段：
# report_up 以 ``report.__dict__`` 发总线，send_pending 以
# ``Report(**record["report"])`` 复原队列条目，多出的字段会让复原抛
# TypeError。故标记只能落在既有字符串字段里。
WAL_REDELIVERY_MARK = "[补投]"

# intent 记录必填字段（「字段缺失」判定口径）。缺任一字段即视为损坏：
# 无法唯一定位、无法重建消息、无法判定结局，一律 data_gaps 留痕。
_WAL_INTENT_REQUIRED = ("intent_id", "issue_id", "effective",
                        "message_class", "ts")

# WAL 写路径的进程内串行锁。锁序与状态写一致：
# 「实例锁 → WAL 写锁 → WAL file_lock」，且**永不**与状态写锁嵌套，
# 避免两条锁链交叉成环。
_WAL_WRITE_THREAD_LOCK = threading.Lock()



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
    def __init__(self, dev_team_channel: str = "r_and_d",
                 state_path: Optional[Path] = None, **kw: Any) -> None:
        """构造归口席。

        Args:
            dev_team_channel: 研发派单频道。
            state_path: 台账持久化文件路径（可选）。``None``（默认）= 纯
                内存态，零 IO、不创建任何文件，行为与未持久化版本等价
                （既有调用方 ``TianFeng(report_to=..., bus_url=...,
                metrics_port=...)`` 不受影响）。注入时：构造即加载既有
                状态（进程重启后台账与升级去重账延续），后续登记/分级
                改写/送达记账/派单均原子写回，格式见 ``_persist_state``。

                加载失败（文件不可读 / JSON 非法 / 字段缺失 / 契约版本
                不匹配）不 crash：重置为空台账，并在 ``self.data_gaps``
                留痕 ``state_corrupt: <原因>``（fail-visible，绝不静默
                当空台账）。
        """
        super().__init__(name="tian_feng", role="command:intake",
                         level="command", **kw)
        self.dev_team_channel = dev_team_channel
        self.issue_board: List[Dict[str, Any]] = []          # 内存问题台账
        self._fingerprint_index: Dict[str, str] = {}         # 哈希 → issue_id
        self.dev_tasks: Dict[str, Dict[str, Any]] = {}       # 研发工单登记
        self._issue_seq = 0
        # 升级投递账（幂等去重）：key 见 ESCALATION_DEDUP_RULE。
        self._escalation_index: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        # 持久化开关：None = 纯内存；非 None = 加载 + 每次变更原子写回。
        self.state_path: Optional[Path] = (
            Path(state_path) if state_path is not None else None)
        # 升级送达预写日志（独立文件，派生自 state_path；None 时不启用）。
        # 只在 state_path 注入时才存在：既保持零 IO 语义，也不动主状态契约。
        self.wal_path: Optional[Path] = (
            self.state_path.with_name(self.state_path.name + ".wal.jsonl")
            if self.state_path is not None else None)
        # WAL 内存视图：intent_id → intent 记录；intent_id → [committed 记录]
        self._wal_intents: Dict[str, Dict[str, Any]] = {}
        self._wal_committed: Dict[str, List[Dict[str, Any]]] = {}
        # 心跳对账节流（WO-LEGION-S14-01）：None=本进程从未对账，
        # 首个心跳立即对账一次（清上次进程遗留的未闭环 intent），
        # 之后按 WAL_RECONCILE_MIN_INTERVAL_S 节流。escalate_issue 入口的
        # 惰性对账不走这个节流——它有「先对账再落新单」的正确性理由。
        self._last_reconcile_ts: Optional[float] = None
        # 无法归属/损坏的 WAL 行原文：保留不丢（丢弃=未知结局被当成已处理），
        # 由 data_gaps 每次加载如实留痕，交由人工核账。
        self._wal_orphans: List[str] = []
        # 加载期如实留痕（不在加载时写盘，避免只读启动产生 IO）。
        self.data_gaps: List[str] = []
        # 实例内串行锁：保护「内存结构变更 + 随后落盘」这一段的一致性，
        # 可重入（_persist_state 内部仍可再取）。锁序见 _STATE_WRITE_THREAD_LOCK。
        self._state_lock = threading.RLock()
        if self.state_path is not None:
            self._load_state()
            # 对账分两步：构造期只读 WAL（不联网、不发消息），真正重发在
            # reconcile_wal()（由 escalate_issue 入口惰性触发或显式调用）。
            # 构造期不发网络的理由：调用方通常在构造后才接总线/注入桥接，
            # 构造期投递会打空靶并把「未接线的失败」记成「投递失败」。
            self._load_wal()
            # 降级账本加载（WO-LEGION-S15-01）：与 WAL 同期只读，同样
            # 不在构造期发网络——只把「上轮降级送达结局未知」的事实
            # 放进 data_gaps，处置（人工核账）交给消费方。
            self._load_degraded()

    # ---- 台账持久化（state_path 注入时生效；None 时全部方法直接返回） ----

    def _state_lock_path(self) -> Path:
        """状态文件边车锁：``<state_path>.lock``。

        不直锁状态文件本身——file_lock 以 open("a") 打开句柄，Windows
        强制字节区间锁会与临界区内 os.replace 的目标句柄冲突（与
        base_agent._pending_lock_path 同一取舍）。
        """
        assert self.state_path is not None
        return self.state_path.with_name(self.state_path.name + ".lock")

    @classmethod
    def _parse_state(cls, data: Any) -> Dict[str, Any]:
        """校验并还原落盘状态；任何结构/类型不符都抛 ValueError。

        还原口径（与 ``_persist_state`` 一一对应）：
        - ``issue_board``：台账条目原样还原（不增删键、不改语义）；
        - ``escalation_index``：``[{"key": [issue_id, effective,
          message_class], "value": {...}}]`` → ``{(issue_id, effective,
          message_class): {...}}``（三元列表还原为元组）；
        - ``issue_seq``：非负整数；低于台账内最大编号时上修，避免重启后
          新单号与既有单号相撞；
        - ``dev_tasks``：可选（缺失视为空）。不落盘会让「阻断类已派工单」
          在重启后退化为升级请示出口，去重键随之变化而重发——故随账落盘。
        """
        if not isinstance(data, dict):
            raise ValueError("顶层结构须为对象")
        version = data.get("contract_version")
        if version != TIANFENG_STATE_CONTRACT_VERSION:
            raise ValueError(
                f"contract_version 缺失或不匹配: {version!r}"
                f"（期望 {TIANFENG_STATE_CONTRACT_VERSION!r}）")
        missing = [k for k in ("issue_board", "escalation_index", "issue_seq")
                   if k not in data]
        if missing:
            raise ValueError(f"缺少字段: {missing}")

        raw_board = data["issue_board"]
        if not isinstance(raw_board, list):
            raise ValueError("issue_board 须为数组")
        board: List[Dict[str, Any]] = []
        seen_ids: set = set()
        for pos, item in enumerate(raw_board):
            if not isinstance(item, dict):
                raise ValueError(f"issue_board[{pos}] 须为对象")
            absent = [f for f in _ISSUE_REQUIRED_FIELDS if f not in item]
            if absent:
                raise ValueError(f"issue_board[{pos}] 缺少字段: {absent}")
            for field in _ISSUE_STR_FIELDS:
                if not isinstance(item[field], str):
                    raise ValueError(
                        f"issue_board[{pos}].{field} 须为字符串")
            for field in _ISSUE_LIST_FIELDS:
                if not isinstance(item[field], list):
                    raise ValueError(f"issue_board[{pos}].{field} 须为数组")
            ts = item["ts"]
            if isinstance(ts, bool) or not isinstance(ts, (int, float)):
                raise ValueError(f"issue_board[{pos}].ts 须为数值")
            issue_id = item["issue_id"]
            if not issue_id:
                raise ValueError(f"issue_board[{pos}].issue_id 为空")
            if issue_id in seen_ids:
                raise ValueError(f"issue_id 重复: {issue_id}")
            seen_ids.add(issue_id)
            board.append(dict(item))

        raw_index = data["escalation_index"]
        if not isinstance(raw_index, list):
            raise ValueError("escalation_index 须为数组")
        index: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for pos, entry in enumerate(raw_index):
            if not isinstance(entry, dict):
                raise ValueError(f"escalation_index[{pos}] 须为对象")
            key = entry.get("key")
            value = entry.get("value")
            if (not isinstance(key, list) or len(key) != 3
                    or not all(isinstance(part, str) for part in key)):
                raise ValueError(
                    f"escalation_index[{pos}].key 须为三元字符串数组: {key!r}")
            if not isinstance(value, dict):
                raise ValueError(
                    f"escalation_index[{pos}].value 须为对象")
            index[(key[0], key[1], key[2])] = dict(value)

        seq = data["issue_seq"]
        if isinstance(seq, bool) or not isinstance(seq, int) or seq < 0:
            raise ValueError(f"issue_seq 须为非负整数: {seq!r}")
        # 上修：台账内最大单号优先（防止手改/截断后新单号撞号）。
        for issue_id in seen_ids:
            _, _, tail = issue_id.partition("-")
            if tail.isdigit():
                seq = max(seq, int(tail))

        raw_dev = data.get("dev_tasks", {})
        if not isinstance(raw_dev, dict):
            raise ValueError("dev_tasks 须为对象")
        dev_tasks: Dict[str, Dict[str, Any]] = {}
        for task_id, order in raw_dev.items():
            if not isinstance(order, dict):
                raise ValueError(f"dev_tasks[{task_id}] 须为对象")
            dev_tasks[task_id] = dict(order)

        return {"issue_board": board, "escalation_index": index,
                "issue_seq": seq, "dev_tasks": dev_tasks}

    def _load_state(self) -> None:
        """启动加载：文件不存在视为首次运行；损坏则空台账 + data_gaps 留痕。

        读取进边车 file_lock（WO-LEGION-S16-01）：与写路径同一把
        ``<state>.lock``，避免加载到半程被其他进程 ``os.replace`` 的
        中间态（读取本身原子性靠 replace 保证，进锁是为了与其他进程
        的「读-合并-写」临界区互斥，防止读到合并前的旧账后立刻覆盖写）。
        """
        assert self.state_path is not None
        try:
            with file_lock(self._state_lock_path()):
                raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # 首次运行，无既有状态
        except OSError as exc:
            self.data_gaps.append(f"state_corrupt: 状态文件不可读已重置: {exc}")
            return
        try:
            parsed = self._parse_state(json.loads(raw))
        except (ValueError, TypeError, AttributeError) as exc:
            # 损坏/字段缺失/契约不符：一律空台账重置并留痕，不静默放行。
            self.data_gaps.append(f"state_corrupt: {exc}")
            return
        self.issue_board = parsed["issue_board"]
        self._escalation_index = parsed["escalation_index"]
        self._issue_seq = parsed["issue_seq"]
        self.dev_tasks = parsed["dev_tasks"]
        # 指纹索引由台账重建：重启后同内容问题单仍按既有指纹去重。
        self._fingerprint_index = {
            item["fingerprint"]: item["issue_id"] for item in self.issue_board}

    def _merge_disk_state_into_memory(self) -> None:
        """锁内重读盘上状态，按节做键级并集并入内存（WO-LEGION-S16-01）。

        闭环「后写者整文件覆盖前写者」的账本级丢单：本方法只在
        ``_persist_state`` 的 file_lock 临界区内调用，流程为
        「重读盘态 → 键级并集 → 冲突以内存为准 → 原子写 → 内存即合并后视图」。

        并集键口径（以代码实际结构为准，顶层节为 issue_board /
        escalation_index / dev_tasks / issue_seq）：
        - ``issue_board``：按 **fingerprint**（内容指纹，本模块自带的
          内容同一性原语）并集。不用 issue_id 的理由：单号由各进程本地
          ``issue_seq`` 起算，跨进程不保证唯一（两进程同起点都会派
          ISS-0001），按 id 并集会把「不同内容、同单号」判成冲突而丢账。
          盘上独有条目并入时若 issue_id 与内存相撞，则**重派新号**并入
          （冲突以内存为准；重派号同步用于 escalation_index 键修复）；
        - ``escalation_index``：按三元组键并集；冲突键以内存为准；
        - ``dev_tasks``：按 task_id 并集；冲突键以内存为准；
        - ``issue_seq``：取 max（含重派号后的新峰值），保证后续单号
          不与任何已并入条目相撞。

        盘态不可用（文件不存在/不可读/损坏）时不阻断写路径：跳过合并、
        以内存为准整份写回（损坏自愈语义与 ``_load_state`` 一致），打
        LOG.warning 留痕，不静默。

        可观测性：实际并入（issues/escalation_keys/dev_tasks 任一 >0）
        必打 ``LOG.warning``（合并发生次数可审计）；零并入不告警。

        零 IO 语义：仅 ``_persist_state``（state_path 非 None 分支）调用，
        ``state_path=None`` 时本方法不会被触达。
        """
        assert self.state_path is not None
        try:
            raw = self.state_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # 首次落盘，无盘态可并
        except OSError as exc:
            LOG.warning(
                "%s state_path 重读失败，跳过合并以内存为准写回: %s", self.name, exc)
            return
        try:
            disk = self._parse_state(json.loads(raw))
        except (ValueError, TypeError, AttributeError,
                json.JSONDecodeError) as exc:
            LOG.warning(
                "%s state_path 盘态损坏，跳过合并以内存为准写回（自愈）: %s",
                self.name, exc)
            return

        memory_fps = {i["fingerprint"] for i in self.issue_board}
        used_ids = {i["issue_id"] for i in self.issue_board}
        seq = max(self._issue_seq, disk["issue_seq"])
        rename: Dict[str, str] = {}
        merged_issues = 0
        for item in disk["issue_board"]:
            if item["fingerprint"] in memory_fps:
                continue  # 同内容问题：以内存为准（跨进程重复），不并入
            new_id = item["issue_id"]
            if new_id in used_ids:
                # 单号相撞（不同内容）：盘上独有条目重派新号并入，
                # 冲突以内存为准——内存是活权威。
                while True:
                    seq += 1
                    new_id = f"ISS-{seq:04d}"
                    if new_id not in used_ids:
                        break
                rename[item["issue_id"]] = new_id
            entry = dict(item)
            entry["issue_id"] = new_id
            self.issue_board.append(entry)
            memory_fps.add(entry["fingerprint"])
            used_ids.add(new_id)
            self._fingerprint_index[entry["fingerprint"]] = new_id
            merged_issues += 1

        merged_keys = 0
        for key, value in disk["escalation_index"].items():
            new_key = (rename.get(key[0], key[0]), key[1], key[2])
            if new_key in self._escalation_index:
                continue  # 冲突键以内存为准
            self._escalation_index[new_key] = dict(value)
            merged_keys += 1

        merged_tasks = 0
        for task_id, order in disk["dev_tasks"].items():
            if task_id in self.dev_tasks:
                continue  # 冲突键以内存为准
            self.dev_tasks[task_id] = dict(order)
            merged_tasks += 1

        # 单号峰值兜底上修：盘态 parse 已上修过一次，这里再对合并后的
        # 全量台账扫一遍尾号，保证后续派号不与任何在账条目相撞。
        for entry in self.issue_board:
            _, _, tail = entry["issue_id"].partition("-")
            if tail.isdigit():
                seq = max(seq, int(tail))
        self._issue_seq = max(self._issue_seq, seq)

        if merged_issues or merged_keys or merged_tasks:
            LOG.warning(
                "%s state_path 多进程写合并: 并入盘上独有条目 "
                "issues=%d escalation_keys=%d dev_tasks=%d"
                "（冲突键以内存为准；合并发生即留痕可观测）",
                self.name, merged_issues, merged_keys, merged_tasks)

    def _persist_state(self) -> None:
        """原子写回落盘（临时文件 + os.replace，边车 file_lock 防并发写坏）。

        多进程写合并（WO-LEGION-S16-01，闭合原 TODO 单写者假设缺口）：
        file_lock 临界区内先 ``_merge_disk_state_into_memory`` 重读盘态
        并按节键级并集（冲突以内存为准），再以**合并后的内存**整份序列化
        原子写回——后写者不再整文件覆盖前写者，前写者的增量保留。

        落盘字段：``contract_version`` / ``issue_seq`` / ``issue_board`` /
        ``escalation_index`` / ``dev_tasks``。``_escalation_index`` 的
        tuple 键编码为三元数组 ``[issue_id, effective, message_class]``，
        加载时 ``tuple(key)`` 还原（见 ``_parse_state``）。

        失败即闭（closed on failure）：序列化/写临时文件/replace 任一步
        异常都向上抛出，不吞；临时文件就地清理，既有状态文件因替换未
        发生而完好，内存结构保持调用前的合法状态（不半写、不回滚业务分支）。

        TODO(领域实现): 落盘失败后内存已推进、磁盘未更新，本实现只用异常
        上抛（closed on failure）让调用方感知。送达成功后落盘失败这一段的
        补偿已由 WO-S12-01 的预写日志闭合（见 ``_load_wal`` / ``reconcile_wal``）：
        送达前先写 intent（耐久），重启对账按「主状态去重账里有没有该键」
        判定闭环，缺失即带 ``redelivery_of`` 标记重发并记 ``redelivered=True``。
        未闭合的部分只剩「双失败」（主状态与 WAL 同时写不进去）：此时仍退化为
        本方法描述的必然后果，且 WAL 写失败会进 ``data_gaps``（wal_write_failed）。
        """
        if self.state_path is None:
            return
        tmp_path = self.state_path.with_name(self.state_path.name + ".tmp")
        with _STATE_WRITE_THREAD_LOCK:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(self._state_lock_path()):
                # 锁内重读盘态并集（后写者不覆盖前写者的关键一步），
                # 随后从合并后的内存重建 payload 整份写回。
                self._merge_disk_state_into_memory()
                payload = {
                    "contract_version": TIANFENG_STATE_CONTRACT_VERSION,
                    "issue_seq": self._issue_seq,
                    "issue_board": self.issue_board,
                    "escalation_index": [
                        {"key": [key[0], key[1], key[2]], "value": value}
                        for key, value in self._escalation_index.items()
                    ],
                    "dev_tasks": self.dev_tasks,
                }
                text = json.dumps(payload, ensure_ascii=False, indent=2)
                try:
                    # 耐久契约（WO-LEGION-S18-02）：tmp 必须 flush+fsync 后
                    # 再 os.replace——replace 的原子性只覆盖进程级崩溃，
                    # 断电窗口内 rename 可先于数据落盘，无 fsync 会留下
                    # 旧内容甚至空文件。失败即闭：清掉半成品后原样抛出。
                    with tmp_path.open("w", encoding="utf-8") as fh:
                        fh.write(text)
                        fh.flush()
                        os.fsync(fh.fileno())
                    os.replace(tmp_path, self.state_path)
                except BaseException:
                    # 失败即闭：清掉半成品临时文件后原样抛出。
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise

    # ---- 升级送达预写日志（WAL；仅 state_path 注入时启用） ----
    #
    # 时序（唯一正确方向，不可颠倒）：
    #   1) 写 intent（append + fsync，耐久）→ 2) 送达 report_up
    #   → 3) 去重账/问题单记账 → 4) 主状态原子落盘 → 5) 写 committed
    #   → 6) 截断已闭环记录（必须在 4 成功之后）。
    #
    # 禁止：送达前先落「已送达」（发送失败即漏报，方向错）；发现未 committed
    # 就静默重发（重复不可判定）。

    def _wal_lock_path(self) -> Path:
        """WAL 边车锁：``<wal>.lock``（与状态锁同一取舍，不直锁数据文件）。"""
        assert self.wal_path is not None
        return self.wal_path.with_name(self.wal_path.name + ".lock")

    def _wal_append(self, record: Dict[str, Any]) -> None:
        """追加一条 WAL 记录并耐久落盘（flush + fsync 后才返回）。

        追加写不做「读-改-写」，天然不会写坏既有行；跨进程互斥靠
        ``file_lock`` 边车锁，同进程多线程靠 ``_WAL_WRITE_THREAD_LOCK``
        （file_lock 不支持同进程重入，见 base_agent.file_lock）。

        失败即闭（closed on failure）：任何异常向上抛，不吞——调用方据此
        决定是放弃送达（不可接受，会漏报）还是降级送达 + data_gaps 留痕
        （本实现选择后者，见 ``_wal_write_intent`` 调用点）。
        """
        assert self.wal_path is not None
        # ensure_ascii=False 只为可读性；JSON 转义保证正文里的换行不会破坏
        # 一行一记录的 JSONL 契约。
        line = json.dumps(record, ensure_ascii=False)
        with _WAL_WRITE_THREAD_LOCK:
            self.wal_path.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(self._wal_lock_path()):
                with self.wal_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())

    def _load_wal(self) -> None:
        """启动加载 WAL（只读，不发网络）：解析 intent/committed 两类记录。

        损坏处理口径：**绝不静默当空**。非法 JSON、缺 ``intent_id``、缺
        必填字段、未知 phase 一律进 ``data_gaps``（``wal_corrupt: <原因>``），
        原始行保留在 ``_wal_orphans`` 里不丢——丢弃等于把「未知结局」当成
        「已处理」，就是漏报。孤儿 committed（找不到对应 intent）同样是
        数据缺口，按损坏留痕。
        """
        assert self.wal_path is not None
        try:
            raw = self.wal_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # 首次运行或从未升级送达，无 WAL
        except OSError as exc:
            self.data_gaps.append(f"wal_corrupt: 预写日志不可读: {exc}")
            return
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                self.data_gaps.append(
                    f"wal_corrupt: 第 {lineno} 行非法 JSON: {exc}")
                self._wal_orphans.append(line)
                continue
            if not isinstance(record, dict):
                self.data_gaps.append(
                    f"wal_corrupt: 第 {lineno} 行不是对象: {type(record).__name__}")
                self._wal_orphans.append(line)
                continue
            version = record.get("wal_version")
            if version is not None and version != WAL_CONTRACT_VERSION:
                self.data_gaps.append(
                    f"wal_corrupt: 第 {lineno} 行契约版本不匹配: {version!r}"
                    f"（期望 {WAL_CONTRACT_VERSION!r}）")
                self._wal_orphans.append(line)
                continue
            phase = record.get("phase")
            if phase == WAL_PHASE_INTENT:
                self._load_wal_intent(record, lineno, line)
            elif phase == WAL_PHASE_COMMITTED:
                self._load_wal_committed(record, lineno, line)
            else:
                self.data_gaps.append(
                    f"wal_corrupt: 第 {lineno} 行未知 phase: {phase!r}")
                self._wal_orphans.append(line)
        # 孤儿 committed：intent 行已损坏/被截断，无法配对 → 结局不可判定。
        for intent_id in self._wal_committed:
            if intent_id not in self._wal_intents:
                for record in self._wal_committed[intent_id]:
                    self._wal_orphans.append(
                        json.dumps(record, ensure_ascii=False))
                self.data_gaps.append(
                    f"wal_corrupt: committed 无对应 intent: {intent_id}"
                    f"（该条结局不可判定，已保留原文待人工核账）")
        for intent_id in [k for k in self._wal_committed
                          if k not in self._wal_intents]:
            self._wal_committed.pop(intent_id, None)

    def _load_wal_intent(self, record: Dict[str, Any], lineno: int,
                         line: str) -> None:
        """校验并登记一条 intent 记录；缺字段即损坏（留痕 + 保留原文）。"""
        intent_id = record.get("intent_id")
        if not isinstance(intent_id, str) or not intent_id:
            self.data_gaps.append(
                f"wal_corrupt: 第 {lineno} 行 intent 缺 intent_id")
            self._wal_orphans.append(line)
            return
        missing = [f for f in _WAL_INTENT_REQUIRED if f not in record]
        if missing:
            self.data_gaps.append(
                f"wal_corrupt: 第 {lineno} 行 intent({intent_id}) 缺字段: {missing}")
            self._wal_orphans.append(line)
            return
        ts = record.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)):
            self.data_gaps.append(
                f"wal_corrupt: 第 {lineno} 行 intent({intent_id}) ts 非数值: {ts!r}")
            self._wal_orphans.append(line)
            return
        if intent_id in self._wal_intents:
            # 同一 intent_id 重复：保留首条为准，后来的原文留痕不丢。
            self.data_gaps.append(
                f"wal_corrupt: 第 {lineno} 行 intent_id 重复: {intent_id}")
            self._wal_orphans.append(line)
            return
        self._wal_intents[intent_id] = dict(record)

    def _load_wal_committed(self, record: Dict[str, Any], lineno: int,
                            line: str) -> None:
        """校验并登记一条 committed 记录（必须含 intent_id 与布尔 ok）。"""
        intent_id = record.get("intent_id")
        ok = record.get("ok")
        if not isinstance(intent_id, str) or not intent_id:
            self.data_gaps.append(
                f"wal_corrupt: 第 {lineno} 行 committed 缺 intent_id")
            self._wal_orphans.append(line)
            return
        if not isinstance(ok, bool):
            self.data_gaps.append(
                f"wal_corrupt: 第 {lineno} 行 committed({intent_id}) ok 非布尔: {ok!r}")
            self._wal_orphans.append(line)
            return
        self._wal_committed.setdefault(intent_id, []).append(dict(record))

    @staticmethod
    def _intent_dedup_key(intent: Dict[str, Any]) -> Tuple[str, str, str]:
        """intent → 去重键（与 ESCALATION_DEDUP_RULE 同一三维口径）。"""
        return (intent["issue_id"], intent["effective"],
                intent["message_class"])

    def _wal_intent_closed(self, intent: Dict[str, Any]) -> bool:
        """闭环判据：**主状态去重账里已有该键**（主账是唯一权威）。

        只看 committed 不够：committed 只证明总线那次调用返回 ok，若主状态
        随后落盘失败，重启后主账里没有该键——那正是 S11 报出的缺口，必须
        判为「未闭环」并按重发处理（带标记），不能因为「WAL 里有 ok=True」
        就当作已处理。
        """
        return self._intent_dedup_key(intent) in self._escalation_index

    def _wal_write_intent(self, built: Dict[str, Any], ts: float) -> str:
        """送达前写 intent（耐久）并登记内存；返回 intent_id。

        记录里带 ``level`` / ``subject`` / ``body`` 是为了重启后**按原样
        重放**当时那条消息（分级可能已变，重放必须忠实于当时的意图）。

        本方法失败即抛（closed on failure）；调用点选择「照常送达 + 留痕」，
        代价见 ``escalate_issue`` docstring 的「WAL 不可用时的必然代价」一节：
        intent 没写进去，后续对账就**没有线索**，这条送达在后来的落盘失败
        下会退化成不可判定的重复。
        """
        assert self.wal_path is not None
        intent_id = f"WAL-{uuid.uuid4().hex}"
        record = {
            "wal_version": WAL_CONTRACT_VERSION,
            "phase": WAL_PHASE_INTENT,
            "intent_id": intent_id,
            "issue_id": built["issue_id"],
            "effective": built["grade"]["effective"],
            "message_class": built["message_class"],
            "level": built["level"],
            "subject": built["report"].subject,
            "body": built["report"].body,
            "ts": ts,
        }
        self._wal_append(record)
        self._wal_intents[intent_id] = record
        self._wal_committed.setdefault(intent_id, [])
        return intent_id

    def _wal_write_committed(self, intent_id: str, ok: bool,
                             *, redelivery: bool = False) -> None:
        """送达结果落 WAL（含 intent_id 与 ok），作为意图的结局证据。"""
        record = {
            "wal_version": WAL_CONTRACT_VERSION,
            "phase": WAL_PHASE_COMMITTED,
            "intent_id": intent_id,
            "ok": bool(ok),
            "ts": time.time(),
        }
        if redelivery:
            record["redelivery"] = True
        self._wal_append(record)
        self._wal_committed.setdefault(intent_id, []).append(record)

    def _wal_compact(self) -> None:
        """收敛 WAL：只截断本进程视图内已闭环的记录，其余原样保留。

        截断口径（WO-LEGION-S17-01 起，跨进程安全）：压缩前在 WAL
        file_lock 内**重读文件原始行**，逐行解析 ``intent_id`` 按归属分流：
        - 解析不出 ``intent_id``（坏行）→ 原样保留（孤儿口径：丢弃等于把
          「未知结局」当成「已处理」，就是漏报）；
        - ``intent_id`` 不属于本进程 ``_wal_intents`` 视图 → **逐字节原样
          保留（含行序）**。那是他进程的账：他进程留在共享 WAL 里的
          intent/committed，本进程视图不认得，绝不能因本进程压缩而截丢
          （否则他进程崩溃重启后无 intent 可对账 = 漏报）；
        - ``intent_id`` 属于本进程视图 → 应用既有闭环判据
          （``_wal_intent_closed``，主状态去重账为唯一权威）：已闭环则
          截断，未闭环则原样保留。**他进程视图内的闭环判定不做**——那
          需要他进程的主状态去重账，本进程无从判定，也不该替他判。

        两进程同时压缩的互斥由 WAL file_lock 保证（重读-过滤-写回全程
        持锁）；锁序红线不变：WAL file_lock 永不与状态写锁嵌套。

        截断时机（硬约束）：**必须在对应 committed 已耐久写入主状态之后**。
        调用点只有两处——``escalate_issue`` 成功分支（``_persist_state``
        已返回）与 ``reconcile_wal`` 末尾（同上）。若不确定就不截断：
        写回失败时异常上抛，WAL 原样保留，宁可下一轮重放（重放会重新
        按闭环判据过滤，重复是可判定的）也不冒险丢账。

        崩溃在截断中途：写临时文件 + ``os.replace`` 原子替换，故只有
        「旧 WAL 完整」或「新 WAL 完整」两种结果。崩在 replace 之前 = 旧
        WAL 仍在（其记录要么已闭环被重新判掉、要么重放，均不丢）；崩在
        replace 之后 = 已截断，但被截断的前提是主状态已耐久写入，故不会
        丢「已送达」事实。残留 ``.tmp`` 由下一次截断覆盖清理。

        边界：重读时文件缺失（FileNotFoundError）→ 不写不截（「不确定
        就不截断」——盘面已不可见时，不凭内存视图重建猜测盘面），内存
        视图原样保留，交由后续对账处理。
        """
        if self.wal_path is None:
            return
        tmp_path = self.wal_path.with_name(self.wal_path.name + ".tmp")
        with _WAL_WRITE_THREAD_LOCK:
            self.wal_path.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(self._wal_lock_path()):
                # 压缩前重读原始行（锁内）：以盘面为准做逐行归属判定，
                # 而不是以本进程内存视图重写整文件（旧口径会截丢他进程的行）。
                try:
                    raw = self.wal_path.read_text(encoding="utf-8")
                except FileNotFoundError:
                    # 文件不存在：无盘面可压缩，不凭内存视图重建。
                    return
                kept: List[str] = []
                seen_view_ids: set = set()
                for line in raw.splitlines():
                    if not line.strip():
                        continue
                    intent_id: Optional[str] = None
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        record = None
                    if isinstance(record, dict):
                        iid = record.get("intent_id")
                        if isinstance(iid, str) and iid:
                            intent_id = iid
                    if intent_id is None:
                        kept.append(line)     # 坏行：孤儿口径原样保留
                    elif intent_id not in self._wal_intents:
                        kept.append(line)     # 他进程的账：逐字节原样保留
                    elif self._wal_intent_closed(self._wal_intents[intent_id]):
                        continue              # 本进程视图内已闭环 → 截断
                    else:
                        kept.append(line)     # 本进程视图内未闭环 → 原样保留
                        seen_view_ids.add(intent_id)
                # 视图内未闭环但盘上找不到对应行的 intent（理论边界：行被
                # 外部截走）：按内存记录补写，不让视图内的账凭空消失。
                for intent_id, intent in self._wal_intents.items():
                    if intent_id in seen_view_ids:
                        continue
                    if self._wal_intent_closed(intent):
                        continue
                    kept.append(json.dumps(intent, ensure_ascii=False))
                    for committed in self._wal_committed.get(intent_id, []):
                        kept.append(json.dumps(committed, ensure_ascii=False))
                text = "".join(line + "\n" for line in kept)
                try:
                    tmp_path.write_text(text, encoding="utf-8")
                    os.replace(tmp_path, self.wal_path)
                except BaseException:
                    # 失败即闭：清掉半成品后原样抛出，内存视图不动。
                    try:
                        tmp_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise
        # 物理写入成功后才收缩内存视图（顺序不可颠倒：先收缩会让写失败
        # 时磁盘与内存不一致，未闭环记录可能被下一次截断抹掉）。只收缩
        # 本进程视图内已闭环的 intent；他进程的账本就不在本进程视图里。
        for intent_id in list(self._wal_intents):
            if self._wal_intent_closed(self._wal_intents[intent_id]):
                self._wal_intents.pop(intent_id, None)
                self._wal_committed.pop(intent_id, None)

    def _build_redelivery_report(self, intent: Dict[str, Any]) -> Report:
        """按 intent 原样重建补投消息，并打上 ``redelivery_of`` 标记。

        标记落点只用 Report 既有字段（subject 尾部 + body 尾部）：
        - subject 尾部：不影响 ``classify_message_class``（只认 startswith
          前缀），principal 类别的类别校验照常通过；
        - 不新增 field：``report_up`` 发 ``report.__dict__``，
          ``send_pending`` 用 ``Report(**record["report"])`` 复原队列条目，
          多一个键就会 TypeError。
        """
        intent_id = intent["intent_id"]
        level = intent.get("level") or (
            "principal" if intent.get("message_class") else "command")
        subject = intent.get("subject")
        body = intent.get("body") or ""
        if not subject:
            # 老记录/半损坏：回落到按问题单重建（重建会重算分级，正文留痕）。
            built = self.build_escalation_report(intent["issue_id"])
            subject = built["report"].subject
            body = body or built["report"].body
        original_ts = intent.get("ts")
        marker = f"redelivery_of={intent_id}"
        report = Report(
            level=level,
            source=self.name,
            subject=f"{subject} {WAL_REDELIVERY_MARK} {marker}",
            body=(f"{body}\n补投说明: {marker}"
                  f"（上次送达结局未知，按预写日志对账重发；非新问题）"
                  f"\n原意图时刻: {original_ts}"),
        )
        return report

    def _redeliver_intent(self, intent: Dict[str, Any]) -> Dict[str, Any]:
        """对一条「结局未知」的 intent 重发，并把重发事实记进主账。

        记 ``redelivered=True`` + ``redelivery_of``：下游据此**判定这是重发**
        （不是无法区分的重复），可去重也可只告警一次。
        """
        intent_id = intent["intent_id"]
        report = self._build_redelivery_report(intent)
        # 网络不持实例锁（与 escalate_issue 同口径）。
        result = self.report_up(report)
        ok = bool(result.get("ok"))
        self._wal_write_committed(intent_id, ok, redelivery=True)
        if not ok:
            return {"ok": False, "result": result, "report": report}

        ts = time.time()
        key = self._intent_dedup_key(intent)
        with self._state_lock:
            prior = self._escalation_index.get(key) or {}
            attempt = int(prior.get("attempt", 0)) + 1
            self._escalation_index[key] = {
                "ts": ts, "subject": report.subject,
                "attempt": attempt, "level": report.level,
                "redelivered": True, "redelivery_of": intent_id,
            }
            issue = next((i for i in self.issue_board
                          if i["issue_id"] == intent["issue_id"]), None)
            if issue is None:
                # 主状态整份丢失（非本条丢失）：重发照做（漏报优先），
                # 但台账无处落账必须留痕，不得静默。
                self.data_gaps.append(
                    f"wal_redelivery_issue_missing: intent={intent_id} "
                    f"issue_id={intent['issue_id']} 已重发但问题单不在台账")
            else:
                issue.setdefault("escalations", []).append({
                    "message_class": intent["message_class"],
                    "effective": intent["effective"],
                    "level": report.level,
                    "subject": report.subject,
                    "attempt": attempt,
                    "manual_repeat": False,
                    "redelivered": True,
                    "redelivery_of": intent_id,
                    "ts": ts,
                })
            # 主状态耐久写入在此；失败向上抛（closed on failure），
            # 此时不截断 WAL，下一轮仍按「未闭环」重放。
            self._persist_state()
        return {"ok": True, "result": result, "report": report,
                "attempt": attempt}

    # ---- 降级送达账本（WO-LEGION-S15-01：第三条持久化通道） ----
    #
    # 双失败场景（_wal_write_intent 失败 + _persist_state 失败）下，磁盘上
    # 既无 intent 也无去重账，data_gaps 又只是内存列表——进程一死痕迹全失，
    # 这条升级变成「零痕不可判定」。降级账本与 WAL 文件、主状态文件互不
    # 依赖，主状态写失败不拖累它、WAL 盘故障不拖累它。

    def _degraded_path(self) -> Optional[Path]:
        """降级送达账本路径：``<state名>.degraded.jsonl``；零 IO 席位为 None。"""
        if self.state_path is None:
            return None
        return self.state_path.with_name(self.state_path.name + ".degraded.jsonl")

    def _write_degraded_record(self, built: Dict[str, Any], ts: float,
                               err: BaseException) -> None:
        """送达前落一条降级留痕（flush+fsync，独立锁，失败即抛）。

        调用点约定（与 intent 同一条硬约束）：**先落盘后发送**——痕迹必须
        在消息出去之前就在盘上，否则「送达成功但痕迹没写成」又回到零痕。
        写失败向上抛，由调用点降级为 data_gaps（三层兜底的最后一层）。
        """
        path = self._degraded_path()
        if path is None:
            raise RuntimeError(
                "state_path 未注入：零 IO 席位无降级账本，痕迹只能进 data_gaps")
        record = {
            "wal_version": WAL_CONTRACT_VERSION,
            "phase": WAL_PHASE_DEGRADED,
            "issue_id": built["issue_id"],
            "dedup_key": built.get("dedup_key"),
            "level": built["level"],
            "subject": built["report"].subject,
            "ts": ts,
            "err": str(err)[:500],
        }
        line = json.dumps(record, ensure_ascii=False)
        lock_path = path.with_name(path.name + ".lock")
        with _WAL_WRITE_THREAD_LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with file_lock(lock_path):
                with path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
                    fh.flush()
                    os.fsync(fh.fileno())

    def _load_degraded(self) -> None:
        """启动加载降级账本（只读）：每条进 data_gaps，坏行保留原文。

        **不自动补投（与 WAL intent 的区别，显式披露）**：降级条目 =
        「送达意图明确 + 结局未知」，按漏报>重复口径理论上应补投；但降级
        场景意味着磁盘/日志盘正在故障，自动补投走 WAL 写 intent 大概率
        同样失败，且 _redeliver_intent 依赖 WAL 文件本身。本方法的产出是
        「有痕可审计 + 显式人工核账指引」，自动补投另立单。
        """
        path = self._degraded_path()
        if path is None:
            return
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return  # 从未发生过降级送达
        except OSError as exc:
            self.data_gaps.append(f"degraded_corrupt: 降级账本不可读: {exc}")
            return
        for lineno, line in enumerate(raw.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError as exc:
                self.data_gaps.append(
                    f"degraded_corrupt: 第 {lineno} 行非法 JSON: {exc}")
                self._wal_orphans.append(line)
                continue
            if not isinstance(record, dict):
                self.data_gaps.append(
                    f"degraded_corrupt: 第 {lineno} 行不是对象: "
                    f"{type(record).__name__}")
                self._wal_orphans.append(line)
                continue
            self.data_gaps.append(
                f"degraded_send_unresolved: issue={record.get('issue_id')!r} "
                f"ts={record.get('ts')} subject={record.get('subject')!r} "
                f"降级送达结局未知，须人工核对总线账本（bus_ledger/DLQ）与下游")

    def reconcile_wal(self) -> Dict[str, Any]:
        """重启对账：把「结局未知」的 intent 显式重发，其余按判据收敛。

        分类口径（不重发也不丢弃的第三种情况必须存在，否则会把「明确未送达」
        反复重投成噪音）：
        - 主状态去重账已有该键 → 已闭环，不重发；
        - 有 committed 且全部 ok=False（总线明确拒绝，进 DLQ）→ **已知未
          送达**，不自动重发（真相在 DLQ，可审计），保留 WAL 待显式重试；
        - 其余（无 committed，或 committed 有 ok=True 但主账无该键——即
          S11 报出的落盘失败缺口）→ 结局未知 → 重发并打 ``redelivery_of``。

        返回 ``{"redelivered": [...], "kept": [...], "failed": [...]}``；
        失败项进 ``data_gaps``（``wal_redelivery_failed``），intent 保持未
        闭环，下轮再试——不静默吞。

        本方法**不会自己跑起来**：它只在被调用时对账。当前触发点有两个，
        都依赖外部调用方——(1) ``escalate_issue`` 入口的惰性调用；(2) 调用方
        显式调用。因此「田丰长期没有新的升级」时，旧未闭环 intent 不会被
        补投，会一直悬挂在 WAL 里。

        TODO(领域实现): 补投触发依赖调用方；应由常驻心跳周期调用
        （heartbeat_loop 在 roles/base_agent.py，不在本单可改范围），否则
        「长期无新升级」场景下未闭环 intent 会一直悬挂。当前实现**不是**
        「已自动补投」，只是「有调用时对账」。
        """
        summary: Dict[str, List[str]] = {
            "redelivered": [], "kept": [], "failed": []}
        if self.wal_path is None or not self._wal_intents:
            return summary
        for intent_id, intent in list(self._wal_intents.items()):
            if self._wal_intent_closed(intent):
                continue
            commits = self._wal_committed.get(intent_id, [])
            if commits and not any(c.get("ok") is True for c in commits):
                # 已知未送达：不自动重发（避免对结构性拒绝反复重投），
                # 也不丢弃（WAL 记录即未送达证据，直到显式重试成功）。
                summary["kept"].append(intent_id)
                continue
            try:
                self._redeliver_intent(intent)
                summary["redelivered"].append(intent_id)
            except Exception as exc:  # 单条失败不阻断其余对账
                summary["failed"].append(intent_id)
                self.data_gaps.append(
                    f"wal_redelivery_failed: intent={intent_id}: {exc}")
        # 收敛：主状态已在 _redeliver_intent 内耐久写入，此时截断才安全。
        try:
            self._wal_compact()
        except Exception as exc:  # 截断失败不影响正确性，只影响文件大小
            self.data_gaps.append(f"wal_compact_failed: {exc}")
        return summary

    def on_heartbeat(self) -> Dict[str, Any]:
        """心跳周期对账：清未闭环 intent，消除「长期无新升级则悬挂」
        （WO-LEGION-S14-01；reconcile_wal docstring 的 TODO 就地闭合）。

        节流口径：
        - ``_last_reconcile_ts is None``（本进程从未对账）→ **立即对账**：
          重启后的首个心跳就清掉上次进程遗留的悬挂，不等一个周期。
        - 距上次对账 < ``WAL_RECONCILE_MIN_INTERVAL_S`` → 跳过。对账会
          重发 + 截断 WAL，高频空转只会制造噪音与 IO。
        - 到期 → 对账并刷新时间戳。

        可观测性：``redelivered`` 非空打 warning——发生的是**重复投递**，
        运维必须知道（对账口径是「漏报比重复危险」，重复是买来的安全）；
        ``failed`` 已由 reconcile_wal 落 data_gaps，这里不重复记。

        返回对账摘要（含 ``skipped`` 标志），供测试与审计断言；
        heartbeat_loop 只消费异常、不消费返回值。
        """
        now = time.time()
        if (self._last_reconcile_ts is not None
                and now - self._last_reconcile_ts
                < WAL_RECONCILE_MIN_INTERVAL_S):
            return {"skipped": True,
                    "next_due_s": round(
                        WAL_RECONCILE_MIN_INTERVAL_S
                        - (now - self._last_reconcile_ts), 3)}
        summary = self.reconcile_wal()
        self._last_reconcile_ts = now
        if summary["redelivered"]:
            LOG.warning(
                "%s 心跳对账补投 %d 条未闭环 intent: %s（对账口径：漏报比重复危险）",
                self.name, len(summary["redelivered"]),
                summary["redelivered"])
        elif summary["failed"]:
            LOG.warning("%s 心跳对账 %d 条补投失败（已落 data_gaps）: %s",
                        self.name, len(summary["failed"]), summary["failed"])
        return summary

    def wal_open_intents(self) -> List[Dict[str, Any]]:
        """未闭环 intent 只读快照（审计用；副本，外部改动不影响内部）。"""
        return [dict(intent) for intent_id, intent in self._wal_intents.items()
                if not self._wal_intent_closed(intent)]

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
        with self._state_lock:
            if fp in self._fingerprint_index:
                existing_id = self._fingerprint_index[fp]
                for item in self.issue_board:
                    if item["issue_id"] == existing_id:
                        return dict(item, duplicate=True)

            self._issue_seq += 1
            declared = normalize_severity(issue.get("severity"))
            record = {
                "issue_id": f"ISS-{self._issue_seq:04d}",
                "source": source,
                "platform": issue.get("platform"),
                "subject": issue.get("subject"),
                # 分级线索：来源声明的严重度；归口复核见 grade_issue。
                "severity": declared,
                # 声明原始值单独留存且此后不再改写（幂等前提）：grade_issue 只
                # 改写 severity（复核结论），declared 永远可回溯到来源声明，
                # 避免二次复核读到上一次改写结果导致 declared 语义漂移。
                "severity_declared": declared,
                # 分级改写快照（覆盖前留痕）：每次 effective 变化追加一条，
                # 含改写前的完整 grade，供审计回溯「谁在哪一步把分级改掉了」。
                "grade_history": [],
                # 已送达的升级消息台账（含 message_class / effective / ts）。
                "escalations": [],
                # 被幂等拦截的重复投递尝试（不丢事实，与 escalations 分账）。
                "escalation_skips": [],
                "evidence": issue.get("evidence"),
                "fingerprint": fp,
                "status": "open",
                "ts": time.time(),
            }
            self.issue_board.append(record)
            self._fingerprint_index[fp] = record["issue_id"]
            # 登记成功即落盘；写失败向上抛（closed on failure），内存台账
            # 仍是合法的已完成登记状态，不半写。
            self._persist_state()
        return dict(record, duplicate=False)

    # ---- 归口复核分级：分级权在本席（未经归口不得分级） ----

    def grade_issue(self, issue_id: str, *,
                    now: Optional[float] = None) -> Dict[str, Any]:
        """归口复核分级：一般整改 / 升级请示 / 阻断。

        分级口径（TODO(领域实现) 细则待定稿）：以归口登记时随单声明的
        severity 为线索；「阻断」须有佐证（证据非空）方成立，缺佐证则
        降级为升级请示——宁可走 command 层，也不直发本人。

        幂等（老田 2026-10-20 裁定「同 issue 同 severity 不重发 + 覆盖前
        留快照」）：
        - **声明值不可变**：declared 一律取 intake 时固化的
          ``severity_declared``，不读会被本方法改写的 ``severity``。
          此前实现二次复核会读到首次改写结果，declared 语义漂移
          （原先是观察项，现按裁定修正）。
        - **覆盖前留快照**：effective 与既有 severity 不同时，先把
          ``{from, to, reason, prev_grade, ts}`` 追加进 ``grade_history``，
          再改写 severity。同值复核不增量，重复调用结果稳定
          （``changed=False``，幂等）。
        - 分级结论本身每次复算（不缓存），保证佐证补录后可重新分档。
        - 持久化：仅在**发生分级改写**（changed=True）时写回；state_path
          未注入则为纯内存零 IO。
        """
        issue = self._find_issue(issue_id)
        # 声明值以 intake 固化的 severity_declared 为唯一口径；老数据无该
        # 字段时回落到 severity（兼容此前已在内存中的台账）。
        declared = normalize_severity(
            issue.get("severity_declared", issue.get("severity")))
        evidence = issue.get("evidence") or {}
        has_evidence = bool(evidence)
        if declared == SEVERITY_BLOCKING and not has_evidence:
            effective = SEVERITY_ESCALATION
            reason = "阻断声明缺佐证，降级为升级请示（不直发本人）"
        else:
            effective = declared
            reason = ""
        ts = time.time() if now is None else now
        graded = {
            "issue_id": issue_id,
            "declared": declared,
            "effective": effective,
            "reason": reason,
            "has_evidence": has_evidence,
            "ts": ts,
        }
        previous = issue.get("severity")
        changed = normalize_severity(previous) != effective
        graded["changed"] = changed
        with self._state_lock:
            if changed:
                # 覆盖前留快照：连同改写前的完整 grade 一并留存，可回溯。
                issue.setdefault("grade_history", []).append({
                    "from": normalize_severity(previous),
                    "to": effective,
                    "reason": reason,
                    "prev_grade": (dict(issue["grade"])
                                   if "grade" in issue else None),
                    "ts": ts,
                })
            graded["grade_history_len"] = len(issue.get("grade_history") or [])
            issue["severity"] = effective
            issue["grade"] = graded
            if changed:
                # 分级改写即落盘；写失败向上抛（closed on failure），内存
                # 台账保持改写后的合法状态。
                self._persist_state()
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

        本方法只构造不发送（不发网络、不落总线账本）；发送见 escalate_issue。
        注：内部会调用 grade_issue 复核分级——state_path 注入时，若本次
        复核改写了分级，会随分级改写原子落盘（台账一致性），此外无副作用。
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

    def escalate_issue(self, issue_id: str, note: str = "", *,
                       allow_repeat: bool = False,
                       now: Optional[float] = None) -> Dict[str, Any]:
        """归口后分级路径的送达入口：构造上报消息并经总线送出（幂等去重）。

        构造与送达分离：构造见 build_escalation_report（纯函数、可离线审计），
        送达走 BaseAgent.report_up（真实总线投递，失败即闭，closed on failure）。

        幂等（老田 2026-10-20 裁定「同 issue 同 severity 不重发 + 覆盖前
        留快照」）：
        - 去重键 = (issue_id, effective, message_class)，口径见
          ESCALATION_DEDUP_RULE。命中则**不构造送达、不落总线账本**，
          直接返回 ``skipped=True`` 并给出首次送达时刻与 subject；
        - 拦截事实不丢：问题单 ``escalation_skips`` 追加一条，与已送达的
          ``escalations`` 分账——"没发"与"发过"不得混为一谈；
        - **仅成功才记账**：report_up 返回 ``ok=False``（进 DLQ）或抛异常
          均不写入去重账，下次仍可重试（失败不得被当成"已送过"）；
        - 人工强制重推走 ``allow_repeat=True``，问题单与结果都留痕
          （``manual_repeat=True``），不静默放行。
        - 持久化（state_path 注入时）：送达成功与被幂等拦截两种记账都
          原子写回，重启后同一去重键仍被拦截——修复「重启后已送达升级
          被重复投递」的漏洞；落盘失败向上抛（closed on failure），内存
          去重账已生效故同进程不会重发。
        - 预写日志（WO-S12-01，state_path 注入时）：送达**前**先写
          ``phase=intent`` 并 fsync；送达后（且主状态已耐久写入）追加
          ``phase=committed``。若送达成功但主状态落盘失败，intent 保持
          未闭环，重启对账会带 ``redelivery_of=<intent_id>`` 显式重发，
          并在 ``escalations`` 记 ``redelivered=True``——重复不再不可区分，
          而是可判定的补投（治理口径：漏报比重复报危险，故不消除重复，
          只让它可见）。
        - 入口会先做一次惰性对账（``reconcile_wal``）：无待对账 intent
          时零动作；有待对账记录则先把上次结局未知的消息补投，再走本次
          去重判断（补投成功后本次通常被幂等拦截，返回 ``skipped=True``，
          补投事实见 ``wal_redelivered``）。补投的触发缺口见
          ``reconcile_wal`` 的 TODO（依赖调用方，非自动）。

        WAL 不可用时的**必然代价**（不是概率性风险）：
        intent 写失败时本方法仍会送达（避免日志盘故障造成漏报），代价是
        编码死的确定性后果——此后若主状态也落盘失败，磁盘上既无去重账
        也无 intent，重启后同一升级会被**不带 ``redelivery_of`` 标记地再投
        一次**。这条重复与首次投递同形（不可自动区分），但**不再是零痕**：
        S15 起（WO-LEGION-S15-01）送达前会先落独立降级账本
        ``<state名>.degraded.jsonl``（与 WAL/主状态互不依赖），重启后
        ``data_gaps`` 出现 ``degraded_send_unresolved`` 并给出人工核账
        指引。三层兜底：WAL → 降级账本 → data_gaps（内存），降级账本
        写入也失败时仍有内存层留痕（进程存活期间可见）。降级条目**不
        自动补投**——降级场景意味着磁盘正在故障，补投走 WAL 是伪解，
        处置是显式人工核对总线账本与下游；自动补投另立单。
        """
        # 惰性对账：重启后第一次调用送达入口即完成「上轮未知结局」的处置。
        reconciled = self.reconcile_wal()
        built = self.build_escalation_report(issue_id, note=note)
        ts = time.time() if now is None else now
        key = (issue_id, built["grade"]["effective"], built["message_class"])
        built["dedup_key"] = list(key)
        built["wal_redelivered"] = list(reconciled["redelivered"])
        prior = self._escalation_index.get(key)

        if prior is not None and not allow_repeat:
            issue = self._find_issue(issue_id)
            with self._state_lock:
                issue.setdefault("escalation_skips", []).append({
                    "message_class": built["message_class"],
                    "effective": built["grade"]["effective"],
                    "reason": ESCALATION_DEDUP_RULE,
                    "first_sent_ts": prior.get("ts"),
                    "ts": ts,
                })
                # 拦截事实同样落盘：重启后 escalation_skips 可回溯（与
                # escalations 分账不混）。
                self._persist_state()
            return {
                **built,
                "skipped": True,
                "duplicate": True,
                "sent": None,
                "reason": f"幂等拦截：{ESCALATION_DEDUP_RULE}",
                "first_sent_ts": prior.get("ts"),
                "first_subject": prior.get("subject"),
            }

        # ---- 送达前：写 intent 并耐久落盘（顺序硬约束，不可颠倒） ----
        # WAL 写失败**不阻断送达**：本线漏报比重复报危险，日志盘故障不该
        # 让阻断级问题发不出去。降级留痕（data_gaps + wal_degraded），
        # 代价是这一条退回 S11 的「无补偿」语义——可见，不静默。
        intent_id: Optional[str] = None
        if self.wal_path is not None:
            try:
                intent_id = self._wal_write_intent(built, ts)
            except Exception as exc:
                built["wal_degraded"] = True
                # 三层兜底（WO-LEGION-S15-01）：WAL → 降级账本 → data_gaps。
                # 降级账本在送达**前**写（先落盘后发送），双失败场景下
                # 重启仍有痕可审计；它也失败才退回内存 data_gaps。
                try:
                    self._write_degraded_record(built, ts, exc)
                    degraded_note = "降级账本已留痕（重启后 degraded_send_unresolved）"
                except Exception as exc2:
                    degraded_note = f"降级账本写入亦失败: {exc2}"
                self.data_gaps.append(
                    f"wal_write_failed: intent 未落盘，已降级为无 WAL 送达"
                    f"（{degraded_note}）: {exc}")
        if intent_id is not None:
            built["wal_intent_id"] = intent_id

        # 结局未知窗口：report_up 抛异常（重试耗尽/超时）时消息**可能已送达**，
        # 故此处不写 committed、不吞异常（保持既有 closed on failure 语义）。
        # intent 保持未闭环 → 重启对账带标记重发；这是有意的「宁可再发」。
        result = self.report_up(built["report"])
        built["sent"] = result
        built["skipped"] = False
        built["duplicate"] = False
        delivered_ok = bool(result.get("ok"))
        if delivered_ok:
            attempt = (prior or {}).get("attempt", 0) + 1
            with self._state_lock:
                self._escalation_index[key] = {
                    "ts": ts, "subject": built["report"].subject,
                    "attempt": attempt, "level": built["level"],
                }
                issue = self._find_issue(issue_id)
                issue.setdefault("escalations", []).append({
                    "message_class": built["message_class"],
                    "effective": built["grade"]["effective"],
                    "level": built["level"],
                    "subject": built["report"].subject,
                    "attempt": attempt,
                    "manual_repeat": bool(allow_repeat and prior is not None),
                    "ts": ts,
                })
                # 送达成功才记账并落盘：去重账与 escalations 同批写入，
                # 重启后同一去重键仍可拦截（不再重复投递）。写失败向上抛
                # （closed on failure），内存去重账已生效，同进程不会重发。
                # 注意：此处抛异常时**不能**写 committed——未闭环才会被对账。
                self._persist_state()
            built["manual_repeat"] = bool(allow_repeat and prior is not None)
            self._wal_after_main_state(intent_id)
        else:
            # 未送达（DLQ）：不记去重账、不记 escalations，保证可重试。
            # WAL 记 committed(ok=False) 表示「结局已知：明确未送达」，
            # 对账不会自动重投（真相在 DLQ），保留待显式重试成功后收敛。
            built["manual_repeat"] = False
            if intent_id is not None:
                try:
                    self._wal_write_committed(intent_id, False)
                except Exception as exc:
                    self.data_gaps.append(
                        f"wal_write_failed: committed(ok=False) 未落盘: {exc}")
        return built

    def _wal_after_main_state(self, intent_id: Optional[str]) -> None:
        """主状态耐久写入之后才落 committed + 截断（时序硬约束）。

        单独成方法是为了把「committed 必须在主状态之后」这条约束收口到
        一个调用点，避免日后有人在 ``_persist_state`` 之前顺手加一行。
        """
        if self.wal_path is None or intent_id is None:
            return
        try:
            self._wal_write_committed(intent_id, True)
        except Exception as exc:
            # 主状态已生效，重启按主账判闭环，不会误重发；只留痕不抛。
            self.data_gaps.append(
                f"wal_write_failed: committed 未落盘（主账已耐久，重启按主账判闭环）: {exc}")
            return
        try:
            self._wal_compact()
        except Exception as exc:
            self.data_gaps.append(f"wal_compact_failed: {exc}")

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
        with self._state_lock:
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
            # 工单登记即落盘（随「问题单已派单」一起，保证重启后阻断类
            # 仍走整改工单出口，去重键不因重启改变）；写失败向上抛。
            self._persist_state()
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
          不发总线消息，仅在本地工单台账留痕；state_path 注入时合法
          迁移随台账落盘。
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
        # state_path 注入时：合法迁移即落盘（工单状态随账走，避免重启后
        # 工单回退到派单时的状态）；写失败向上抛（closed on failure），
        # 内存工单已迁移到合法新态。
        self._persist_state()
        return order

    # ---- 日常文书职能（保留） ----

    def daily_brief(self) -> Report:
        """每日全局简报：业务进展 + 问题/整改台账摘要。

        TODO(领域实现): 简报字段契约；缺失数据显式标注，不造数。
        """
        raise NotImplementedError

    # ---- 查询（只读） ----

    def issues(self, since: Optional[float] = None) -> List[Dict[str, Any]]:
        """问题台账只读查询。

        台账持久化见 ``state_path``（构造注入）；本方法只读内存快照。
        TODO(领域实现): 审计导出（跨进程只读查询/归档），当前仅单进程内存视图。
        """
        if since is None:
            return list(self.issue_board)
        return [i for i in self.issue_board if i["ts"] >= since]

    def escalation_index(self) -> Dict[str, Dict[str, Any]]:
        """升级去重账只读快照（键为 "issue_id|effective|message_class"）。

        返回副本：调用方增删改不影响本席去重账，避免外部误清导致重发。
        记账时点为「实际送达成功」，未送达（DLQ/异常）不入账，可重试。
        """
        return {
            f"{k[0]}|{k[1]}|{k[2]}": dict(v)
            for k, v in self._escalation_index.items()
        }


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
