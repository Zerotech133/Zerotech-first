# -*- coding: utf-8 -*-
"""xq_proxy.py 单测（P0-3）：静态出口池轮换/冷却/降级/env 接线/熔断联动。

全 mock 零真实网络（RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md §E P0-3）。
"""
import json
import os
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xq_proxy as P


def _entry(name="e1", url="http://u:p@1.1.1.1:8080"):
    return P.ProxyEntry(name=name, url=url)


class TestLoadPool(unittest.TestCase):
    """配置加载：缺失/坏格式/缺字段 → 静默降级空池（直连）。"""

    def test_missing_file_degrades_empty(self):
        with tempfile.TemporaryDirectory() as td:
            pool = P.load_pool(os.path.join(td, "none.json"))
        self.assertFalse(pool.enabled())
        self.assertIsNone(pool.current())
        self.assertIsNone(pool.proxies())

    def test_bad_json_degrades_empty(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.json")
            with open(p, "w", encoding="utf-8") as f:
                f.write("{oops")
            self.assertFalse(P.load_pool(p).enabled())

    def test_valid_entries_loaded_skips_bad_items(self):
        with tempfile.TemporaryDirectory() as td:
            p = os.path.join(td, "x.json")
            with open(p, "w", encoding="utf-8-sig") as f:
                json.dump([{"name": "a", "url": "http://1.1.1.1:1"},
                           {"name": "", "url": "http://2.2.2.2:2"},
                           {"name": "b", "url": ""}], f)
            pool = P.load_pool(p)
        self.assertTrue(pool.enabled())
        self.assertEqual([e.name for e in pool.entries], ["a"])


class TestPoolRotation(unittest.TestCase):
    """轮换语义：健康优先 / 冷却跳过 / 全冷却直连降级。"""

    def test_proxies_dict_shape(self):
        pool = P.ProxyPool([_entry("a"),
                            _entry("b", "http://2.2.2.2:2")])
        self.assertEqual(pool.proxies(),
                         {"http": "http://u:p@1.1.1.1:8080",
                          "https": "http://u:p@1.1.1.1:8080"})

    def test_current_skips_cooldown_exit(self):
        pool = P.ProxyPool([_entry("a"),
                            _entry("b", "http://2.2.2.2:2")])
        pool.entries[0].cooldown_until = time.time() + 999
        self.assertEqual(pool.current().name, "b")

    def test_all_cooldown_degrades_direct(self):
        """绝不硬选脏出口：全冷却 → current/proxies 均 None（直连）。"""
        pool = P.ProxyPool([_entry("a"),
                            _entry("b", "http://2.2.2.2:2")])
        for e in pool.entries:
            e.cooldown_until = time.time() + 999
        self.assertIsNone(pool.current())
        self.assertIsNone(pool.proxies())

    def test_mark_fail_puts_current_cooldown(self):
        pool = P.ProxyPool([_entry("a"),
                            _entry("b", "http://2.2.2.2:2")])
        pool.mark_fail()
        self.assertGreater(pool.entries[0].cooldown_until, time.time())
        self.assertEqual(pool.entries[0].fails, 1)
        self.assertEqual(pool.current().name, "b")

    def test_rotate_disabled_returns_none(self):
        self.assertIsNone(P.ProxyPool([]).rotate("any"))


class TestApplyEnv(unittest.TestCase):
    """sync 层接线：HTTP(S)_PROXY 环境变量（pysnowball trust_env）。

    setUp/tearDown 快照恢复四键，防止污染进程环境。
    """
    PROXY_KEYS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self.PROXY_KEYS}

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_apply_sets_and_clears_legacy_conflict(self):
        os.environ["HTTP_PROXY"] = "http://legacy:1"
        pool = P.ProxyPool([_entry("a")])
        self.assertEqual(P.apply_proxy_env(pool), "a")
        self.assertEqual(os.environ["HTTPS_PROXY"], "http://u:p@1.1.1.1:8080")

    def test_apply_empty_clears_all(self):
        os.environ["HTTPS_PROXY"] = "http://legacy:1"
        P.apply_proxy_env(P.ProxyPool([]))
        for k in self.PROXY_KEYS:
            self.assertNotIn(k, os.environ)

    def test_notify_blocked_rotates_and_reapplies(self):
        """熔断联动：当前出口进冷却 → 轮换下一健康出口并刷新 env。"""
        pool = P.ProxyPool([_entry("a"),
                            _entry("b", "http://2.2.2.2:2")])
        self.assertEqual(P.notify_blocked(pool=pool), "b")
        self.assertEqual(os.environ["HTTP_PROXY"], "http://2.2.2.2:2")
        self.assertEqual(pool.entries[0].fails, 1)
        self.assertGreater(pool.entries[0].cooldown_until, time.time())

    def test_notify_blocked_all_cooldown_clears_env(self):
        pool = P.ProxyPool([_entry("a")])
        self.assertEqual(P.notify_blocked(pool=pool), "")
        self.assertNotIn("HTTP_PROXY", os.environ)

    def test_notify_blocked_disabled_noop(self):
        self.assertEqual(P.notify_blocked(pool=P.ProxyPool([])), "")


class TestTripHook(unittest.TestCase):
    """xueqiu_common.ip_block_trip 钩子集成：熔断即轮换出口。"""

    def test_ip_block_trip_rotates_exit(self):
        import xueqiu_common as X
        pool = P.ProxyPool([_entry("a"),
                            _entry("b", "http://2.2.2.2:2")])
        old = P._POOL
        P._POOL = pool
        try:
            mf = X.XqManifest(os.path.join(tempfile.mkdtemp(), "m.json"))
            X.ip_block_trip(mf)
        finally:
            P._POOL = old
        self.assertEqual(pool.entries[0].fails, 1)   # 当前出口被标脏
        self.assertEqual(pool.current().name, "b")   # 已切到健康出口

    def test_ip_block_trip_without_pool_noop(self):
        """空池（未配置代理）→ 钩子静默，熔断行为不变（P0-1 回归）。"""
        import xueqiu_common as X
        old = P._POOL
        P._POOL = P.ProxyPool([])
        try:
            mf = X.XqManifest(os.path.join(tempfile.mkdtemp(), "m.json"))
            until = X.ip_block_trip(mf)
        finally:
            P._POOL = old
        self.assertTrue(until)
        self.assertIn("ip_blocked_until", mf.health())


class TestUaStickiness(unittest.TestCase):
    """P2-7 出口-指纹粘性：出口绑定专属 UA（RESEARCH_MEDIACRAWLER
    _XUEQIU_20261017.md §E P2-7「同一出口 IP 固定 UA/请求头组合」）。"""

    def test_ua_for_none_default(self):
        """直连（None）→ DEFAULT_UA（与 xueqiu_common.UA 同串语义）。"""
        self.assertEqual(P.ua_for(None), P.DEFAULT_UA)

    def test_ua_for_deterministic(self):
        """同 name 跨实例恒同 UA（crc32 稳定哈希，非内建随机 hash）。"""
        e1, e2 = _entry("home-1"), _entry("home-1")
        ua1, ua2 = P.ua_for(e1), P.ua_for(e2)
        self.assertEqual(ua1, ua2)
        self.assertIn(ua1, P.UA_VARIANTS)
        # 分布性弱断言：16 个不同 name 至少命中 2 种变体
        seen = {P.ua_for(_entry("n%d" % i)) for i in range(16)}
        self.assertGreaterEqual(len(seen), 2)

    def test_ua_for_explicit_overrides(self):
        """配置显式 ua 字段优先于确定性分配。"""
        e = _entry("home-1")
        e.ua = "CustomUA/1.0"
        self.assertEqual(P.ua_for(e), "CustomUA/1.0")

    def test_load_pool_ua_field(self):
        """load_pool 读取 ua 字段；缺省空串（运行时按 name 分配）。"""
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "xq_proxies.json")
            with open(p, "w", encoding="utf-8") as f:
                json.dump([{"name": "a1", "url": "http://u:p@1.1.1.1:8080",
                            "ua": "UA/X"},
                           {"name": "a2", "url": "http://u:p@2.2.2.2:8080"}],
                          f)
            pool = P.load_pool(p)
        self.assertEqual(pool.entries[0].ua, "UA/X")
        self.assertEqual(pool.entries[1].ua, "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
