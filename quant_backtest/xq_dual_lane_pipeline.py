# -*- coding: utf-8 -*-
"""WO-QT-20261020-076：来源无关、合成页驱动的双 lane 离线处理流水线。

本模块不发起任何网络请求，不接入任何采集器，不写任何生产数据库；
全部输入均为调用方显式构造的合成页（SyntheticPage）。术语对齐既有
xq_offline_queue / xq_request_gate 的页/条目/lane 契约（lane 仅取
'new'/'history'），但不依赖运行它们。

设计要点：
1. 双 lane 配额调度：追新优先 lane（new）与回溯 lane（history）各自
   持有独立待处理队列与槽位配额（lane_slots={"new":N,"history":M}），
   按轮次取任务——每轮先取至多 N 个追新页、再取至多 M 个回溯页；
   一 lane 为空时另一 lane 立即独立推进，两 lane 互不阻塞。
2. 页级断档失败关闭：段（segment = lane+source_key+segment_id）内页码
   必须自首张提交页起严格连续递增；检测到跳号（page_no > last+1）即把
   该段全部待处理任务置 failed_closed 并封闭该段，此后同段任何提交被
   拒绝（segment_closed）——禁止静默跳过继续。失败关闭只作用于断档段
   本身，不牵连其他段。
3. ID 去重保留多股关系：同一 post_id 全流水线只处理一次（输出侧仅
   发布一次），但重复出现时其 cashtag 关系做并集合并，绝不因去重丢弃
   任何一条多股关系；合并次数经 dedup_hits 与 seen_count 暴露。
4. 时间证据：每条目必须携带 fetched_at_ms 与 available_at_ms 双时点
   证据；缺任一证据的条目 freshness=unverified，永远不得进入"新鲜"
   判定。双证齐全时以 available_at_ms 为基准：0 <= judged_at -
   available_at <= freshness_window 判 fresh，否则 stale。
5. 单写者有界缓冲：输出侧唯一写者（single_writer 会话互斥）+ 有界
   缓冲；溢出按策略背压阻塞（backpressure）或丢弃并计数（drop_count），
   计数经 stats().dropped 暴露，绝不静默丢失。
"""

from __future__ import annotations

from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
import threading
from typing import Callable, Iterator, Mapping

Lane = str  # 仅接受 'new' / 'history'
LANES: tuple[str, ...] = ("new", "history")

OVERFLOW_POLICIES: tuple[str, ...] = ("drop_count", "backpressure")


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a nonempty string")
    return value


def _nonneg_int(value: object, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field_name} must be a nonnegative integer")
    return value


@dataclass(frozen=True)
class Entry:
    """一个来源条目（对齐既有术语中的"条目"）。

    fetched_at_ms / available_at_ms 是时间证据双时点；缺任一（None）
    的条目不得进入"新鲜"判定。cashtags 是该条目关联的多股（多
    cashtag）关系，去重合并时全量保留。
    """

    post_id: str
    created_at_ms: int
    cashtags: tuple[str, ...] = ()
    fetched_at_ms: int | None = None
    available_at_ms: int | None = None
    payload: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        _text(self.post_id, "post_id")
        _nonneg_int(self.created_at_ms, "created_at_ms")
        for tag in self.cashtags:
            _text(tag, "cashtag")
        for name in ("fetched_at_ms", "available_at_ms"):
            value = getattr(self, name)
            if value is not None:
                _nonneg_int(value, name)
        if self.payload is not None and not isinstance(self.payload, Mapping):
            raise ValueError("payload must be a mapping or None")


@dataclass(frozen=True)
class SyntheticPage:
    """一张调用方显式构造的合成页（对齐既有术语中的"页"）。

    page_no 在段内必须连续递增；跳号即触发段级失败关闭。
    """

    source_key: str
    lane: Lane
    segment_id: str
    page_no: int
    entries: tuple[Entry, ...]

    def __post_init__(self) -> None:
        _text(self.source_key, "source_key")
        if self.lane not in LANES:
            raise ValueError("lane must be 'new' or 'history'")
        _text(self.segment_id, "segment_id")
        if isinstance(self.page_no, bool) or not isinstance(self.page_no, int) \
                or self.page_no < 1:
            raise ValueError("page_no must be a positive integer")
        if not isinstance(self.entries, tuple):
            object.__setattr__(self, "entries", tuple(self.entries))
        for item in self.entries:
            if not isinstance(item, Entry):
                raise ValueError("page entries must be Entry instances")


@dataclass
class PageTask:
    """页级任务：pending / done / failed_closed。"""

    page: SyntheticPage
    state: str = "pending"
    detail: str = ""


@dataclass
class Segment:
    """段状态：页码连续性与断档失败关闭的作用域。"""

    lane: Lane
    source_key: str
    segment_id: str
    state: str = "open"  # open | failed_closed
    last_page_no: int = 0
    gap_after: int | None = None

    @property
    def key(self) -> tuple[Lane, str, str]:
        return (self.lane, self.source_key, self.segment_id)


@dataclass
class EntryRecord:
    """条目聚合记录：去重合并后的多股关系并集与新鲜度判定。"""

    post_id: str
    created_at_ms: int
    cashtags: set[str]
    fetched_at_ms: int | None
    available_at_ms: int | None
    seen_count: int = 1
    freshness: str = "unverified"  # fresh | stale | unverified


@dataclass(frozen=True)
class OutputRecord:
    """发布到输出侧的单条记录；同一 post_id 只发布一次。"""

    post_id: str
    source_key: str
    lane: Lane
    segment_id: str
    page_no: int
    created_at_ms: int
    cashtags: tuple[str, ...]
    fetched_at_ms: int | None
    available_at_ms: int | None
    freshness: str


@dataclass(frozen=True)
class RelationUpdate:
    """重复原帖中新发现的关系；不作为第二条原帖发布。"""

    post_id: str
    added_cashtags: tuple[str, ...]


@dataclass(frozen=True)
class SubmitResult:
    """一次页提交的结果：queued / stale_page / failed_closed / segment_closed。"""

    outcome: str
    page: SyntheticPage
    detail: str = ""
    closed_tasks: int = 0


@dataclass(frozen=True)
class RunSummary:
    """一次 run_all 的处理摘要；关系增量仅列出成功进入缓冲的事件。"""

    processed_order: tuple[OutputRecord, ...]
    processed_count: int
    dedup_hits: int
    fresh_ids: tuple[str, ...]
    pages_done: int
    pages_failed_closed: int
    buffer_dropped: int
    segments_failed_closed: tuple[tuple[Lane, str, str], ...]
    relation_updates: tuple[RelationUpdate, ...] = ()


class BoundedOutputBuffer:
    """单写者、有界输出缓冲：满则背压阻塞或丢弃计数，绝不静默丢。

    single_writer() 会话内允许至多一个写者；会话外 drain 被拒绝。
    overflow_policy='drop_count' 在缓冲满时丢弃并累加 dropped 计数
    （stats() 暴露）；'backpressure' 则阻塞等待写者排空腾位。
    """

    def __init__(self, maxsize: int = 1024,
                 overflow_policy: str = "drop_count") -> None:
        if isinstance(maxsize, bool) or not isinstance(maxsize, int) or maxsize < 1:
            raise ValueError("maxsize must be a positive integer")
        if overflow_policy not in OVERFLOW_POLICIES:
            raise ValueError("overflow_policy must be 'drop_count' or 'backpressure'")
        self._maxsize = maxsize
        self._policy = overflow_policy
        self._items: deque[object] = deque()
        self._lock = threading.Lock()
        self._space = threading.Condition(self._lock)
        self._writer_active = False
        self._writer_thread: int | None = None
        self._published = 0
        self._dropped = 0

    def offer(self, record: object) -> bool:
        """发布一条记录；drop_count 策略下满则丢弃并计数，返回 False。"""
        with self._lock:
            if len(self._items) >= self._maxsize:
                if self._policy != "backpressure":
                    self._dropped += 1
                    return False
                while len(self._items) >= self._maxsize:
                    self._space.wait()
            self._items.append(record)
            self._published += 1
            self._space.notify_all()
            return True

    @contextmanager
    def single_writer(self) -> Iterator["BoundedOutputBuffer"]:
        """进入唯一写者会话；会话互斥，第二个并发写者立即被拒绝。"""
        with self._lock:
            if self._writer_active:
                raise RuntimeError("output buffer admits exactly one writer")
            self._writer_active = True
            self._writer_thread = threading.get_ident()
        try:
            yield self
        finally:
            with self._lock:
                self._writer_active = False
                self._writer_thread = None
                self._space.notify_all()

    def drain(self, handler: Callable[[object], None]) -> None:
        """仅在单写者会话内排空缓冲并逐条交给 handler。"""
        while True:
            with self._lock:
                if not self._writer_active or self._writer_thread != threading.get_ident():
                    raise RuntimeError("drain requires the owning single writer context")
                if not self._items:
                    return
                item = self._items[0]
            # 不持内部锁调用外部回调；失败时队首不变，可安全重试。
            handler(item)
            with self._lock:
                self._items.popleft()
                self._space.notify_all()

    def stats(self) -> dict[str, object]:
        """缓冲运行统计：capacity/size/published/dropped/写者状态。"""
        with self._lock:
            return {"capacity": self._maxsize, "size": len(self._items),
                    "published": self._published, "dropped": self._dropped,
                    "overflow_policy": self._policy,
                    "writer_active": self._writer_active}


class DualLanePipeline:
    """追新优先/回溯双 lane 离线流水线（合成页驱动，来源无关）。"""

    def __init__(self, lane_slots: dict[str, int] | None = None,
                 output_buffer: BoundedOutputBuffer | None = None) -> None:
        slots = dict(lane_slots) if lane_slots is not None else {"new": 1, "history": 1}
        if set(slots) != set(LANES):
            raise ValueError("lane_slots must define quotas for 'new' and 'history'")
        for lane, quota in slots.items():
            if isinstance(quota, bool) or not isinstance(quota, int) or quota < 1:
                raise ValueError("lane quotas must be positive integers")
        self._slots: dict[Lane, int] = {lane: slots[lane] for lane in LANES}
        self._pending: dict[Lane, deque[PageTask]] = {"new": deque(), "history": deque()}
        self._tasks: list[PageTask] = []
        self._segments: dict[tuple[Lane, str, str], Segment] = {}
        self._records: dict[str, EntryRecord] = {}
        self.buffer = output_buffer if output_buffer is not None else BoundedOutputBuffer()
        self._dedup_hits = 0
        self._run_lock = threading.Lock()
        self._published_tags: dict[str, set[str]] = {}
        self._pending_original: dict[str, OutputRecord] = {}

    # ------------------------------------------------------------------
    # 提交侧：页级断档失败关闭
    # ------------------------------------------------------------------

    def submit_page(self, page: SyntheticPage) -> SubmitResult:
        """提交一张合成页入对应 lane；断档段整段失败关闭。"""
        if not isinstance(page, SyntheticPage):
            raise TypeError("submit_page requires a SyntheticPage")
        key = (page.lane, page.source_key, page.segment_id)
        segment = self._segments.get(key)
        if segment is None:
            segment = Segment(page.lane, page.source_key, page.segment_id)
            self._segments[key] = segment
        if segment.state == "failed_closed":
            return SubmitResult(
                "segment_closed", page,
                f"segment closed after gap after page {segment.gap_after}")
        if segment.last_page_no and page.page_no <= segment.last_page_no:
            return SubmitResult(
                "stale_page", page,
                f"page {page.page_no} not after confirmed page {segment.last_page_no}")
        if segment.last_page_no and page.page_no > segment.last_page_no + 1:
            return self._fail_closed(segment, page)
        task = PageTask(page)
        self._tasks.append(task)
        self._pending[page.lane].append(task)
        segment.last_page_no = page.page_no
        return SubmitResult("queued", page)

    def _fail_closed(self, segment: Segment, page: SyntheticPage) -> SubmitResult:
        """页级断档：封闭整段并把该段全部待处理任务置 failed_closed。"""
        segment.state = "failed_closed"
        segment.gap_after = segment.last_page_no
        detail = (f"page sequence gap in segment {segment.segment_id}: "
                  f"page {segment.gap_after} -> {page.page_no} "
                  f"(expected page {segment.gap_after + 1})")
        closed = 0
        for task in self._tasks:
            if task.state == "pending" and task.page.lane == segment.lane \
                    and task.page.source_key == segment.source_key \
                    and task.page.segment_id == segment.segment_id:
                task.state = "failed_closed"
                task.detail = detail
                closed += 1
        return SubmitResult("failed_closed", page, detail, closed_tasks=closed)

    # ------------------------------------------------------------------
    # 调度侧：双 lane 配额、追新优先、互不阻塞
    # ------------------------------------------------------------------

    def _take_round(self) -> list[PageTask]:
        """取一轮任务：每轮先追新至多 N 个、再回溯至多 M 个。"""
        batch: list[PageTask] = []
        for lane in LANES:
            lane_queue = self._pending[lane]
            taken = 0
            while taken < self._slots[lane] and lane_queue:
                task = lane_queue.popleft()
                # failed_closed 任务出队即弃，绝不进入处理（保留在
                # self._tasks 供审计），禁止静默跳过继续处理。
                if task.state == "pending":
                    batch.append(task)
                    taken += 1
        return batch

    def run_all(self, judged_at_ms: int,
                freshness_window_ms: int) -> RunSummary:
        """串行处理待处理页；并发调用直接拒绝而非重复发布。"""
        if not self._run_lock.acquire(blocking=False):
            raise RuntimeError("run_all already active")
        try:
            return self._run_all(judged_at_ms, freshness_window_ms)
        finally:
            self._run_lock.release()

    def _run_all(self, judged_at_ms: int,
                 freshness_window_ms: int) -> RunSummary:
        _nonneg_int(judged_at_ms, "judged_at_ms")
        _nonneg_int(freshness_window_ms, "freshness_window_ms")
        processed: list[OutputRecord] = []
        updates: list[RelationUpdate] = []
        fresh: list[str] = []
        self._flush_pending(updates)
        while True:
            batch = self._take_round()
            if not batch:
                break
            for task in batch:
                emitted, added = self._process(task, judged_at_ms, freshness_window_ms)
                updates.extend(added)
                for record in emitted:
                    processed.append(record)
                    if record.freshness == "fresh":
                        fresh.append(record.post_id)
        return RunSummary(
            processed_order=tuple(processed),
            processed_count=len(processed),
            dedup_hits=self._dedup_hits,
            fresh_ids=tuple(fresh),
            pages_done=sum(1 for t in self._tasks if t.state == "done"),
            pages_failed_closed=sum(
                1 for t in self._tasks if t.state == "failed_closed"),
            buffer_dropped=self.buffer.stats()["dropped"],
            segments_failed_closed=tuple(
                seg.key for seg in self._segments.values()
                if seg.state == "failed_closed"),
            relation_updates=tuple(updates),
        )

    # ------------------------------------------------------------------
    # 处理侧：时间证据 + ID 去重保留多股关系
    # ------------------------------------------------------------------

    @staticmethod
    def _judge_freshness(record: EntryRecord, judged_at_ms: int,
                         window_ms: int) -> str:
        """新鲜判定：缺任一证据一律 unverified，绝不判 fresh。"""
        if record.fetched_at_ms is None or record.available_at_ms is None:
            return "unverified"
        if 0 <= judged_at_ms - record.available_at_ms <= window_ms:
            return "fresh"
        return "stale"

    def _flush_pending(self, updates: list[RelationUpdate]) -> None:
        """先补原帖再补关系；满缓冲留下待办供下次 run_all 重试。"""
        for post_id, original in tuple(self._pending_original.items()):
            record = self._records[post_id]
            final_original = OutputRecord(
                post_id=original.post_id, source_key=original.source_key,
                lane=original.lane, segment_id=original.segment_id,
                page_no=original.page_no, created_at_ms=original.created_at_ms,
                cashtags=tuple(sorted(record.cashtags)),
                fetched_at_ms=original.fetched_at_ms,
                available_at_ms=original.available_at_ms,
                freshness=original.freshness)
            if self.buffer.offer(final_original):
                self._published_tags[post_id] = set(record.cashtags)
                del self._pending_original[post_id]
            else:
                return
        for post_id, record in self._records.items():
            if post_id not in self._published_tags:
                continue
            added = record.cashtags - self._published_tags[post_id]
            if added:
                update = RelationUpdate(post_id, tuple(sorted(added)))
                if self.buffer.offer(update):
                    self._published_tags[post_id].update(added)
                    updates.append(update)
                else:
                    return

    def _process(self, task: PageTask, judged_at_ms: int,
                 window_ms: int) -> tuple[list[OutputRecord], list[RelationUpdate]]:
        """原帖只发布一次；重复 ID 的新增关系以独立事件发布。"""
        page = task.page
        if task.state != "pending":
            return [], []
        emitted: list[OutputRecord] = []
        updates: list[RelationUpdate] = []
        for entry in page.entries:
            record = self._records.get(entry.post_id)
            if record is None:
                record = EntryRecord(
                    post_id=entry.post_id,
                    created_at_ms=entry.created_at_ms,
                    cashtags=set(entry.cashtags),
                    fetched_at_ms=entry.fetched_at_ms,
                    available_at_ms=entry.available_at_ms,
                )
                record.freshness = self._judge_freshness(
                    record, judged_at_ms, window_ms)
                self._records[entry.post_id] = record
                output = OutputRecord(
                    post_id=record.post_id,
                    source_key=page.source_key,
                    lane=page.lane,
                    segment_id=page.segment_id,
                    page_no=page.page_no,
                    created_at_ms=record.created_at_ms,
                    cashtags=tuple(sorted(record.cashtags)),
                    fetched_at_ms=record.fetched_at_ms,
                    available_at_ms=record.available_at_ms,
                    freshness=record.freshness,
                )
                emitted.append(output)
                if self.buffer.offer(output):
                    self._published_tags[entry.post_id] = set(record.cashtags)
                else:
                    self._pending_original[entry.post_id] = output
            else:
                # 原帖只发布一次；尚未入缓冲的原帖先补发，关系差集后发。
                self._dedup_hits += 1
                record.cashtags |= set(entry.cashtags)
                record.seen_count += 1
                self._flush_pending(updates)
        task.state = "done"
        return emitted, updates

    # ------------------------------------------------------------------
    # 观测侧
    # ------------------------------------------------------------------

    def entry_record(self, post_id: str) -> EntryRecord:
        record = self._records.get(_text(post_id, "post_id"))
        if record is None:
            raise KeyError(f"post_id {post_id!r} has not been processed")
        return record

    def page_tasks(self) -> tuple[PageTask, ...]:
        return tuple(self._tasks)

    def stats(self) -> dict[str, object]:
        """流水线观测统计（含输出缓冲的丢弃计数，绝不静默丢）。"""
        return {
            "pending_new": len(self._pending["new"]),
            "pending_history": len(self._pending["history"]),
            "processed_entries": len(self._records),
            "dedup_hits": self._dedup_hits,
            "segments_failed_closed": sum(
                1 for seg in self._segments.values()
                if seg.state == "failed_closed"),
            "output": self.buffer.stats(),
        }
