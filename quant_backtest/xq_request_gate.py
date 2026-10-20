# -*- coding: utf-8 -*-
"""雪球主源的纯离线、跨进程请求许可与人工停轮原语。

本模块不发起网络请求，也不接入既有采集器。唯一安全发送入口 execute_once
持有固定跨进程文件锁，依次持久占额、持久标记发送、发送一次、判断 403
并持久停轮，然后释放锁。网络期间不得持有未提交的 SQLite 写事务。
回调只能发送一个物理请求，不得包装旧爬虫/旧内部重试/代理验活。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import math
import os
import sqlite3
import time
from typing import Callable, Iterator, Protocol, TypeVar
from uuid import uuid4


class Clock(Protocol):
    """可注入的、返回 Unix 秒的时钟。跨进程应使用一致的时间基准。"""

    def now(self) -> float:
        """返回当前 Unix 时间（秒）。"""


class SystemClock:
    """生产默认墙上时钟；窗口在系统时间回拨时保守地不释放令牌。"""

    def now(self) -> float:
        """返回当前 Unix 时间（秒）。"""
        return time.time()


class GateDenied(RuntimeError):
    """停轮、预算不足、节奏限制或许可失效时拒绝发送。"""


@dataclass(frozen=True)
class Permit:
    """单次请求的持久令牌，发放即占用预算，不能退还。"""

    gate_id: str
    permit_id: int
    generation: int
    issued_at: float


@dataclass(frozen=True)
class GateSnapshot:
    """不改变状态的闸门快照；issued_total 不是成功请求数。"""

    gate_id: str
    generation: int
    stopped: bool
    stop_reason: str | None
    issued_total: int
    dispatched_total: int
    failure_total: int
    last_issued_at: float | None


_Result = TypeVar("_Result")


class RequestGate:
    """使用单个 SQLite 文件协调多个线程/进程的保守请求预算。

    max_requests 和 window_seconds 必须由已获批准的调用方显式提供；
    此处不预设任何真实站点的审批阈值。不同进程重开相同库必须使用
    完全一致的限额，reset 不清除时间窗内已发许可。
    """

    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        max_requests: int,
        window_seconds: float,
        min_interval_seconds: float = 2.0,
        clock: Clock | None = None,
        busy_timeout_seconds: float = 30.0,
    ) -> None:
        if not isinstance(max_requests, int) or isinstance(max_requests, bool) or max_requests < 1:
            raise ValueError("max_requests must be a positive integer")
        for name, number, allow_zero in (
            ("window_seconds", window_seconds, False),
            ("min_interval_seconds", min_interval_seconds, True),
            ("busy_timeout_seconds", busy_timeout_seconds, False),
        ):
            if isinstance(number, bool) or not isinstance(number, (float, int)):
                raise ValueError(f"{name} must be a finite number")
            if not math.isfinite(number) or (number < 0 if allow_zero else number <= 0):
                raise ValueError(f"{name} must be {'nonnegative' if allow_zero else 'positive'}")
        path: str = os.fspath(db_path)
        if (not path or path == ":memory:" or path.startswith("file:") or
                "\x00" in path):
            raise ValueError("db_path must be a persistent local filesystem path")
        if not os.path.isdir(os.path.dirname(os.path.abspath(path))):
            raise ValueError("db_path parent directory must already exist")
        # 先按运行期一致的真实路径基准落定协调文件，再对其执行 symlink
        # 检查：若沿用 abspath 基准，含符号链接祖先目录的 path 会使
        # 检查恒为 False，防御被绕过。保留对原始 path 本身的检查。
        self.db_path: str = os.path.normcase(os.path.realpath(path))
        self.lock_path: str = self.db_path + ".request.lock"
        self.inflight_path: str = self.db_path + ".request.inflight"
        if (os.path.islink(path) or os.path.islink(self.db_path) or
                os.path.islink(self.lock_path) or
                os.path.islink(self.inflight_path)):
            raise ValueError("gate database and coordination files must not be symlinks")
        self.max_requests: int = max_requests
        self.window_seconds: float = float(window_seconds)
        self.min_interval_seconds: float = float(min_interval_seconds)
        self.busy_timeout_seconds: float = float(busy_timeout_seconds)
        self.clock: Clock = clock if clock is not None else SystemClock()
        with self._round_lock(allow_inflight=True):
            self._initialize()

    def _now(self) -> float:
        current: float = float(self.clock.now())
        if not math.isfinite(current):
            raise ValueError("clock must return a finite Unix timestamp")
        return current

    def _connect(self) -> sqlite3.Connection:
        connection: sqlite3.Connection = sqlite3.connect(
            self.db_path, timeout=self.busy_timeout_seconds, isolation_level=None
        )
        connection.execute(f"PRAGMA busy_timeout={int(self.busy_timeout_seconds * 1000)}")
        return connection

    @contextmanager
    def _round_lock(self, *, allow_inflight: bool = False) -> Iterator[None]:
        """锁定同一本地数据库的物理请求全段，异常和进程退出自动解锁。"""
        try:
            descriptor: int = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        except OSError as error:
            raise GateDenied("request coordination lock unavailable") from error
        locked: bool = False
        try:
            deadline: float = time.monotonic() + self.busy_timeout_seconds
            while not locked:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    locked = True
                except OSError as error:
                    retryable = error.errno in (errno.EAGAIN, errno.EACCES)
                    if not retryable:
                        raise GateDenied("request coordination lock failed") from error
                    if time.monotonic() >= deadline:
                        raise GateDenied("request coordination lock timed out") from error
                    time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
            # 若上一进程发送后在持久停轮前崩溃，不能猜测其响应是否
            # 为 403：在任何后继发送前保守停轮并要求人工核准复位。
            if not allow_inflight:
                connection = self._connect()
                try:
                    self._check_inflight_integrity(connection)
                finally:
                    connection.close()
            yield
        finally:
            if locked:
                try:
                    if os.name == "nt":
                        import msvcrt
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(descriptor, fcntl.LOCK_UN)
                except OSError as error:
                    raise GateDenied("request coordination unlock failed") from error
                finally:
                    os.close(descriptor)
            else:
                os.close(descriptor)

    def _inflight_present(self) -> bool:
        """文件检查失败时一律拒绝新发送，而非视作没有悬置请求。"""
        try:
            os.stat(self.inflight_path)
            return True
        except FileNotFoundError:
            if not os.path.isdir(os.path.dirname(self.inflight_path)):
                raise GateDenied("in-flight marker directory unavailable")
            return False
        except OSError as error:
            raise GateDenied("in-flight marker state unavailable") from error

    def _inflight_start(self) -> None:
        """在 send 前 fsync 悬置标识，崩溃后禁止后继物理发送。"""
        flags: int = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        try:
            descriptor: int = os.open(self.inflight_path, flags, 0o600)
            try:
                os.write(descriptor, b"request in flight\n")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise GateDenied("cannot persist in-flight request marker") from error

    def _inflight_finish(self) -> None:
        """仅在响应安全分类或持久停轮提交之后清除悬置标识。"""
        os.unlink(self.inflight_path)

    def _check_inflight_integrity(self, connection: sqlite3.Connection) -> None:
        """比对 SQLite 持久悬置位与旁路 fsync 标识，任一不一致即拒发。"""
        row = connection.execute(
            "SELECT active FROM gate_inflight WHERE id=1"
        ).fetchone()
        if row is None or row[0] not in (0, 1) or bool(row[0]) != self._inflight_present():
            raise GateDenied("in-flight coordination state inconsistent")
        if row[0]:
            raise GateDenied("unresolved in-flight request; manual review required")

    def _initialize(self) -> None:
        connection: sqlite3.Connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gate_state ("
                "id INTEGER PRIMARY KEY CHECK(id=1), gate_id TEXT NOT NULL, "
                "max_requests INTEGER NOT NULL, window_seconds REAL NOT NULL, "
                "min_interval_seconds REAL NOT NULL, generation INTEGER NOT NULL, "
                "stopped INTEGER NOT NULL, stop_reason TEXT, last_issued_at REAL, "
                "issued_total INTEGER NOT NULL, dispatched_total INTEGER NOT NULL, "
                "failure_total INTEGER NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gate_permits ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, gate_id TEXT NOT NULL, "
                "generation INTEGER NOT NULL, issued_at REAL NOT NULL, "
                "dispatched_at REAL)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_gate_permits_issued "
                "ON gate_permits(issued_at)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gate_inflight ("
                "id INTEGER PRIMARY KEY CHECK(id=1), active INTEGER NOT NULL "
                "CHECK(active IN (0, 1)))"
            )
            connection.execute("INSERT OR IGNORE INTO gate_inflight VALUES (1, 0)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS gate_audit ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, occurred_at REAL NOT NULL, "
                "event TEXT NOT NULL, generation INTEGER NOT NULL, "
                "permit_id INTEGER, detail TEXT NOT NULL, actor TEXT NOT NULL)"
            )
            existing = connection.execute(
                "SELECT max_requests, window_seconds, min_interval_seconds "
                "FROM gate_state WHERE id=1"
            ).fetchone()
            if existing is None:
                connection.execute(
                    "INSERT INTO gate_state VALUES (1, ?, ?, ?, ?, 0, 0, NULL, NULL, 0, 0, 0)",
                    (str(uuid4()), self.max_requests, self.window_seconds,
                     self.min_interval_seconds),
                )
            elif existing != (
                self.max_requests, self.window_seconds, self.min_interval_seconds
            ):
                raise ValueError("existing gate budget differs; changing limits requires separate approval")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _state(connection: sqlite3.Connection) -> tuple:
        result = connection.execute(
            "SELECT gate_id, generation, stopped, stop_reason, last_issued_at, "
            "issued_total, dispatched_total, failure_total FROM gate_state WHERE id=1"
        ).fetchone()
        if result is None:
            raise RuntimeError("gate state missing")
        return result

    @staticmethod
    def _audit(
        connection: sqlite3.Connection, at: float, event: str, generation: int,
        permit_id: int | None = None, detail: str = "", actor: str = "system",
    ) -> None:
        connection.execute(
            "INSERT INTO gate_audit (occurred_at, event, generation, permit_id, detail, actor) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (at, event, generation, permit_id, detail, actor),
        )

    @staticmethod
    def _required_text(value: str, name: str, length: int = 256) -> str:
        if not isinstance(value, str) or not value.strip() or len(value) > length:
            raise ValueError(f"{name} must be nonempty and at most {length} characters")
        return value.strip()

    def _reserve(self) -> Permit:
        """内部发放持久许可；绝不把许可本身当作独立发送授权。"""
        current: float = self._now()
        connection: sqlite3.Connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            gate_id, generation, stopped, _, last_at, _, _, _ = self._state(connection)
            if stopped:
                raise GateDenied("request round stopped; manual reset required")
            # 回拨时旧许可继续占预算，且间隔不会因负时间差而放行。
            count: int = connection.execute(
                "SELECT COUNT(*) FROM gate_permits WHERE issued_at > ?",
                (current - self.window_seconds,),
            ).fetchone()[0]
            if count >= self.max_requests:
                raise GateDenied("request budget exhausted")
            if last_at is not None and current - last_at < self.min_interval_seconds:
                raise GateDenied("minimum request interval not elapsed")
            cursor = connection.execute(
                "INSERT INTO gate_permits (gate_id, generation, issued_at) VALUES (?, ?, ?)",
                (gate_id, generation, current),
            )
            permit_id: int = int(cursor.lastrowid)
            connection.execute(
                "UPDATE gate_state SET last_issued_at=?, issued_total=issued_total+1 WHERE id=1",
                (current,),
            )
            self._audit(connection, current, "permit_issued", generation, permit_id)
            connection.commit()
            return Permit(gate_id, permit_id, generation, current)
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _check_permit(self, connection: sqlite3.Connection, permit: Permit) -> None:
        if not isinstance(permit, Permit):
            raise GateDenied("invalid permit")
        gate_id, generation, stopped, _, _, _, _, _ = self._state(connection)
        row = connection.execute(
            "SELECT gate_id, generation, issued_at, dispatched_at "
            "FROM gate_permits WHERE id=?", (permit.permit_id,)
        ).fetchone()
        if (stopped or permit.gate_id != gate_id or permit.generation != generation
                or row is None or row != (gate_id, generation, permit.issued_at, None)):
            raise GateDenied("permit revoked, foreign, or already dispatched")

    def _mark_dispatched(self, permit: Permit) -> None:
        """发送之前独立 COMMIT；进程崩溃后同一许可仍不可再次使用。"""
        connection: sqlite3.Connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._check_permit(connection, permit)
            at: float = self._now()
            connection.execute(
                "UPDATE gate_permits SET dispatched_at=? WHERE id=?",
                (at, permit.permit_id),
            )
            connection.execute(
                "UPDATE gate_state SET dispatched_total=dispatched_total+1 WHERE id=1"
            )
            connection.execute("UPDATE gate_inflight SET active=1 WHERE id=1")
            self._audit(connection, at, "permit_dispatched", permit.generation,
                        permit.permit_id)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _clear_inflight(self) -> None:
        """安全分类之后先清文件，最后持久清 DB；故障仍不允许放行。"""
        self._inflight_finish()
        connection: sqlite3.Connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute("UPDATE gate_inflight SET active=0 WHERE id=1")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _trip_for_permit(self, permit: Permit, reason: str) -> None:
        """迟到的旧轮响应不得停掉人工 reset 后的新轮。"""
        detail: str = self._required_text(reason, "reason")
        connection: sqlite3.Connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            gate_id, generation, stopped, _, _, _, _, _ = self._state(connection)
            if gate_id == permit.gate_id and generation == permit.generation and not stopped:
                connection.execute(
                    "UPDATE gate_state SET stopped=1, generation=generation+1, "
                    "stop_reason=? WHERE id=1", (detail,)
                )
                self._audit(connection, self._now(), "round_stopped",
                            generation + 1, permit.permit_id, detail)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def execute_once(
        self, send_once: Callable[[], _Result], *,
        status_code: Callable[[_Result], int],
        failure_reason: Callable[[_Result], str | None] | None = None,
    ) -> _Result:
        """唯一安全发送入口：占额提交→发送标记提交→单次发送→403 停轮。

        status_code 必须从原始响应提取整数状态码，不能在回调里隐式重试。
        failure_reason 可记 401/token_invalid、429、5xx 等响应失败，不会退款。
        若分类器/发送异常，保守停轮并原样传播；持久化失败保留悬置标识。
        """
        if (not callable(send_once) or not callable(status_code) or
                (failure_reason is not None and not callable(failure_reason))):
            raise TypeError("send_once, status_code and optional failure_reason must be callable")
        with self._round_lock():
            permit: Permit = self._reserve()
            self._inflight_start()
            self._mark_dispatched(permit)
            try:
                result: _Result = send_once()
            except BaseException as error:
                self._trip_for_permit(permit, "transport_exception")
                self._record_failure(type(error).__name__, permit)
                self._clear_inflight()
                raise
            try:
                status: int = status_code(result)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise ValueError("status_code must return an integer")
            except BaseException:
                self._trip_for_permit(permit, "response_classification_failed")
                self._record_failure("response_classification_failed", permit)
                self._clear_inflight()
                raise
            if status == 403:
                self._trip_for_permit(permit, "403")
                self._record_failure("403", permit)
            else:
                try:
                    reason: str | None = (
                        failure_reason(result) if failure_reason is not None else None
                    )
                    if reason is not None:
                        reason = self._required_text(reason, "failure_reason")
                except BaseException:
                    self._trip_for_permit(permit, "response_classification_failed")
                    self._record_failure("response_classification_failed", permit)
                    self._clear_inflight()
                    raise
                if reason is not None:
                    self._record_failure(reason, permit)
            self._clear_inflight()
            return result

    def _record_failure(self, reason: str, permit: Permit | None = None) -> None:
        """内部记录 401/token_invalid/429/5xx，不记成功也不退预算。"""
        detail: str = self._required_text(reason, "reason")
        at: float = self._now()
        connection: sqlite3.Connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            gate_id, generation, _, _, _, _, _, _ = self._state(connection)
            permit_id: int | None = None
            if permit is not None:
                if not isinstance(permit, Permit):
                    raise GateDenied("failure must refer to a dispatched local permit")
                row = connection.execute(
                    "SELECT gate_id, generation, issued_at, dispatched_at "
                    "FROM gate_permits WHERE id=?", (permit.permit_id,)
                ).fetchone()
                if (row is None or
                        row[:3] != (gate_id, permit.generation, permit.issued_at) or
                        row[3] is None or permit.gate_id != gate_id):
                    raise GateDenied("failure must refer to a dispatched local permit")
                permit_id = permit.permit_id
            connection.execute(
                "UPDATE gate_state SET failure_total=failure_total+1 WHERE id=1"
            )
            self._audit(connection, at, "request_failure", generation,
                        permit_id, detail)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def trip(self, reason: str) -> None:
        """管理性持久停轮，不作为响应 403 的事后替代品。"""
        detail: str = self._required_text(reason, "reason")
        with self._round_lock():
            connection: sqlite3.Connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                _, generation, stopped, _, _, _, _, _ = self._state(connection)
                if not stopped:
                    generation += 1
                    connection.execute(
                        "UPDATE gate_state SET stopped=1, generation=?, stop_reason=? WHERE id=1",
                        (generation, detail),
                    )
                    self._audit(connection, self._now(), "round_stopped", generation,
                                detail=detail)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def reconcile_inflight(self, approved_by: str, reason: str) -> None:
        """仅人工审查悬置请求后停轮并归档标识，绝不自动复网。"""
        actor: str = self._required_text(approved_by, "approved_by", 128)
        detail: str = self._required_text(reason, "reason")
        with self._round_lock(allow_inflight=True):
            connection: sqlite3.Connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT active FROM gate_inflight WHERE id=1").fetchone()
                if row is None or (not row[0] and not self._inflight_present()):
                    raise ValueError("no unresolved in-flight request")
                _, generation, stopped, _, _, _, _, _ = self._state(connection)
                if not stopped:
                    generation += 1
                    connection.execute(
                        "UPDATE gate_state SET stopped=1, generation=?, stop_reason=? WHERE id=1",
                        (generation, "unresolved_inflight"),
                    )
                self._audit(connection, self._now(), "manual_inflight_review",
                            generation, detail=detail, actor=actor)
                connection.commit()
                if self._inflight_present():
                    self._clear_inflight()
                else:
                    connection.execute("BEGIN IMMEDIATE")
                    connection.execute("UPDATE gate_inflight SET active=0 WHERE id=1")
                    connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def reset(self, approved_by: str, reason: str) -> None:
        """人工核准复位；保留历史预算，崩溃悬置标识禁止直接复位。"""
        actor: str = self._required_text(approved_by, "approved_by", 128)
        detail: str = self._required_text(reason, "reason")
        with self._round_lock(allow_inflight=True):
            connection: sqlite3.Connection = self._connect()
            try:
                self._check_inflight_integrity(connection)
            finally:
                connection.close()
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                _, generation, stopped, _, _, _, _, _ = self._state(connection)
                if not stopped:
                    raise ValueError("only a stopped round may be reset")
                generation += 1
                connection.execute(
                    "UPDATE gate_state SET stopped=0, generation=?, stop_reason=NULL WHERE id=1",
                    (generation,),
                )
                self._audit(connection, self._now(), "manual_reset", generation,
                            detail=detail, actor=actor)
                connection.commit()
            except BaseException:
                connection.rollback()
                raise
            finally:
                connection.close()

    def snapshot(self) -> GateSnapshot:
        """返回当前持久状态，不根据冷却时间自动解除停轮。"""
        connection: sqlite3.Connection = self._connect()
        try:
            gate_id, generation, stopped, reason, last_at, issued, dispatched, failures = (
                self._state(connection)
            )
            return GateSnapshot(gate_id, generation, bool(stopped), reason,
                                issued, dispatched, failures, last_at)
        finally:
            connection.close()
