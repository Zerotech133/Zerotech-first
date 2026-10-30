# -*- coding: utf-8 -*-
"""P2.5: 限流测试。"""
import os
import sys
import unittest

WT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(WT, "portal"))

import rate_limit  # noqa: E402


class TestRateLimit(unittest.TestCase):
    def setUp(self):
        rate_limit.reset_all()

    def test_allow_then_deny(self):
        w = rate_limit.SlidingWindow(3, 60)
        self.assertTrue(w.allow("ip1"))
        self.assertTrue(w.allow("ip1"))
        self.assertTrue(w.allow("ip1"))
        self.assertFalse(w.allow("ip1"))
        self.assertTrue(w.allow("ip2"))  # 其他 key 不受影响

    def test_window_expiry(self):
        w = rate_limit.SlidingWindow(2, 0.5)
        base = 1000.0
        self.assertTrue(w.allow("k", now=base))
        self.assertTrue(w.allow("k", now=base + 0.1))
        self.assertFalse(w.allow("k", now=base + 0.2))
        self.assertTrue(w.allow("k", now=base + 0.6))  # 窗口滑出

    def test_reset(self):
        w = rate_limit.SlidingWindow(1, 60)
        self.assertTrue(w.allow("k"))
        self.assertFalse(w.allow("k"))
        w.reset("k")
        self.assertTrue(w.allow("k"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
