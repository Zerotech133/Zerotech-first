# -*- coding: utf-8 -*-
"""仅用临时 SQLite 文件和假发送回调测试雪球请求许可，不访问网络。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import multiprocessing
import os
from pathlib import Path
import sqlite3
import sys
import threading

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xq_request_gate import GateDenied, RequestGate  # noqa: E402


class FakeClock:
    """确定性地模拟多个请求共享时间基准。"""

    def __init__(self, current: float = 1000.0) -> None:
        self.current: float = current
        self.lock: threading.Lock = threading.Lock()

    def now(self) -> float:
        with self.lock:
            return self.current

    def advance(self, seconds: float) -> None:
        with self.lock:
            self.current += seconds


def _process_acquire(path: str, start: multiprocessing.synchronize.Event,
                     results: multiprocessing.queues.Queue) -> None:
    """顶层函数使 Windows spawn 能安全加载，不引入任何采集器。"""
    try:
        gate = RequestGate(path, max_requests=7, window_seconds=60,
                           min_interval_seconds=0, clock=FakeClock())
        start.wait(timeout=15)
        gate.execute_once(lambda: 200, status_code=lambda result: result)
        results.put("issued")
    except GateDenied:
        results.put("denied")
    except BaseException as error:
        results.put(f"error:{type(error).__name__}:{error}")


def _crash_after_send_started(path: str, sent: multiprocessing.queues.Queue) -> None:
    """物理发送已经开始后硬退出；无 finally、无响应分类。"""
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())

    def send() -> int:
        sent.put("physical-send")
        sent.close()
        sent.join_thread()
        os._exit(37)

    gate.execute_once(send, status_code=lambda result: result)


def _crash_before_send(path: str) -> None:
    """发放许可已 durable commit，进入发送标记阶段前硬退出。"""
    gate = RequestGate(path, max_requests=1, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    gate._inflight_start = lambda: os._exit(38)  # 内部阶段定点故障注入
    gate.execute_once(lambda: 200, status_code=lambda result: result)


def _crash_after_dispatched_commit(path: str) -> None:
    """崩溃发生于发送标记已提交、物理发送开始前。"""
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    original = gate._mark_dispatched

    def mark_then_crash(permit) -> None:
        original(permit)
        os._exit(39)

    gate._mark_dispatched = mark_then_crash
    gate.execute_once(lambda: 200, status_code=lambda result: result)


def test_thread_contention_never_exceeds_shared_budget(tmp_path: Path) -> None:
    """不同实例/线程并发争抢同一事务预算。"""
    path: Path = tmp_path / "thread_gate.sqlite"
    clock: FakeClock = FakeClock()
    gates = [RequestGate(path, max_requests=9, window_seconds=60,
                         min_interval_seconds=0, clock=clock) for _ in range(5)]

    def acquire(index: int) -> bool:
        try:
            gates[index % len(gates)].execute_once(lambda: 200,
                                                  status_code=lambda result: result)
            return True
        except GateDenied:
            return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        outcomes = list(executor.map(acquire, range(60)))
    assert sum(outcomes) == 9
    assert gates[0].snapshot().issued_total == 9


def test_spawned_processes_never_exceed_budget(tmp_path: Path) -> None:
    """spawn 多进程测试覆盖 Windows 与类 Unix 的独立进程文件锁。"""
    path: Path = tmp_path / "process_gate.sqlite"
    gate = RequestGate(path, max_requests=7, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    context = multiprocessing.get_context("spawn")
    start = context.Event()
    results = context.Queue()
    processes = [context.Process(target=_process_acquire,
                                 args=(str(path), start, results)) for _ in range(12)]
    for process in processes:
        process.start()
    start.set()
    outcomes = [results.get(timeout=30) for _ in processes]
    for process in processes:
        process.join(timeout=30)
        assert process.exitcode == 0
    assert outcomes.count("issued") == 7, outcomes
    assert outcomes.count("denied") == 5, outcomes
    assert gate.snapshot().issued_total == 7


def test_crash_after_physical_send_never_replays_or_unblocks(tmp_path: Path) -> None:
    """硬退出时 OS 锁自动释放，但悬置请求阻断任何后继发送。"""
    path: Path = tmp_path / "crash_during_send.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    context = multiprocessing.get_context("spawn")
    sent = context.Queue()
    child = context.Process(target=_crash_after_send_started, args=(str(path), sent))
    child.start()
    assert sent.get(timeout=15) == "physical-send"
    child.join(timeout=15)
    assert child.exitcode == 37
    reopened = RequestGate(path, max_requests=2, window_seconds=60,
                           min_interval_seconds=0, clock=FakeClock())
    assert reopened.snapshot().issued_total == 1
    assert reopened.snapshot().dispatched_total == 1
    with pytest.raises(GateDenied, match="unresolved"):
        reopened.execute_once(lambda: pytest.fail("no replay"),
                              status_code=lambda result: result)
    with pytest.raises(GateDenied, match="unresolved"):
        reopened.reset("operator", "do not infer prior status")
    reopened.reconcile_inflight("operator", "unknown status; quarantine round")
    assert reopened.snapshot().stopped
    with pytest.raises(GateDenied, match="stopped"):
        reopened.execute_once(lambda: pytest.fail("no second send"),
                              status_code=lambda result: result)


def test_crash_after_dispatched_commit_cannot_reuse_permit(tmp_path: Path) -> None:
    path: Path = tmp_path / "crash_after_dispatch_commit.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    context = multiprocessing.get_context("spawn")
    child = context.Process(target=_crash_after_dispatched_commit, args=(str(path),))
    child.start()
    child.join(timeout=15)
    assert child.exitcode == 39
    assert gate.snapshot().issued_total == 1
    assert gate.snapshot().dispatched_total == 1
    with sqlite3.connect(path) as connection:
        old = connection.execute("SELECT id, dispatched_at FROM gate_permits").fetchone()
    assert old[1] is not None
    with pytest.raises(GateDenied, match="unresolved"):
        gate.execute_once(lambda: pytest.fail("old permit cannot replay"),
                          status_code=lambda result: result)


def test_crash_after_reservation_does_not_return_budget(tmp_path: Path) -> None:
    path: Path = tmp_path / "crash_before_send.sqlite"
    gate = RequestGate(path, max_requests=1, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    context = multiprocessing.get_context("spawn")
    child = context.Process(target=_crash_before_send, args=(str(path),))
    child.start()
    child.join(timeout=15)
    assert child.exitcode == 38
    assert gate.snapshot().issued_total == 1
    assert gate.snapshot().dispatched_total == 0
    with pytest.raises(GateDenied, match="budget"):
        gate.execute_once(lambda: pytest.fail("permit must not be returned"),
                          status_code=lambda result: result)


def test_spacing_window_and_wall_clock_rollback_are_conservative(tmp_path: Path) -> None:
    clock: FakeClock = FakeClock()
    gate = RequestGate(tmp_path / "spacing.sqlite", max_requests=2,
                       window_seconds=60, min_interval_seconds=3, clock=clock)
    run = lambda: gate.execute_once(lambda: 200, status_code=lambda code: code)
    run()
    with pytest.raises(GateDenied, match="interval"):
        run()
    clock.advance(3)
    run()
    clock.advance(3)
    with pytest.raises(GateDenied, match="budget"):
        run()
    clock.advance(-30)
    with pytest.raises(GateDenied):
        run()
    clock.advance(91)
    run()
    assert gate.snapshot().issued_total == 3


def test_trip_revokes_issued_permits_and_persists_across_restart(tmp_path: Path) -> None:
    path: Path = tmp_path / "stop.sqlite"
    gate = RequestGate(path, max_requests=4, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    gate.trip("403")
    gate.trip("403 again")
    restarted = RequestGate(path, max_requests=4, window_seconds=60,
                            min_interval_seconds=0, clock=FakeClock(99999))
    assert restarted.snapshot().stopped
    assert restarted.snapshot().stop_reason == "403"
    with pytest.raises(GateDenied, match="stopped"):
        restarted.execute_once(lambda: pytest.fail("must not send"),
                               status_code=lambda result: result)
    assert restarted.snapshot().issued_total == 0
    assert restarted.snapshot().dispatched_total == 0


def test_each_retry_requires_fresh_permit_and_failure_stays_counted(tmp_path: Path) -> None:
    clock: FakeClock = FakeClock()
    gate = RequestGate(tmp_path / "retry.sqlite", max_requests=2,
                       window_seconds=60, min_interval_seconds=0, clock=clock)
    calls: list[int] = []

    def fake_send(code: int) -> int:
        calls.append(code)
        return code

    classify = lambda code: str(code) if code != 200 else None
    assert gate.execute_once(lambda: fake_send(429), status_code=lambda result: result,
                             failure_reason=classify) == 429
    assert gate.execute_once(lambda: fake_send(503), status_code=lambda result: result,
                             failure_reason=classify) == 503
    assert (gate.snapshot().issued_total, gate.snapshot().dispatched_total,
            gate.snapshot().failure_total) == (2, 2, 2)
    assert calls == [429, 503]
    with pytest.raises(GateDenied, match="budget"):
        gate.execute_once(lambda: pytest.fail("must not send"),
                          status_code=lambda result: result)
    with pytest.raises(GateDenied, match="budget"):
        gate.execute_once(lambda: pytest.fail("must not resend"),
                          status_code=lambda result: result)


def test_exception_consumes_permit_without_fabricating_success(tmp_path: Path) -> None:
    gate = RequestGate(tmp_path / "exception.sqlite", max_requests=1,
                       window_seconds=60, min_interval_seconds=0,
                       clock=FakeClock())
    def fail() -> None:
        raise TimeoutError("offline simulated transport failure")

    with pytest.raises(TimeoutError):
        gate.execute_once(fail, status_code=lambda result: result)
    assert (gate.snapshot().issued_total, gate.snapshot().dispatched_total,
            gate.snapshot().failure_total) == (1, 1, 1)
    assert gate.snapshot().stopped
    with pytest.raises(GateDenied):
        gate.execute_once(lambda: pytest.fail("must not send"),
                          status_code=lambda result: result)


def test_trip_waits_for_in_flight_send_and_blocks_new_dispatch(tmp_path: Path) -> None:
    path: Path = tmp_path / "race.sqlite"
    gate = RequestGate(path, max_requests=3, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    sending = threading.Event()
    release = threading.Event()
    stopped = threading.Event()
    def send() -> str:
        sending.set()
        assert release.wait(timeout=5)
        return "sent"

    send_thread = threading.Thread(
        target=lambda: gate.execute_once(send, status_code=lambda result: 200))
    send_thread.start()
    assert sending.wait(timeout=5)
    trip_thread = threading.Thread(target=lambda: (gate.trip("403"), stopped.set()))
    trip_thread.start()
    assert not stopped.wait(timeout=0.05)
    release.set()
    send_thread.join(timeout=5)
    trip_thread.join(timeout=5)
    assert not send_thread.is_alive() and stopped.is_set()
    with pytest.raises(GateDenied, match="stopped"):
        gate.execute_once(lambda: pytest.fail("must not send"),
                          status_code=lambda result: result)
    assert gate.snapshot().dispatched_total == 1


def test_403_paused_response_blocks_waiting_thread_before_send(tmp_path: Path) -> None:
    path: Path = tmp_path / "403_queue.sqlite"
    gate_a = RequestGate(path, max_requests=2, window_seconds=60,
                         min_interval_seconds=0, clock=FakeClock())
    gate_b = RequestGate(path, max_requests=2, window_seconds=60,
                         min_interval_seconds=0, clock=FakeClock())
    entered = threading.Event()
    release = threading.Event()
    b_finished = threading.Event()
    physical: list[str] = []
    errors: list[BaseException] = []

    def first_send() -> int:
        physical.append("A")
        entered.set()
        assert release.wait(timeout=5)
        return 403

    def second_send() -> int:
        physical.append("B")
        return 200

    def wait_for_gate() -> None:
        try:
            gate_b.execute_once(second_send, status_code=lambda code: code)
        except BaseException as error:
            errors.append(error)
        finally:
            b_finished.set()

    a = threading.Thread(target=lambda: gate_a.execute_once(
        first_send, status_code=lambda code: code))
    a.start()
    assert entered.wait(timeout=5)
    b = threading.Thread(target=wait_for_gate)
    b.start()
    assert not b_finished.wait(timeout=0.05)
    release.set()
    a.join(timeout=5)
    b.join(timeout=5)
    assert not a.is_alive() and not b.is_alive()
    assert physical == ["A"]
    assert len(errors) == 1 and isinstance(errors[0], GateDenied)
    assert gate_a.snapshot().stopped


def test_403_classification_stops_before_dispatch_lock_release(tmp_path: Path) -> None:
    gate = RequestGate(tmp_path / "403.sqlite", max_requests=3,
                       window_seconds=60, min_interval_seconds=0,
                       clock=FakeClock())
    assert gate.execute_once(lambda: 403, status_code=lambda result: result) == 403
    assert gate.snapshot().stopped
    with pytest.raises(GateDenied, match="stopped"):
        gate.execute_once(lambda: pytest.fail("must not dispatch"),
                          status_code=lambda result: result)


def test_trip_persistence_failure_prevents_next_send(tmp_path: Path) -> None:
    path: Path = tmp_path / "trip_fail.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())

    def broken_trip(permit, reason):
        raise sqlite3.OperationalError("offline injected disk failure")

    gate._trip_for_permit = broken_trip
    with pytest.raises(sqlite3.OperationalError, match="disk failure"):
        gate.execute_once(lambda: 403, status_code=lambda result: result)
    assert gate.snapshot().dispatched_total == 1
    reopened = RequestGate(path, max_requests=2, window_seconds=60,
                           min_interval_seconds=0, clock=FakeClock())
    with pytest.raises(GateDenied, match="unresolved"):
        reopened.execute_once(lambda: pytest.fail("no send on failed trip"),
                              status_code=lambda result: result)


def test_deleted_inflight_marker_does_not_bypass_db_stop(tmp_path: Path) -> None:
    path: Path = tmp_path / "deleted_marker.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    gate._trip_for_permit = lambda permit, reason: (_ for _ in ()).throw(
        sqlite3.OperationalError("offline trip failure"))
    with pytest.raises(sqlite3.OperationalError):
        gate.execute_once(lambda: 403, status_code=lambda result: result)
    os.unlink(gate.inflight_path)
    reopened = RequestGate(path, max_requests=2, window_seconds=60,
                           min_interval_seconds=0, clock=FakeClock())
    with pytest.raises(GateDenied, match="inconsistent"):
        reopened.execute_once(lambda: pytest.fail("must not send"),
                              status_code=lambda result: result)
    reopened.reconcile_inflight("operator", "manual audit of inconsistent marker")
    assert reopened.snapshot().stopped


def test_classifier_failure_closes_gate_without_undoing_dispatch(tmp_path: Path) -> None:
    gate = RequestGate(tmp_path / "classifier.sqlite", max_requests=2,
                       window_seconds=60, min_interval_seconds=0,
                       clock=FakeClock())
    def broken_classifier(status: int) -> str:
        raise ValueError("unparseable offline response")

    with pytest.raises(ValueError, match="unparseable"):
        gate.execute_once(lambda: 200, status_code=broken_classifier)
    assert gate.snapshot().stopped
    assert gate.snapshot().dispatched_total == 1
    assert gate.snapshot().failure_total == 1
    assert gate.snapshot().stop_reason == "response_classification_failed"


@pytest.mark.parametrize("classifier_result", ["", "x" * 257, 404])
def test_failure_reason_invalid_result_stops_and_records_once(
    tmp_path: Path, classifier_result: object,
) -> None:
    """失败原因不可分类时仍占额、停轮并记一次失败，不允许再发送。"""
    path: Path = tmp_path / "invalid_failure_reason.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    physical_sends: list[int] = []

    with pytest.raises(ValueError, match="failure_reason"):
        gate.execute_once(
            lambda: physical_sends.append(200) or 200,
            status_code=lambda response: response,
            failure_reason=lambda response: classifier_result,
        )
    snapshot = gate.snapshot()
    assert physical_sends == [200]
    assert (snapshot.issued_total, snapshot.dispatched_total,
            snapshot.failure_total) == (1, 1, 1)
    assert snapshot.stopped and snapshot.stop_reason == "response_classification_failed"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT active FROM gate_inflight WHERE id=1").fetchone() == (0,)
        assert connection.execute(
            "SELECT detail FROM gate_audit WHERE event='request_failure'"
        ).fetchall() == [("response_classification_failed",)]
    assert not os.path.exists(gate.inflight_path)
    reopened = RequestGate(path, max_requests=2, window_seconds=60,
                           min_interval_seconds=0, clock=FakeClock())
    with pytest.raises(GateDenied, match="stopped"):
        reopened.execute_once(lambda: pytest.fail("no second physical send"),
                              status_code=lambda response: response)


def test_failure_reason_exception_stops_and_records_once(tmp_path: Path) -> None:
    """分类器异常原样抛出；持久停轮与失败账先落盘。"""
    path: Path = tmp_path / "failure_reason_exception.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    physical_sends: list[int] = []

    def broken_classifier(response: int) -> str:
        raise LookupError("offline classifier failed")

    with pytest.raises(LookupError, match="offline classifier failed"):
        gate.execute_once(
            lambda: physical_sends.append(503) or 503,
            status_code=lambda response: response,
            failure_reason=broken_classifier,
        )
    snapshot = gate.snapshot()
    assert physical_sends == [503]
    assert (snapshot.issued_total, snapshot.dispatched_total,
            snapshot.failure_total) == (1, 1, 1)
    assert snapshot.stopped and snapshot.stop_reason == "response_classification_failed"
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT active FROM gate_inflight WHERE id=1").fetchone() == (0,)
    assert not os.path.exists(gate.inflight_path)
    with pytest.raises(GateDenied, match="stopped"):
        gate.execute_once(lambda: pytest.fail("no second physical send"),
                          status_code=lambda response: response)


def test_failure_record_persistence_error_keeps_inflight_marker(tmp_path: Path) -> None:
    """真实失败账写失败不能误清悬置，也不能再尝试伪造分类失败账。"""
    path: Path = tmp_path / "failure_record_error.sqlite"
    gate = RequestGate(path, max_requests=2, window_seconds=60,
                       min_interval_seconds=0, clock=FakeClock())
    physical_sends: list[int] = []
    record_calls: list[str] = []

    def broken_record(reason: str, permit: object) -> None:
        record_calls.append(reason)
        raise sqlite3.OperationalError("offline failure journal unavailable")

    gate._record_failure = broken_record
    with pytest.raises(sqlite3.OperationalError, match="journal unavailable"):
        gate.execute_once(
            lambda: physical_sends.append(429) or 429,
            status_code=lambda response: response,
            failure_reason=lambda response: "429",
        )
    assert physical_sends == [429]
    assert record_calls == ["429"]
    assert os.path.exists(gate.inflight_path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT active FROM gate_inflight WHERE id=1").fetchone() == (1,)
    reopened = RequestGate(path, max_requests=2, window_seconds=60,
                           min_interval_seconds=0, clock=FakeClock())
    assert (reopened.snapshot().issued_total, reopened.snapshot().dispatched_total,
            reopened.snapshot().failure_total) == (1, 1, 0)
    with pytest.raises(GateDenied, match="unresolved"):
        reopened.execute_once(lambda: pytest.fail("no send after journal failure"),
                              status_code=lambda response: response)


def test_reset_requires_explicit_actor_and_retains_audit_and_budget(tmp_path: Path) -> None:
    path: Path = tmp_path / "audit.sqlite"
    clock: FakeClock = FakeClock()
    gate = RequestGate(path, max_requests=1, window_seconds=60,
                       min_interval_seconds=0, clock=clock)
    assert gate.execute_once(lambda: 200, status_code=lambda code: code) == 200
    gate.trip("403 IP blocked")
    with pytest.raises(ValueError, match="approved_by"):
        gate.reset(" ", "approved")
    assert gate.snapshot().stopped
    gate.reset("human-operator", "written authorization reference")
    assert not gate.snapshot().stopped
    with pytest.raises(GateDenied, match="budget"):
        gate.execute_once(lambda: pytest.fail("must not send"),
                          status_code=lambda code: code)
    with sqlite3.connect(path) as connection:
        audit = connection.execute(
            "SELECT event, detail, actor FROM gate_audit ORDER BY id"
        ).fetchall()
    assert audit == [
        ("permit_issued", "", "system"),
        ("permit_dispatched", "", "system"),
        ("round_stopped", "403 IP blocked", "system"),
        ("manual_reset", "written authorization reference", "human-operator"),
    ]
    reopened = RequestGate(path, max_requests=1, window_seconds=60,
                           min_interval_seconds=0, clock=clock)
    assert reopened.snapshot().generation == 2
    with pytest.raises(ValueError, match="existing gate budget"):
        RequestGate(path, max_requests=2, window_seconds=60, clock=clock)
    clock.advance(61)
    reopened.execute_once(lambda: 200, status_code=lambda code: code)
    assert reopened.snapshot().issued_total == 2


def test_reject_nonpersistent_or_invalid_configuration(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="persistent"):
        RequestGate(":memory:", max_requests=1, window_seconds=60)
    with pytest.raises(ValueError, match="max_requests"):
        RequestGate(tmp_path / "invalid.sqlite", max_requests=0, window_seconds=60)
