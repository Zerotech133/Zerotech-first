# -*- coding: utf-8 -*-
"""tdx_sync 加固单测（T-I4 / K-INTEL-20261019-TDXSYNC，零真实网络/零真锁）。

覆盖两处加固：
a. _get 网络抖动重试退避（3 次、2s/8s 间隔、DNS 错误码留痕）
b. _core_lock_acquire 陈锁自愈（pid 存活佐证 + LOCK-CLEAR 留痕）
mock 全部网络与锁路径，不触 C:\\Temp 真锁、不连 rookie 数据源。
"""

import os
import sys
import time
import types
import unittest
from unittest import mock

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tdx_sync as M  # noqa: E402


class TestGetRetry(unittest.TestCase):
    def setUp(self):
        self.sleeps = []
        self.patchers = [
            mock.patch.object(M.time, "sleep", side_effect=self.sleeps.append),
            mock.patch.object(M, "_dns_probe", return_value=None),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()

    def test_dns_failure_retries_2s_8s_then_errdns(self):
        """DNS 间歇故障：3 次尝试、退避 2s/8s、错误码 ERR_DNS 入异常。"""
        boom = requests.exceptions.ConnectionError(
            "HTTPSConnectionPool(host='rookie.xtcloud-tech.com', port=48088): "
            "NameResolutionError: Failed to resolve 'rookie.xtcloud-tech.com'"
        )
        with mock.patch.object(M.SESS, "get", side_effect=boom) as mg:
            with self.assertRaises(RuntimeError) as cm:
                M._get("/api/data/status")
        self.assertEqual(mg.call_count, M.RETRY)  # 3 次
        self.assertEqual(self.sleeps, [2, 8])  # 2s/8s 退避
        self.assertIn("ERR_DNS", str(cm.exception))
        self.assertIn("NameResolutionError", str(cm.exception))

    def test_dns_recovers_on_second_attempt(self):
        """瞬时抖动：第 1 次败第 2 次成 → 只退避一次 2s，不发第 3 请求。"""
        boom = requests.exceptions.ConnectionError("NameResolutionError x")
        ok = types.SimpleNamespace(
            status_code=200, json=lambda: {"tables": [{"table": "ts_daily"}]}
        )
        with mock.patch.object(M.SESS, "get", side_effect=[boom, ok]) as mg:
            d = M._get("/api/data/status")
        self.assertEqual(mg.call_count, 2)
        self.assertEqual(self.sleeps, [2])
        self.assertIn("ts_daily", d["tables"][0]["table"])

    def test_http_502_errcode_carried(self):
        """非 200：错误码 HTTP 502 与 body 头部随异常带出。"""
        bad = types.SimpleNamespace(status_code=502, content=b"bad gateway")
        with mock.patch.object(M.SESS, "get", return_value=bad):
            with self.assertRaises(RuntimeError) as cm:
                M._get("/api/data/table/ts_daily")
        self.assertEqual(self.sleeps, [2, 8])
        self.assertIn("HTTP 502", str(cm.exception))


class TestStaleLockHeal(unittest.TestCase):
    """陈锁自愈：CORE_LOCK 重定向临时目录，全程不碰 C:\\Temp 真锁。"""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.mkdtemp()
        self.lock = os.path.join(self.tmp, "core.lock")
        self._old = M.CORE_LOCK
        M.CORE_LOCK = self.lock

    def tearDown(self):
        M.CORE_LOCK = self._old
        if os.path.exists(self.lock):
            os.remove(self.lock)

    def _mk(self, body, age_s=0):
        with open(self.lock, "w", encoding="ascii") as f:
            f.write(body)
        if age_s:
            old = time.time() - age_s
            os.utime(self.lock, (old, old))

    def test_dead_pid_stale_lock_healed(self):
        """陈锁（死 pid、age>2h）：自动清理并重抢成功（T-Q3 锁残留场景）。"""
        self._mk("999999999 %d\n" % int(time.time()), age_s=3 * 3600)
        with mock.patch.object(M, "_pid_alive", return_value=False):
            self.assertTrue(M._core_lock_acquire())
        with open(self.lock, encoding="ascii") as f:
            self.assertEqual(int(f.readline().split()[0]), os.getpid())
        M._core_lock_release()

    def test_alive_pid_stale_lock_not_killed(self):
        """age>2h 但 pid 存活：SKIP 不误杀（ts_rt_min 全量 3-4h 长任务）。"""
        self._mk("%d %d\n" % (os.getpid(), int(time.time())), age_s=3 * 3600)
        self.assertFalse(M._core_lock_acquire())  # 自身 pid 必然存活
        with open(self.lock, encoding="ascii") as f:
            self.assertEqual(int(f.readline().split()[0]), os.getpid())

    def test_dead_pid_fresh_lock_cleared_early(self):
        """死 pid 但 age<2h：对齐 ps1 v2 判例即刻 LOCK-CLEAR，不等满阈值。"""
        self._mk("999999999 %d\n" % int(time.time()))
        with mock.patch.object(M, "_pid_alive", return_value=False):
            self.assertTrue(M._core_lock_acquire())
        M._core_lock_release()

    def test_alive_pid_fresh_lock_skip(self):
        """活跃实例（pid 存活、age<2h）：SKIP 静默退 0 判例。"""
        self._mk("%d %d\n" % (os.getpid(), int(time.time())))
        self.assertFalse(M._core_lock_acquire())

    def test_corrupt_lock_falls_back_to_age(self):
        """旧版/损坏锁（无 pid 行）：回退纯 age 判定——新锁 SKIP、陈锁回收。"""
        self._mk("garbage-no-pid\n")
        self.assertFalse(M._core_lock_acquire())
        self._mk("garbage-no-pid\n", age_s=3 * 3600)
        self.assertTrue(M._core_lock_acquire())
        M._core_lock_release()


class TestPidAlive(unittest.TestCase):
    def test_self_alive(self):
        self.assertTrue(M._pid_alive(os.getpid()))

    def test_bogus_pid_dead(self):
        self.assertFalse(M._pid_alive(-1))
        self.assertFalse(M._pid_alive(0))


if __name__ == "__main__":
    unittest.main()
