"""个人 OS 数字军团 · 智能体基类骨架。

生成物为骨架（scaffold）：所有标注 TODO 处为业务逻辑占位，需按领域实现。
本模块只定义：席位注册、任务七态、分层汇报、心跳与健康端点的结构。
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
import threading
import time
import uuid
from collections import Counter as _MultisetCounter
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import httpx
from fastapi import FastAPI
from prometheus_client import REGISTRY, Counter, Gauge

# 跨进程文件锁平台模块：条件 import，非本平台分支不触发（file_lock 用）。
try:
    import fcntl
except ImportError:  # pragma: no cover - 非 POSIX 平台（如 Windows）
    fcntl = None  # type: ignore[assignment]
try:
    import msvcrt
except ImportError:  # pragma: no cover - 非 Windows 平台
    msvcrt = None  # type: ignore[assignment]

LOG = logging.getLogger("os-legion")

# 汇报层级：基层 → 平台 → 中台 → 本人。任何席位不得越级直发。
REPORT_LEVELS = ("agent", "platform", "command", "principal")

# 上行汇报失败重试：最多 3 次，指数退避自 0.2s 起（0.2 / 0.4）。
MAX_REPORT_ATTEMPTS = 3
REPORT_BACKOFF_BASE_S = 0.2
PENDING_QUEUE_FILENAME = "pending_reports.jsonl"

# 单发送者租约（claim）边车文件名后缀：<队列名>.claim（S12 工位CB）。
# 语义见 BaseAgent.send_pending 的阶段一。
PENDING_CLAIM_SUFFIX = ".claim"

# 租约超时（秒）：超过此时长未被续租，视为持有者已被强杀/崩溃，可被
# 他人回收接管。取值远大于一批重发的正常耗时（单条 HTTP 超时 10s，
# 每发一条前续租一次），目的是把「慢而不死」误判成「已崩溃」的概率
# 压到最低；误判的代价是重复投递（可接受），不误判的收益是不丢。
PENDING_CLAIM_TTL_S = 300.0

# 队列原子重写的**有限**重试（S12 工位CB2）。Windows 上 os.replace 的目标
# 若正被另一进程/线程以只读句柄打开（Python 的 open 不带 FILE_SHARE_DELETE），
# 会抛 PermissionError [WinError 5]——这是平台文件共享语义，不是逻辑错误。
# 只对 PermissionError 重试；读句柄的存活期是「读完一个小 jsonl」级别，
# 同进程交叠已由分片锁消除，这里只兜跨进程的窄窗口：10/20/40ms 三次
# （累计等待上限 70ms）足以覆盖调度抖动，又不会把发送卡在网络/磁盘上；
# 超出即抛（失败即拒绝，绝不写半截）。故不做无限重试。
PENDING_REPLACE_RETRIES = 3
PENDING_REPLACE_BACKOFF_S = 0.01

# pending 队列**进程内按路径分片锁**（S12 工位CB，取代原模块级单锁）。
# 为什么需要它：file_lock 的同进程重入登记表 _HELD_LOCK_PATHS 不区分
# 线程，同进程两个 Agent 实例（或两条线程）并发进入同一路径会被判为
# 「重入」而响亮抛 RuntimeError。分片锁按队列路径归并同一把 RLock
# （同线程可重入、跨线程串行），把同进程并发收敛成串行后再进
# file_lock。锁序全文件统一：分片锁 → file_lock，任何区段只进入一次
# 同路径 file_lock（既有多条 spy 用例锁住「每次 send_pending 恰好一次
# 边车锁」，勿在其中再叠一层）。
_PENDING_PATH_LOCKS: Dict[str, Any] = {}
_PENDING_PATH_LOCKS_GUARD = threading.Lock()


def _pending_path_lock(path: Any) -> Any:
    """取队列路径对应的进程内分片锁（RLock，同线程可重入）。

    语义是「按路径分片」而非「按实例」：同一队列路径必须拿到同一把锁，
    否则同进程多实例并发时会同时进入 file_lock 而被判重入。

    TODO(领域实现): 登记表随 pending_dir 数量增长不回收（长跑进程若
    动态切换大量目录才会显现），当前按「席位目录数有限」接受。
    """
    key = str(Path(path).resolve())
    with _PENDING_PATH_LOCKS_GUARD:
        lock = _PENDING_PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PENDING_PATH_LOCKS[key] = lock
        return lock


# 进程级共享指标：多席位共用同一注册项，按 label 区分（避免重复注册）。
def _shared_metric(cls: Any, name: str, doc: str, labels: List[str]) -> Any:
    """取已注册的同名指标，若无则创建（模块重载/重复导入安全）。

    TODO(领域实现): 如需多注册表隔离（多套军团并行），在此接入自定义 Registry。
    """
    existing = REGISTRY._names_to_collectors.get(name)
    if existing is not None:
        return existing
    return cls(name, doc, labels)


HEARTBEAT_GAUGE = _shared_metric(
    Gauge, "agent_heartbeat_unixtime", "最近心跳时间", ["agent"])
REPORT_COUNTER = _shared_metric(
    Counter, "agent_report_total", "上行汇报条数", ["agent", "level"])


class TaskState(str, Enum):
    """任务七态，对齐仓内既有 tasks 工单语义。"""

    CREATED = "created"
    ASSIGNED = "assigned"
    DOING = "doing"
    REVIEW = "review"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


@dataclass
class StatusPoint:
    """归集状态点：OS 归集的最小单元（老田 2026-10-20 校准）。

    板块对 OS 是黑盒——OS 不关心板块的前端与后端实现，只归集
    「哪个点在干什么、干到哪、产出在哪」。字段由板块自述，OS 不校验
    内部细节；缺失如实留空，不造数。
    """

    agent: str = ""   # 哪个点（席位/Agent 名）
    platform: str = ""  # 所属板块
    doing: str = ""   # 正在干什么（板块自述）
    progress: str = ""  # 干到哪（自述口径，可为空）
    artifact: str = ""  # 产出/证据位置（路径或引用，可为空）
    ts: float = field(default_factory=time.time)


@dataclass
class Report:
    """上行汇报的最小结构。签名与层级由总线核验，席位只负责如实填写。"""

    level: str                 # REPORT_LEVELS 之一
    source: str                # 席位名
    subject: str
    body: str
    ts: float = field(default_factory=time.time)

    def validate(self) -> None:
        if self.level not in REPORT_LEVELS:
            raise ValueError(f"非法汇报层级: {self.level}")


class BaseAgent:
    """所有席位共享的最小骨架：注册、收单、上行汇报、心跳、健康端点。"""

    def __init__(
        self,
        name: str,
        role: str,
        report_to: str,
        bus_url: str,
        metrics_port: int,
        level: str = "agent",
    ) -> None:
        self.name = name
        self.role = role
        self.report_to = report_to
        self.bus_url = bus_url.rstrip("/")
        self.metrics_port = metrics_port
        self.level = level
        self.app = FastAPI()
        self._register_routes()
        self.heartbeat_gauge = HEARTBEAT_GAUGE
        self.report_counter = REPORT_COUNTER
        # 本地持久化队列目录与退避 sleep 注入点（测试/部署可替换）。
        self.pending_dir: str = "pending"
        self.sleep_fn: Callable[[float], None] = time.sleep
        # send_pending 最近一轮的可观测统计（新增字段只进这里，不进返回值：
        # 返回值 {"sent","remaining"} 是既有契约，按整字典相等被用例锁住）。
        # 每一轮（含「他人持租约、本轮空转」分支）都会整体刷新，不留上一轮
        # 残值；skipped_other_holder 用于区分「空转」与「真发不出去」，
        # write_retries 暴露 os.replace 的 PermissionError 重试次数。
        self.last_pending_stats: Dict[str, Any] = {
            "claimed": 0, "sent": 0, "reclaimed": 0, "released": False,
            "skipped_other_holder": False, "write_retries": 0}

    # ---------------- 结构（已实现） ----------------

    def _register_routes(self) -> None:
        @self.app.get("/health")
        def health() -> Dict[str, Any]:
            return {"ok": True, "agent": self.name, "role": self.role,
                    "level": self.level}

        @self.app.post("/task")
        def receive(task: Dict[str, Any]) -> Dict[str, Any]:
            """中台任务单入口。业务处理交由 receive_task 钩子。"""
            state = self.receive_task(task)
            return {"ok": True, "task_id": task.get("task_id"),
                    "state": getattr(state, "value", state)}

    # ---------------- 钩子（骨架，需领域实现） ----------------

    def receive_task(self, task: Dict[str, Any]) -> TaskState:
        """收到任务单后的处理。各席位必须窄职责实现，一单一事。

        TODO(领域实现): 按席位职责解析 task、执行、更新七态；
        涉及签准/交易/生产变更的任务一律转 approval_gate，禁止本地放行。
        """
        raise NotImplementedError(f"{self.name} 未实现 receive_task")

    # ---------------- 基础能力（半成品） ----------------

    def status_point(self) -> StatusPoint:
        """板块自述状态点：OS 归集层的唯一要求接口。

        TODO(领域实现): 各席位从自身运行态提取 doing/progress/artifact；
        只报"是什么"，不暴露内部前后端实现细节。
        """
        raise NotImplementedError(f"{self.name} 未实现 status_point")

    def send_status_point(self) -> Dict[str, Any]:
        """向归集层（经总线）上报状态点。

        已与 heartbeat_loop 合流：心跳周期内自动归集；本方法保留为
        手动/即时触发入口，复用 report_up 的重试与持久化队列。

        TODO(领域实现): 总线侧只做登记与展示投影，不解析板块内部语义。
        """
        point = self.status_point()
        return self.report_up(Report(level="command", source=self.name,
                                     subject=f"[状态点] {point.doing}",
                                     body=str(point.__dict__)))

    def report_up(self, report: Report) -> Dict[str, Any]:
        """向上级（report_to 层）发送汇报；总线负责核验层级与署名。

        失败重试最多 MAX_REPORT_ATTEMPTS 次，指数退避（0.2s 起，sleep_fn
        可注入）；仍失败则写入本地持久化队列 pending_reports.jsonl，
        待 send_pending() 重发。总线不可达或 HTTP 错误均计为失败。
        落队写路径已接 file_lock 边车锁（见 _enqueue_pending），
        避免多席位并发写坏队列文件。
        """
        report.validate()
        if REPORT_LEVELS.index(report.level) < REPORT_LEVELS.index(self.level):
            raise ValueError("禁止向更低层级汇报")
        payload = {"expected_target": self.report_to, "report": report.__dict__}
        last_exc: Optional[Exception] = None
        for attempt in range(MAX_REPORT_ATTEMPTS):
            try:
                resp = httpx.post(f"{self.bus_url}/route", json=payload,
                                  timeout=10)
                if resp.is_success:
                    self.report_counter.labels(self.name, report.level).inc()
                    return resp.json()
                last_exc = RuntimeError(f"总线 HTTP 错误: {resp.status_code}")
            except Exception as exc:  # 连接失败/超时等
                last_exc = exc
            if attempt < MAX_REPORT_ATTEMPTS - 1:
                self.sleep_fn(REPORT_BACKOFF_BASE_S * (2 ** attempt))
        self._enqueue_pending(report)
        assert last_exc is not None  # 重试耗尽必有失败原因
        raise last_exc

    def send_pending(self) -> Dict[str, Any]:
        """重发本地持久化队列中的历史汇报；成功条目移出，失败条目保留。

        时序（四阶段）：整周期互斥靠「单发送者租约」，**网络阶段不持
        file_lock**——发送可能 10s×N 条，持锁会把可用性押在网络 RTT 上。

            阶段一 认领   以 O_CREAT|O_EXCL 建立 <队列>.claim 单发送者租约
                          （纯文件系统原子操作，不进入 file_lock）。抢不到
                          即认定「他人正在发送本队列」，本轮直接空转返回，
                          绝不发送——这就是「同一条待发记录任一时刻最多被
                          一个进程发送」的唯一依据。租约不承载数据：队列
                          文件在原位一动未动，任何时刻被强杀都不丢记录。
            阶段二 快照   持分片锁读取队列全文作为待发清单（与同进程追加
                          串行）。边车 file_lock 此阶段不进入——其重入登记
                          表不区分线程，同进程并发进入会响亮失败；且快照
                          不承担跨进程一致性职责（阶段四以锁内重读为权威
                          兜底，见下）。
            阶段三 发送   锁外逐条 HTTP 重发（不持任何锁）。每发一条前
                          os.utime 续租，避免「慢而不死」被误判为崩溃而遭
                          夺权；一旦发现租约已不属于自己，立刻停手并把剩余
                          条目留给接管者。
            阶段四 合并   持 (分片锁, file_lock) 重读队列为权威内容，按行
                          多重度只剔除「确证发送成功」的快照行；重写内容 =
                          未发送存量（快照中失败行，保序）+ 期间新增（权威
                          内容中快照之外的行，保序）。写盘走临时文件 +
                          os.replace 原子替换，全空则删除队列文件。租约在
                          同一临界区内按 token 校验后释放。

        保证一（不丢）：阶段三全程队列文件完整，进程被强杀时记录仍在队列
        里，恢复后照常重发。阶段四写盘用临时文件 + os.replace，不存在
        「写了一半被杀」把队列截断的中间态。
        保证二（不双投）：只有租约持有者发送；租约靠 O_EXCL 原子创建，
        跨进程唯一。
        保证三（不退化）：阶段三期间他线程/他进程经 _enqueue_pending 追加
        的条目，由阶段四「以锁内重读的全量权威内容为基准、只剔除已确证
        发送的行」保留（S9 修复的 TOCTOU 保证未动）。

        崩溃恢复语义（宁可重复，不可丢）：
        - 唯一会「已发送但未回写」的窗口 = 「某条 HTTP 返回成功 → 阶段四
          合并落盘之前」被强杀，后果是该条重复投递一次。这是显式取舍：
          上报丢失比重复严重。
        - 阶段一建了租约就被杀：租约文件残留，队列不丢；下次 send_pending
          在 PENDING_CLAIM_TTL_S 内视作「他人持有」不发（避免与可能仍活着
          的持有者双投），超期后由 _reclaim_stale_claim 原子夺权回收并留
          WARNING 痕迹。
        - 阶段四写盘中途被杀：需要重发旧内容中已发送的条目（重复），
          不会丢条目。

        返回值契约逐字保持 {"sent": int, "remaining": int}：既有多条守卫用
        例按整字典相等断言，新增键会直接打断它们，故可观测字段改由实例
        属性 last_pending_stats 暴露（claimed / sent / reclaimed /
        released / skipped_other_holder / write_retries），不进入返回值。
        **每一轮都会整体刷新该属性**，包括「他人持租约、本轮空转」的让路
        分支（claimed=0、sent=0、skipped_other_holder=True）：空转也是一次
        真实观测结果，不能让属性停在上一轮、把上一轮的 sent 冒充成本轮的。

        remaining 的读取语义：让路分支要如实报告「此刻队列里还有多少」，
        该读数取自分片锁内的一次短读。若读取本身撞上竞态（PermissionError
        等 OSError，见 _pending_line_count），remaining 返回 **-1**，表示
        「积压量不可判定」而不是「队列空了」——消费方不得把 -1 当 0 用。

        TODO(领域实现): 租约为「整队单写者」，同一队列同一时刻并发度=1；
        积压量需要多进程并行时应改为逐条认领（每条一个 claim 名，语义同上）。
        TODO(领域实现): 租约超期靠 mtime 判定，跨机共享盘上的时钟漂移会
        放大误判窗口；当前目标场景是单机多进程/多实例。
        """
        path = self._pending_path()
        # 无队列文件 = 无待发内容：提前返回，不建租约、不碰任何锁，也不
        # 创建 pending 目录。这是刻意排在认领之前的顺序，不是疏漏：认领
        # 会 O_EXCL 建 .claim、阶段四会进 .lock 并在队列本已不存在时多走
        # 一遍「重读-删空文件」，旧契约（零副作用，见
        # tests/test_ledger_locking.py::test_send_pending_no_file_skips_lock）
        # 与零积压常态下的无谓 IO 都要求在此止步。勿因「租约才是不双投
        # 的唯一依据」把本判断挪到认领之后。
        if not path.exists():
            return {"sent": 0, "remaining": 0}
        claim, token, reclaimed = self._acquire_pending_claim()
        if claim is None:
            # 他人持租约：本轮一律不发（宁可空转，不可双投）。统计必须在本轮
            # 刷新——否则 last_pending_stats 会停在上一轮的数值，观测说谎
            # （上一轮 sent=3、本轮实际空转，读者会误以为刚发了 3 条）。
            self.last_pending_stats = {
                "claimed": 0,
                "sent": 0,
                "reclaimed": reclaimed,
                "released": False,
                "skipped_other_holder": True,
                "write_retries": 0,
            }
            return {"sent": 0, "remaining": self._pending_line_count(path)}
        sent = 0
        claimed = 0
        remaining: List[str] = []
        final: List[str] = []
        released = False
        write_retries = 0
        try:
            # 阶段二：分片锁内快照（进程内串行，时序见 docstring）。
            with _pending_path_lock(path):
                lines = self._read_queue_lines(path)
            claimed = len(lines)
            # 阶段三：锁外发送（不持任何锁，网络重试不阻塞他人追加）。
            for idx, line in enumerate(lines):
                if not self._claim_owned_by(claim, token):
                    # 租约已被回收接管：立即停手，剩余条目留给接管者，
                    # 避免两个进程同时发同一批（宁可本轮少发，不可双投）。
                    remaining.extend(lines[idx:])
                    LOG.warning("pending 租约已易主，本轮停止发送剩余 %d 条",
                                len(lines) - idx)
                    break
                try:
                    os.utime(claim, None)  # 续租：批次慢不应被判崩溃夺权
                except OSError:
                    pass  # 续租失败只缩短他人夺权窗口，不影响本轮
                record = json.loads(line)
                report = Report(**record["report"])
                try:
                    resp = httpx.post(f"{self.bus_url}/route", json={
                        "expected_target": self.report_to,
                        "report": report.__dict__,
                    }, timeout=10)
                    if resp.is_success:
                        sent += 1
                        self.report_counter.labels(self.name, report.level).inc()
                        continue
                except Exception:
                    pass  # 仍不可达，保留待下次
                remaining.append(line)
            # 阶段四：锁内重读 + 合并 + 原子重写 + 释放租约。
            with (_pending_path_lock(path),
                  file_lock(self._pending_lock_path())):
                current = self._read_queue_lines(path)
                # 多重度计数用标准库 Counter（别名，避免与 prometheus_client
                # 的指标 Counter 重名）；快照行数-失败行数=已发送行数。
                sent_counts = _MultisetCounter(lines)
                sent_counts.subtract(remaining)
                # 以锁内重读的权威内容为基准（失败行本就仍在文件中，不再
                # 另行播种以免重复）：剔除确证发送成功的快照行后，剩下的
                # 正是「未发送存量（原序在前）+ 期间新增（原序在后）」。
                for ln in current:
                    if sent_counts.get(ln, 0) > 0:
                        sent_counts[ln] -= 1
                    else:
                        final.append(ln)
                if final:
                    write_retries = self._atomic_write_lines(path, final)
                else:
                    path.unlink(missing_ok=True)
                released = self._release_pending_claim(claim, token)
            return {"sent": sent, "remaining": len(final)}
        finally:
            # 异常路径（解析失败 / 写盘失败等）也要放掉租约，否则占着队列
            # 到超时才恢复；token 不匹配时不删他人租约（closed on failure）。
            # 这里必须**接住**返回的 bool：否则异常路径明明已释放租约，
            # last_pending_stats["released"] 却报 False（观测说谎）。
            if not released:
                released = self._release_pending_claim(claim, token)
            self.last_pending_stats = {
                "claimed": claimed,
                "sent": sent,
                "reclaimed": reclaimed,
                "released": released,
                "skipped_other_holder": False,
                "write_retries": write_retries,
            }

    def _pending_path(self) -> Path:
        return Path(self.pending_dir) / PENDING_QUEUE_FILENAME

    def _pending_claim_path(self) -> Path:
        """单发送者租约文件：<队列名>.claim（与队列同目录，同文件系统）。

        不复用 .lock 边车锁文件：锁文件是 file_lock 的长驻句柄锚点，
        语义是「临界区互斥」；租约的语义是「谁负责这批发送」，生命周期
        覆盖整个网络阶段，两者混用会让锁持有时间被网络 RTT 绑架。
        """
        pending_path = self._pending_path()
        return pending_path.with_name(pending_path.name + PENDING_CLAIM_SUFFIX)

    def _read_queue_lines(self, path: Path) -> List[str]:
        """读队列全部非空行；文件不存在返回空表（不建文件）。"""
        if not path.exists():
            return []
        return [ln for ln in path.read_text(encoding="utf-8").splitlines()
                if ln.strip()]

    def _pending_line_count(self, path: Path) -> int:
        """返回队列条数；**-1 表示积压量不可判定（读取竞态），不是 0**。

        读也在分片锁内：同进程里「抢不到租约的一方」读队列时，持租约方
        可能正在阶段四 os.replace 同一文件；Windows 上「读句柄未关 +
        目标被替换」会让**写方**抛 WinError 5。把读收敛进锁内即可在本
        进程内彻底消除该交叠（跨进程那一段由 _atomic_write_lines 的有限
        重试兜底）。等待上限只是阶段四的短临界区（读—写—替换，不含网络
        阶段），不会把空转变成长阻塞。

        为什么失败返回 -1 而不是 0：0 会被消费方读成「队列已空」，这是最
        危险的谎报；-1 是显式「不可判定」，消费方必须区别对待，不得当 0 用
        （返回值 remaining=-1 即表示「本轮空转，但积压量读不到」）。
        """
        try:
            with _pending_path_lock(path):
                return len(self._read_queue_lines(path))
        except FileNotFoundError:
            return 0  # 队列刚被持有者清空：0 是真实值，不属于竞态
        except OSError:
            LOG.warning("pending 队列条数不可判定（读取竞态，返回 -1 而非 0）：%s",
                        path, exc_info=True)
            return -1

    def _atomic_write_lines(self, path: Path, lines: List[str]) -> int:
        """临时文件 + os.replace 原子替换队列全文；返回发生过的重试次数。

        崩溃语义：队列表要么是旧内容、要么是新内容，没有「写了一半被杀」
        的截断态；代价是旧内容里已成功发送的条目会被再发一次（重复优先
        于丢失）。失败路径（含注入异常）在 finally 清掉临时文件。

        有限重试：只对 PermissionError 重试（Windows 上另一进程/线程持有
        该文件的只读句柄时，os.replace 会抛 [WinError 5]），退避
        PENDING_REPLACE_BACKOFF_S 的指数序列，最多 PENDING_REPLACE_RETRIES
        次。为什么用重试而不是加互斥锁：同进程的交叠已由分片锁消除（读侧
        _pending_line_count 与阶段四同在 _pending_path_lock 内，临界区只
        覆盖「读—写—替换」）；剩下的是**跨进程**读句柄，file_lock 管不到
        它（对方压根没持锁在读）。若为此把让路方的读也纳入跨进程互斥，
        等于让「本应立即空转」的一方去等赢家的写，可用性反而更差——故由
        写者承担有限重试代价。超过上限即抛出（失败即拒绝），不做无限
        重试，不把发送卡在磁盘上。
        """
        tmp = path.with_name(
            f"{path.name}.tmp.{os.getpid()}-{uuid.uuid4().hex}")
        retries = 0
        try:
            tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
            while True:
                try:
                    os.replace(tmp, path)
                    return retries
                except PermissionError:
                    if retries >= PENDING_REPLACE_RETRIES:
                        raise  # 失败即拒绝：调用方 finally 释放租约，队列未半截
                    time.sleep(PENDING_REPLACE_BACKOFF_S * (2 ** retries))
                    retries += 1
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass  # 清理失败不影响主流程（残留由 _cleanup_stale_tmp 兜底）

    # ---------------- 单发送者租约（S12 工位CB） ----------------

    def _acquire_pending_claim(self) -> Any:
        """阶段一：以 O_CREAT|O_EXCL 争抢单发送者租约（不进入 file_lock）。

        返回 ``(claim_path, token, reclaimed)``；抢不到返回
        ``(None, None, reclaimed)``。抢不到即表示他进程/他实例正在发送本
        队列，调用方必须空转——不得退化为「无租约也发」，否则「同一条只
        发一次」不成立（失败即拒绝，closed on failure）。
        """
        claim = self._pending_claim_path()
        claim.parent.mkdir(parents=True, exist_ok=True)
        reclaimed = 0
        # 至多两轮：建租约 → （超期）回收后重试一次。
        for _ in range(2):
            try:
                fd = os.open(str(claim), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if not self._reclaim_stale_claim(claim):
                    return None, None, reclaimed  # 他人活跃持有，本轮不发
                reclaimed = 1
                continue
            except OSError:
                LOG.warning("pending 租约创建失败（失败即拒绝）：%s 本轮不发送，"
                            "条目全部留在队列", claim, exc_info=True)
                return None, None, reclaimed
            token = f"{os.getpid()}-{uuid.uuid4().hex}"
            try:
                os.write(fd, json.dumps(
                    {"token": token, "pid": os.getpid(), "ts": time.time()},
                    ensure_ascii=False).encode("utf-8"))
            finally:
                os.close(fd)
            # 只有持租约者会走到这里，顺手清理崩溃遗留的写盘临时文件。
            self._cleanup_stale_tmp(claim.parent)
            return claim, token, reclaimed
        return None, None, reclaimed

    def _reclaim_stale_claim(self, claim: Path) -> bool:
        """超期租约回收：返回 True 表示租约已可重新争抢。

        夺权用 ``os.replace(claim, stale)``——原子 rename，多个进程同时
        回收只有一个能成功移走；失败方下一轮 O_EXCL 也只会有一个赢家，
        因此不会出现「两个进程都以为自己持有」。回收必留 WARNING 痕迹。
        """
        try:
            age = time.time() - claim.stat().st_mtime
        except FileNotFoundError:
            return True  # 已被他人清掉，可直接重试争抢
        except OSError:
            return False
        if age < PENDING_CLAIM_TTL_S:
            return False  # 未超期一律视为活跃持有（宁可本轮空转）
        stale = claim.with_name(
            f"{claim.name}.stale.{os.getpid()}-{uuid.uuid4().hex}")
        try:
            os.replace(claim, stale)
        except FileNotFoundError:
            return True
        except OSError:
            LOG.warning("pending 租约回收失败（失败即拒绝）：%s 本轮不发送",
                        claim, exc_info=True)
            return False
        try:
            payload = stale.read_text(encoding="utf-8")[:200]
        except OSError:
            payload = "<不可读>"
        finally:
            try:
                stale.unlink(missing_ok=True)
            except OSError:
                pass
        LOG.warning(
            "pending 租约超期回收：%s 已 %.0fs 未续租（持有者疑似崩溃/被强杀），"
            "租约移交本进程；若原持有者仍存活，该批记录可能重复投递"
            "（宁可重复，不可丢）。原租约内容=%s",
            claim.name, age, payload)
        return True

    def _claim_owned_by(self, claim: Path, token: str) -> bool:
        """发送途中复核租约归属：内容可读且 token 一致才算仍持有。"""
        try:
            data = json.loads(claim.read_text(encoding="utf-8") or "{}")
        except (OSError, ValueError):
            return False  # 读不到/被改写：按「已易主」处理，立即停手
        return data.get("token") == token

    def _release_pending_claim(self, claim: Path, token: str) -> bool:
        """按 token 校验后释放租约；非本进程租约一律不删（closed on failure）。

        校验的目的：崩溃回收可能已把租约移交他人，此时按路径盲删会删掉
        别人的租约，等于凭空放开第二个发送者。
        """
        try:
            data = json.loads(claim.read_text(encoding="utf-8") or "{}")
        except FileNotFoundError:
            return True  # 已被回收，等价于已释放
        except (OSError, ValueError):
            return False
        if data.get("token") != token:
            LOG.warning("pending 租约 token 不匹配（已被他人接管），本进程不删"
                        "他人租约（closed on failure）：%s", claim.name)
            return False
        try:
            claim.unlink(missing_ok=True)
        except OSError:
            LOG.warning("pending 租约删除失败（closed on failure）：%s",
                        claim.name, exc_info=True)
            return False
        return True

    def _cleanup_stale_tmp(self, directory: Path) -> None:
        """清理崩溃遗留的写盘临时文件（仅超期才删，当轮的由 finally 收）。"""
        try:
            entries = list(directory.glob(f"{PENDING_QUEUE_FILENAME}.tmp.*"))
        except OSError:
            return
        now = time.time()
        for tmp in entries:
            try:
                if now - tmp.stat().st_mtime >= PENDING_CLAIM_TTL_S:
                    tmp.unlink(missing_ok=True)
            except OSError:
                continue

    def _pending_lock_path(self) -> Path:
        """pending 队列写路径共用的边车锁文件：<队列名>.lock。

        选择依据：直锁队列文件本身不可行——file_lock 以 open("a")
        打开锁句柄，位置停在 EOF，msvcrt.locking/fcntl 锁住的字节区间
        与临界区内的追加/重写句柄冲突（Windows 强制字节区间锁，实测
        追加即抛 PermissionError）。边车锁不落在被写文件上，规避冲突；
        同调用链（report_up 落队 / send_pending 重写）各自只进入一次，
        无嵌套重入，不触发 file_lock 的 RuntimeError。
        """
        pending_path = self._pending_path()
        return pending_path.with_name(pending_path.name + ".lock")

    def _enqueue_pending(self, report: Report) -> None:
        path = self._pending_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # 整行追加持边车锁；进程内先按队列路径分片串行，避免同进程多实例/
        # 多线程并发进入 file_lock 时撞上「不支持同进程重入」的 RuntimeError
        # （详见 _pending_path_lock）。锁序：分片锁 → file_lock。
        with (_pending_path_lock(path),
              file_lock(self._pending_lock_path())):
            with path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"report": report.__dict__},
                                    ensure_ascii=False) + "\n")

    def on_heartbeat(self) -> None:
        """每周期心跳的可选钩子（WO-LEGION-S14-01）。

        默认空实现：大多数席位没有周期任务，强制实现只会把 11 个角色
        全改成占位噪音。需要周期对账/巡检的席位覆写本方法，心跳循环
        每周期调用一次。

        约定：覆写方**自己负责留痕**——本基类只在钩子抛异常时打 warning
        并让心跳继续（与 heartbeat_loop「不崩」语义一致）；钩子内部的
        业务失败应落到席位自己的 data_gaps / 日志，不得静默吞掉。
        """

    async def heartbeat_loop(self, interval: int = 60,
                             max_cycles: int = 0) -> None:
        """周期心跳：写本地指标 + 上报总线 + 归集状态点（已合流）。

        status_point 未实现（NotImplementedError）时跳过归集，不崩；
        状态点经 report_up 上报，总线不可达时落本地队列，同样不崩。
        max_cycles 仅供测试/受限运行注入，0 表示无限循环。

        TODO(领域实现): 指标落盘与总线侧断连判定（当前仅占位计数）。
        """
        cycles = 0
        while True:
            self.heartbeat_gauge.labels(self.name).set(time.time())
            # 周期钩子（WO-LEGION-S14-01）：对账/巡检类周期任务的挂载点。
            # 钩子异常不中止心跳——心跳挂了状态点归集也一起停，
            # 单个周期任务的异常不该有这个杀伤半径；留痕后继续。
            try:
                self.on_heartbeat()
            except Exception:
                LOG.warning("%s 心跳钩子 on_heartbeat 执行失败，本周期跳过",
                            self.name, exc_info=True)
            # TODO(领域实现): POST {bus}/heartbeat
            try:
                point = self.status_point()
            except NotImplementedError:
                point = None  # 席位未实现状态点，跳过归集
            if point is not None:
                try:
                    self.report_up(Report(
                        level="command", source=self.name,
                        subject=f"[状态点] {point.doing}",
                        body=str(point.__dict__)))
                except Exception:
                    LOG.warning("%s 心跳归集上报失败，已落本地队列（如有）",
                                self.name, exc_info=True)
            cycles += 1
            if max_cycles and cycles >= max_cycles:
                return
            await self._sleep(interval)

    @staticmethod
    async def _sleep(seconds: int) -> None:
        import asyncio  # 标准库
        await asyncio.sleep(seconds)


# ---------------- 跨进程文件锁原语（工位X 追加，不改既有行为） ----------------

# 平台标记：测试可 monkeypatch 覆盖以隔离 Windows/POSIX 分支。
IS_WINDOWS = sys.platform.startswith("win")

# 锁文件句柄打开的重试参数（WO-LEGION-S13-01）。
# 仅针对 Windows 上 CreateFile 返回 ERROR_ACCESS_DENIED（CRT 映射为 EACCES）
# 的瞬时竞争：多进程几乎同时首次创建同一锁文件时实测会命中（pytest 下
# 4 进程 × 8 次并发 submit 的失败率 ≈27%）。重试面必须窄、次数必须有上限、
# 耗尽必须抛原异常——不允许退化成无锁，见 _open_lock_handle。
LOCK_OPEN_RETRIES = 5
LOCK_OPEN_BACKOFF_S = 0.005

# 同进程重入显式拒绝的登记表：锁文件绝对路径 -> 持有标记。
_HELD_LOCK_PATHS: Dict[str, bool] = {}

# 登记表守卫锁（模块级新锁，勿复用业务锁）：保护 _HELD_LOCK_PATHS
# 的检查-登记-清除三步原子性，避免多线程并发进出同一路径时双双通过
# 检查（误放行）或退出时漏清除/误报重入。职责仅限登记表本身，不与
# _PENDING_PATH_LOCKS 等业务锁复用，避免锁序纠缠。
_HELD_LOCK_PATHS_GUARD = threading.Lock()


def _acquire_platform_lock(fh: Any) -> bool:
    """按平台标记对已打开句柄加排它锁；返回是否真正加锁。

    平台锁模块不可用（分支标记与实际 import 结果不匹配，或双方均
    import 失败）时返回 False，由调用方退化为无锁并告警。
    """
    if IS_WINDOWS:
        if msvcrt is None:
            return False
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)
        return True
    if fcntl is None:
        return False
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    return True


def _release_platform_lock(fh: Any) -> None:
    """按平台标记解锁（仅当 _acquire_platform_lock 返回 True 时调用）。"""
    if IS_WINDOWS:
        if msvcrt is not None:
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    elif fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def _open_lock_handle(lock_path: Any) -> Any:
    """打开锁文件句柄；对 Windows 瞬时 EACCES 做有限次退避重试（WO-LEGION-S13-01）。

    为什么需要它：``open("a")`` 在 Windows 上走 CRT 的 ``CreateFile``，
    多个进程几乎同时**首次创建**同一锁文件、且处于 CPU 密集的启动阶段时，
    会偶发返回 ``ERROR_ACCESS_DENIED``（Python 侧表现为 ``PermissionError``，
    ``errno == 13``）。实测：``4 子进程 × 8 次并发 submit`` 在 pytest 时序下
    失败率约 27%（11 跑 3 红，堆栈三次完全同点）；而同样 4 进程单纯
    ``open``+``msvcrt.locking`` 各 1200 次、真实 ``submit`` 480 次均为 0 失败，
    故排除区间锁冲突、目录属性与「高频即触发」三类假设。

    重试面刻意收窄：
    - 只重试 ``PermissionError``。``FileNotFoundError``/其它 ``OSError``
      立即上抛——父目录已由调用方 ``mkdir(parents=True)`` 保证，缺文件
      说明是真故障，重试只会把它拖成超时。
    - 次数与退避有硬上限（``LOCK_OPEN_RETRIES`` × 指数退避，合计 <100ms），
      不能靠「多等一会儿」掩盖持续占用。
    - **重试成功也留痕**：重试次数 >0 时打 warning。环境存在竞争是运维该
      知道的事实，静默吸收会让这类问题永远不可观测。
    - **耗尽后抛原异常**，绝不返回无锁句柄：跨进程互斥是账本正确性的前提，
      退成无锁等于用正确性换可用性。
    """
    last_exc: Optional[BaseException] = None
    delay = LOCK_OPEN_BACKOFF_S
    for attempt in range(LOCK_OPEN_RETRIES):
        try:
            fh = lock_path.open("a")
        except PermissionError as exc:
            last_exc = exc
            if attempt + 1 >= LOCK_OPEN_RETRIES:
                break
            time.sleep(delay)
            delay *= 2
            continue
        if attempt:
            LOG.warning(
                "file_lock: %s 打开锁句柄重试 %d 次后成功（环境存在文件竞争，"
                "已用掉 %d/%d 次重试额度）",
                lock_path, attempt, attempt, LOCK_OPEN_RETRIES)
        return fh
    LOG.error(
        "file_lock: %s 打开锁句柄连续 %d 次 EACCES，放弃并上抛"
        "（不降级为无锁，跨进程互斥不可放弃）",
        lock_path, LOCK_OPEN_RETRIES)
    raise last_exc  # type: ignore[misc]


@contextlib.contextmanager
def file_lock(path: Any) -> Any:
    """跨进程互斥的文件锁上下文管理器（模块级原语，供各席位共用）。

    用法::

        with file_lock("locks/queue.lock"):
            ... 临界区写盘 ...

    行为：
    - 锁文件落在 ``path``；父目录不存在则自动创建（mkdir parents）。
    - 打开锁句柄遇 Windows 瞬时 ``PermissionError``（EACCES）时做有限次
      退避重试，见 ``_open_lock_handle``；**额度耗尽仍抛原异常，不降级无锁**。
    - Windows 用 msvcrt.locking(LK_LOCK)，POSIX 用 fcntl.flock(LOCK_EX)，
      按模块级平台标记 IS_WINDOWS 分派；非本平台分支不触发。
    - 退出时解锁并关闭句柄；临界区内抛异常同样保证解锁 + 关句柄
      （closed on failure：无论加锁成功与否，退出时句柄一律关闭）。

    同进程重入语义（显式拒绝）：
    同一进程对同一路径重复进入 file_lock 将抛 RuntimeError，不做
    可重入计数——嵌套持锁多半是设计缺陷，宁可响亮失败，也不静默
    放行造成误锁。跨进程互斥由底层平台锁保证。

    fallback：msvcrt 与 fcntl 均 import 失败（或当前分支的平台模块
    不可用）时退化为无锁模式：仅创建/打开锁文件并 logger.warning，
    不提供任何互斥保证；退出时照常关闭句柄（closed on failure）。

    挂账：已接入 pending_reports 队列写路径；其他账本写路径
    （bus_ledger 等）待评估。
    """
    lock_path = Path(path)
    key = str(lock_path.resolve())
    # 检查-登记两步在守卫锁内原子完成（先登记后开句柄；若后续开句柄
    # 失败，finally 会原子清除，最终状态与登记前一致）。
    with _HELD_LOCK_PATHS_GUARD:
        if key in _HELD_LOCK_PATHS:
            raise RuntimeError(
                f"file_lock 不支持同进程重入: {lock_path} 已被本进程持有")
        _HELD_LOCK_PATHS[key] = True
    fh = None
    locked = False
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        # 取锁句柄：瞬时 EACCES 做有上限退避重试，耗尽抛原异常（WO-LEGION-S13-01）。
        fh = _open_lock_handle(lock_path)
        locked = _acquire_platform_lock(fh)
        if not locked:
            LOG.warning(
                "file_lock: 平台锁模块不可用（IS_WINDOWS=%s, fcntl=%s, "
                "msvcrt=%s），%s 退化为无锁模式，无跨进程互斥保证",
                IS_WINDOWS, fcntl is not None, msvcrt is not None, lock_path)
        yield fh
    finally:
        if locked:
            try:
                _release_platform_lock(fh)
            except OSError:
                LOG.warning("file_lock: %s 解锁失败（句柄仍将关闭）",
                            lock_path, exc_info=True)
        if fh is not None:
            fh.close()
        # 清除在守卫锁内原子完成，避免并发退出时漏清除/误报重入。
        with _HELD_LOCK_PATHS_GUARD:
            _HELD_LOCK_PATHS.pop(key, None)
