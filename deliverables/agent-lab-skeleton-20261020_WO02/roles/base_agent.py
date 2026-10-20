"""个人 OS 数字军团 · 智能体基类骨架。

生成物为骨架（scaffold）：所有标注 TODO 处为业务逻辑占位，需按领域实现。
本模块只定义：席位注册、任务七态、分层汇报、心跳与健康端点的结构。
"""
from __future__ import annotations

import contextlib
import json
import logging
import sys
import time
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

        TODO(领域实现): 队列文件跨进程加锁，避免多席位并发写坏。
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
        """重发本地持久化队列中的历史汇报；全部成功后清空队列。

        TODO(领域实现): 部分失败时保留剩余条目（当前按行原子化重写）。
        """
        path = self._pending_path()
        if not path.exists():
            return {"sent": 0, "remaining": 0}
        lines = [ln for ln in
                 path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        remaining: List[str] = []
        sent = 0
        for line in lines:
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
        if remaining:
            path.write_text("\n".join(remaining) + "\n", encoding="utf-8")
        else:
            path.unlink()
        return {"sent": sent, "remaining": len(remaining)}

    def _pending_path(self) -> Path:
        return Path(self.pending_dir) / PENDING_QUEUE_FILENAME

    def _enqueue_pending(self, report: Report) -> None:
        path = self._pending_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"report": report.__dict__},
                                ensure_ascii=False) + "\n")

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

# 同进程重入显式拒绝的登记表：锁文件绝对路径 -> 持有标记。
_HELD_LOCK_PATHS: Dict[str, bool] = {}


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


@contextlib.contextmanager
def file_lock(path: Any) -> Any:
    """跨进程互斥的文件锁上下文管理器（模块级原语，供各席位共用）。

    用法::

        with file_lock("locks/queue.lock"):
            ... 临界区写盘 ...

    行为：
    - 锁文件落在 ``path``；父目录不存在则自动创建（mkdir parents）。
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

    挂账：既有账本写路径（pending_reports.jsonl 等）接锁待编排者
    统一评估；本原语先落地，不改动 report_up 既有行为。
    """
    lock_path = Path(path)
    key = str(lock_path.resolve())
    if key in _HELD_LOCK_PATHS:
        raise RuntimeError(
            f"file_lock 不支持同进程重入: {lock_path} 已被本进程持有")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a")
    _HELD_LOCK_PATHS[key] = True
    locked = False
    try:
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
        fh.close()
        _HELD_LOCK_PATHS.pop(key, None)
