# -*- coding: utf-8 -*-
"""动态代理池（DynamicPool）单测——mock 拉取与验活，零真实网络。

覆盖：四段解析 / 两段解析 / 坏格式拒绝 / TTL 被动换新 / rotate 立即换 /
拉取失败限频降级 / get_pool 三级优先（静态>动态>空）。
"""

from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xq_proxy as xp


class ParseTests(unittest.TestCase):
    def test_four_segment(self):
        url = xp.parse_proxy_line("218.95.37.11:25152:u1:p1")
        self.assertEqual(url, "http://u1:p1@218.95.37.11:25152")

    def test_password_with_colon(self):
        # pass 含冒号：第一段后全归密码（四段格式约定）
        url = xp.parse_proxy_line("1.2.3.4:8080:u1:p:a")
        self.assertEqual(url, "http://u1:p%3Aa@1.2.3.4:8080")

    def test_credentials_are_url_encoded(self):
        url = xp.parse_proxy_line("1.2.3.4:8080:u@ser:p#ss? /中")
        self.assertEqual(url, "http://u%40ser:p%23ss%3F%20%2F%E4%B8%AD@1.2.3.4:8080")

    def test_two_segment(self):
        url = xp.parse_proxy_line("218.95.37.11:25152")
        self.assertEqual(url, "http://218.95.37.11:25152")

    def test_bad_formats_rejected(self):
        for bad in ("", "abc", "1.2.3.4", "1.2.3.4:port", "999.1.1.1:80:x:y"):
            self.assertEqual(xp.parse_proxy_line(bad), "", bad)


class DynamicPoolTests(unittest.TestCase):
    def setUp(self):
        self.pool = xp.DynamicPool("http://api.test/get", ttl_sec=240)
        # 默认验活恒真（单测不碰网络）
        self.pool._verify = lambda e: True

    def test_cold_fetch_and_cache(self):
        with mock.patch.object(self.pool, "_fetch_raw", return_value="1.2.3.4:80:u:p"):
            e1 = self.pool.current()
            self.assertIsNotNone(e1)
            self.assertEqual(e1.url, "http://u:p@1.2.3.4:80")
            self.assertIn("kdl-1.2.3.4:80", e1.name)
            # TTL 内不重复拉取
            e2 = self.pool.current()
            self.assertIs(e1, e2)

    def test_ttl_expiry_rotates(self):
        with mock.patch.object(self.pool, "_fetch_raw", return_value="1.2.3.4:80:u:p"):
            e1 = self.pool.current()
        self.pool._expires = time.time() - 1  # 强制过期
        with mock.patch.object(self.pool, "_fetch_raw", return_value="5.6.7.8:80:u:p"):
            e2 = self.pool.current()
        self.assertIsNot(e1, e2)
        self.assertIn("5.6.7.8", e2.name)

    def test_rotate_on_blocked(self):
        with mock.patch.object(self.pool, "_fetch_raw", return_value="1.2.3.4:80:u:p"):
            self.pool.current()
        with mock.patch.object(self.pool, "_fetch_raw", return_value="9.9.9.9:80:u:p"):
            new = self.pool.rotate("ip_blocked")
        self.assertIn("9.9.9.9", new.name)
        self.assertEqual(self.pool.rotations, 2)

    def test_fetch_failure_cooldown_then_direct(self):
        with mock.patch.object(self.pool, "_fetch_raw", return_value=""):
            self.assertIsNone(self.pool.current())  # 直连降级
            # 限频窗口内不再拉取（_fetch_raw 不被调用的路径）
            t0 = self.pool._fetch_cooldown_until
            self.assertGreater(t0, time.time())

    def test_verify_rejects_blacklisted_exit(self):
        """验活：出口 IP 也被雪球拉黑 → 换下一个候选。"""
        calls = {"n": 0}

        def fake_verify(entry):
            calls["n"] += 1
            return calls["n"] >= 2  # 第 1 个坏、第 2 个好

        self.pool._verify = fake_verify
        with mock.patch.object(
            self.pool, "_fetch_raw", side_effect=["1.1.1.1:80:u:p", "2.2.2.2:80:u:p"]
        ):
            e = self.pool.current()
        self.assertIn("2.2.2.2", e.name)
        self.assertEqual(calls["n"], 2)

    def test_proxies_shape(self):
        with mock.patch.object(self.pool, "_fetch_raw", return_value="1.2.3.4:80:u:p"):
            pr = self.pool.proxies()
        self.assertEqual(
            pr, {"http": "http://u:p@1.2.3.4:80", "https": "http://u:p@1.2.3.4:80"}
        )


class GetPoolPriorityTests(unittest.TestCase):
    def setUp(self):
        xp._POOL = None  # 重置单例

    def tearDown(self):
        xp._POOL = None

    def test_dynamic_when_static_empty(self):
        with (
            mock.patch.object(xp, "load_pool", return_value=xp.ProxyPool([])),
            mock.patch.object(
                xp,
                "load_dynamic_config",
                return_value={"api_url": "http://x", "ttl_sec": 300},
            ),
        ):
            pool = xp.get_pool()
        self.assertIsInstance(pool, xp.DynamicPool)
        self.assertEqual(pool.ttl_sec, 300)

    def test_static_wins_when_enabled(self):
        with (
            mock.patch.object(
                xp,
                "load_pool",
                return_value=xp.ProxyPool([xp.ProxyEntry("s1", "http://1.1.1.1:1")]),
            ),
            mock.patch.object(
                xp, "load_dynamic_config", return_value={"api_url": "http://x"}
            ),
        ):
            pool = xp.get_pool()
        self.assertIsInstance(pool, xp.ProxyPool)
        self.assertNotIsInstance(pool, xp.DynamicPool)

    def test_empty_when_no_config(self):
        with (
            mock.patch.object(xp, "load_pool", return_value=xp.ProxyPool([])),
            mock.patch.object(xp, "load_dynamic_config", return_value={}),
        ):
            pool = xp.get_pool()
        self.assertIsInstance(pool, xp.ProxyPool)
        self.assertFalse(pool.enabled())


if __name__ == "__main__":
    unittest.main()
