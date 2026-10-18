# -*- coding: utf-8 -*-
"""backfill_sprint 纯逻辑测试（协作协议 v1 · 2026-10-18）。

只测锁原子性/窗口判断/让路判定/分片循环编排，全部 mock——
不真跑 LLM、不碰业务库（DB 用 tmp 文件或不触）。
"""
import importlib.util
import os
import time
import unittest
from pathlib import Path
from unittest import mock

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "backfill_sprint_under_test",
    os.path.join(os.path.dirname(_here), "backfill_sprint.py"))
BS = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(BS)


class TestParseWindow(unittest.TestCase):
    def test_normal(self):
        self.assertEqual(BS.parse_window("13:30-17:30"), (810, 1050))

    def test_overnight(self):
        self.assertEqual(BS.parse_window("22:00-02:00"), (1320, 120))

    def test_bad_format(self):
        with self.assertRaises(ValueError):
            BS.parse_window("13:30")
        with self.assertRaises(ValueError):
            BS.parse_window("aa-bb")

    def test_out_of_range(self):
        with self.assertRaises(ValueError):
            BS.parse_window("24:00-25:00")


class TestInWindow(unittest.TestCase):
    _win = BS.parse_window("13:30-17:30")

    def _ts(self, h, m):
        # 用当日固定时刻构造：time.mktime 本地时区
        import datetime as _dt
        base = _dt.datetime.now().replace(hour=h, minute=m, second=0,
                                          microsecond=0)
        return base.timestamp()

    def test_inside(self):
        self.assertTrue(BS.in_window(self._ts(14, 0), self._win))

    def test_outside(self):
        self.assertFalse(BS.in_window(self._ts(12, 0), self._win))
        self.assertFalse(BS.in_window(self._ts(18, 0), self._win))

    def test_edges_half_open(self):
        self.assertTrue(BS.in_window(self._ts(13, 30), self._win))   # start 含
        self.assertFalse(BS.in_window(self._ts(17, 30), self._win))  # end 不含

    def test_overnight_window(self):
        win = BS.parse_window("22:00-02:00")
        self.assertTrue(BS.in_window(self._ts(23, 30), win))
        self.assertTrue(BS.in_window(self._ts(1, 30), win))
        self.assertFalse(BS.in_window(self._ts(12, 0), win))


class TestLock(unittest.TestCase):
    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="bsprint_test_"))
        self.lock = self.tmp / "backfill_sprint.lock"

    def test_atomic_create_and_pid(self):
        self.assertTrue(BS.acquire_lock(self.lock))
        self.assertEqual(BS.read_lock_pid(self.lock), os.getpid())
        self.assertTrue(BS.lock_is_ours(self.lock))

    def test_busy_alive_pid_yields(self):
        BS.acquire_lock(self.lock)
        # 另一个进程视角：pid 是别人且活着（mock 探活恒真）
        with mock.patch.object(BS, "is_pid_alive", return_value=True):
            self.assertFalse(BS.acquire_lock(self.lock))
        # 锁文件未被破坏
        self.assertEqual(BS.read_lock_pid(self.lock), os.getpid())

    def test_stale_takeover(self):
        self.lock.write_text("999999")            # 陈锁：死 pid
        with mock.patch.object(BS, "is_pid_alive", return_value=False):
            self.assertTrue(BS.acquire_lock(self.lock))
        self.assertEqual(BS.read_lock_pid(self.lock), os.getpid())

    def test_release(self):
        BS.acquire_lock(self.lock)
        BS.release_lock(self.lock)
        self.assertFalse(self.lock.exists())
        self.assertFalse(BS.lock_is_ours(self.lock))

    def test_release_foreign_lock_noop(self):
        self.lock.write_text("999999")
        BS.release_lock(self.lock)
        self.assertTrue(self.lock.exists())       # 别人的锁不动


class TestShouldYield(unittest.TestCase):
    def test_startup_fresh_write_yields(self):
        now = time.time()
        self.assertTrue(BS.should_yield(now - 10, None, now))

    def test_startup_stale_write_proceeds(self):
        now = time.time()
        self.assertFalse(BS.should_yield(now - 3600, None, now))

    def test_no_log_file(self):
        self.assertFalse(BS.should_yield(None, None, time.time()))
        self.assertFalse(BS.should_yield(None, 12345.0, time.time()))

    def test_baseline_equal_proceeds(self):
        self.assertFalse(BS.should_yield(1000.0, 1000.0, 2000.0))

    def test_baseline_differs_yields(self):
        # 基线之后有新写入（外部写者）
        self.assertTrue(BS.should_yield(1001.0, 1000.0, 2000.0))


class TestSprintLoop(unittest.TestCase):
    """分片循环编排：全 mock，不碰真库真锁真 LLM。"""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="bsprint_loop_"))
        self._saved = (BS.LOCK_PATH, BS.SPRINT_LOG, BS.STATUS_LOG)
        BS.LOCK_PATH = self.tmp / "lock"
        BS.SPRINT_LOG = self.tmp / "sprint.log"
        BS.STATUS_LOG = self.tmp / "status.log"   # 不存在 → mtime None → 不让路

    def tearDown(self):
        BS.LOCK_PATH, BS.SPRINT_LOG, BS.STATUS_LOG = self._saved

    def test_sharded_loop_until_pending_zero(self):
        calls = []

        def fake_run(shard):
            calls.append(shard)
            return 0

        counts_iter = iter([
            {"source": 10, "classified": 0, "ok": 0, "degraded": 0,
             "pending": 10},   # sprint start
            {"source": 10, "classified": 5, "ok": 5, "degraded": 0,
             "pending": 5},    # shard1 progress
            {"source": 10, "classified": 10, "ok": 10, "degraded": 0,
             "pending": 0},    # shard2 progress → 停
            {"source": 10, "classified": 10, "ok": 10, "degraded": 0,
             "pending": 0},    # sprint final
        ])
        with mock.patch.object(BS.xq_classify, "run", side_effect=fake_run), \
             mock.patch.object(BS, "fetch_counts",
                               side_effect=lambda: next(counts_iter)):
            rc = BS.sprint(limit=2000, win=None, force=True)
        self.assertEqual(rc, BS.RC_OK)
        self.assertEqual(calls, [500, 500])       # 2000 上限但 pending 归零即停
        self.assertFalse(BS.LOCK_PATH.exists())   # finally 释放锁

    def test_limit_respected(self):
        calls = []

        def fake_run(shard):
            calls.append(shard)
            return 0

        counts = {"source": 99999, "classified": 0, "ok": 0, "degraded": 0,
                  "pending": 99999}
        with mock.patch.object(BS.xq_classify, "run", side_effect=fake_run), \
             mock.patch.object(BS, "fetch_counts", side_effect=lambda: counts):
            rc = BS.sprint(limit=1200, win=None, force=True)
        self.assertEqual(rc, BS.RC_OK)
        self.assertEqual(calls, [500, 500, 200])  # 尾片截断到剩余额度

    def test_yield_out_after_three_consecutive(self):
        # status.log 持续被外部写（mtime 单调递增）→ 连续 3 次让路退出 4
        self.tmp.mkdir(exist_ok=True)
        status = self.tmp / "status.log"
        status.write_text("x\n")
        fake_mtime = {"t": status.stat().st_mtime}

        def touch_log(*a, **k):
            fake_mtime["t"] += 10.0               # 每次观测都有新写入
            return fake_mtime["t"]

        sleeps = []
        run_calls = []
        _zero = {"source": 0, "classified": 0, "ok": 0, "degraded": 0,
                 "pending": 0}
        with mock.patch.object(BS.xq_classify, "run",
                               side_effect=lambda n: run_calls.append(n)), \
             mock.patch.object(BS, "fetch_counts",
                               side_effect=lambda: dict(_zero)), \
             mock.patch.object(BS, "status_log_mtime",
                               side_effect=touch_log), \
             mock.patch.object(BS.time, "sleep",
                               side_effect=lambda s: sleeps.append(s)):
            rc = BS.sprint(limit=2000, win=None, force=True)
        self.assertEqual(rc, BS.RC_YIELDED_OUT)
        # 让路 1 → 探测前观察窗 → 让路 2 → 观察窗（第 3 次让路直接退出）
        self.assertEqual(sleeps, [BS.YIELD_SLEEP_SEC, BS.OBSERVE_SEC,
                                  BS.YIELD_SLEEP_SEC, BS.OBSERVE_SEC])
        self.assertEqual(run_calls, [])           # run 一次都没被调（首片前就让路退出）

    def test_yield_then_resume_when_writer_quiets(self):
        # 外部写一次后安静：让路 1 次（等 60s 基线对齐）→ 正常分片
        self.tmp.mkdir(exist_ok=True)
        status = self.tmp / "status.log"
        status.write_text("external\n")
        mtime_holder = {"t": status.stat().st_mtime, "yielded": False}

        def mtime_after_yield():
            # 首次探测：mtime 与"启动基线"不同 → 让路；让路 sleep 后基线
            # 对齐（被 mock 的 sleep 里更新 holder）→ 之后不再变 → 放行
            if not mtime_holder["yielded"]:
                mtime_holder["yielded"] = True
                return mtime_holder["t"] + 1.0    # 有新写入
            return mtime_holder["t"]              # 安静

        calls = []

        def fake_run(shard):
            calls.append(shard)
            return 0

        counts = {"source": 10, "classified": 0, "ok": 0, "degraded": 0,
                  "pending": 10}

        def real_sleep_pause(_s):
            # 模拟让路等待后基线对齐语义由 status_log_mtime 承担
            pass

        with mock.patch.object(BS.xq_classify, "run", side_effect=fake_run), \
             mock.patch.object(BS, "fetch_counts", side_effect=lambda: counts), \
             mock.patch.object(BS, "status_log_mtime",
                               side_effect=mtime_after_yield), \
             mock.patch.object(BS.time, "sleep",
                               side_effect=real_sleep_pause):
            rc = BS.sprint(limit=500, win=None, force=True)
        self.assertEqual(rc, BS.RC_OK)
        self.assertEqual(calls, [500])            # 让路一次后恢复并跑完

    def test_writer_active_during_shard_yields(self):
        # 每日循环走查判例（2026-10-18）：外部写者若在我们的分片期间写状态
        # 日志，写会被吸收进「自己写完」的基线（片间隙探测窗口只有毫秒级）
        # → 分片结束后必须有一个观察窗，mtime 变了就是外部写者活跃，让路。
        # 场景：automation 从分片 1 中途开始持续写 → sprint 让路 3 次退出 4，
        # run 只被调 1 次。
        self.tmp.mkdir(exist_ok=True)
        clock = {"t": 1000000.0}
        state = {"last_write": clock["t"] - 3600.0,   # 启动前很久无写入
                 "automation": False}

        def mtime():
            return state["last_write"]

        def sleeper(sec):
            clock["t"] += sec
            if state["automation"]:
                state["last_write"] = clock["t"]      # 写者活跃：每次等待期间都有写入

        calls = []
        def fake_run_shard(n):
            calls.append(n)
            state["automation"] = True
            return 0

        with mock.patch.object(BS.xq_classify, "run",
                               side_effect=fake_run_shard), \
             mock.patch.object(BS, "fetch_counts",
                               side_effect=lambda: {"source": 99999,
                                                    "classified": 1, "ok": 1,
                                                    "degraded": 0,
                                                    "pending": 99998}), \
             mock.patch.object(BS, "status_log_mtime", side_effect=mtime), \
             mock.patch.object(BS.time, "sleep", side_effect=sleeper):
            rc = BS.sprint(limit=2000, win=None, force=True)
        self.assertEqual(rc, BS.RC_YIELDED_OUT)
        self.assertEqual(calls, [500])            # 只跑了 1 片就发现写者活跃

    def test_writer_during_shard_then_quiet_proceeds(self):
        # 对照判例：外部写者只在分片期间写了一次就安静 → 观察窗内 mtime 不再
        # 变化 → 不误让路，继续下一分片。
        self.tmp.mkdir(exist_ok=True)
        clock = {"t": 1000000.0}
        state = {"last_write": clock["t"] - 3600.0}

        def mtime():
            return state["last_write"]

        def sleeper(sec):
            clock["t"] += sec                     # 安静：等待期间无写入

        calls = []

        def fake_run(shard):
            calls.append(shard)
            state["last_write"] = clock["t"] + 1.0  # 外部写者分片期间写一次
            return 0

        with mock.patch.object(BS.xq_classify, "run", side_effect=fake_run), \
             mock.patch.object(BS, "fetch_counts",
                               side_effect=lambda: {"source": 99999,
                                                    "classified": 1, "ok": 1,
                                                    "degraded": 0,
                                                    "pending": 99998}), \
             mock.patch.object(BS, "status_log_mtime", side_effect=mtime), \
             mock.patch.object(BS.time, "sleep", side_effect=sleeper):
            rc = BS.sprint(limit=1000, win=None, force=True)
        self.assertEqual(rc, BS.RC_OK)
        self.assertEqual(calls, [500, 500])       # 两片全跑完，无让路

    def test_circuit_break_on_shard_failure(self):
        calls = []

        def fake_run(shard):
            calls.append(shard)
            return 1                              # 连续硬失败熔断

        with mock.patch.object(BS.xq_classify, "run", side_effect=fake_run), \
             mock.patch.object(BS, "fetch_counts"):
            rc = BS.sprint(limit=2000, win=None, force=True)
        self.assertEqual(rc, BS.RC_OK)            # 熔断按正常收尾落账退出
        self.assertEqual(calls, [500])            # 只跑了 1 片就停

    def test_lock_conflict_short_circuit(self):
        BS.LOCK_PATH.write_text("999999")
        with mock.patch.object(BS, "is_pid_alive", return_value=True), \
             mock.patch.object(BS.xq_classify, "run") as mrun:
            rc = BS.sprint(limit=100, win=None, force=True)
        self.assertEqual(rc, BS.RC_LOCKED)
        self.assertFalse(mrun.called)


if __name__ == "__main__":
    unittest.main()
