# -*- coding: utf-8 -*-
"""otel_tracing 纯单测：默认关闭 no-op / 装饰器透传 / fail-open。"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS))
import otel_tracing as ot  # noqa: E402


class TestNoop(unittest.TestCase):
    def test_disabled_by_default(self):
        os.environ.pop(ot.ENV_ENABLED, None)
        self.assertFalse(ot.enabled())

    def test_traced_context_noop(self):
        os.environ.pop(ot.ENV_ENABLED, None)
        with ot.traced("x", a=1) as span:
            self.assertIsNone(span)

    def test_instrument_passthrough(self):
        os.environ.pop(ot.ENV_ENABLED, None)

        @ot.instrument
        def add(a, b):
            return a + b

        self.assertEqual(add(2, 3), 5)
        self.assertEqual(add.__name__, "add")

    def test_fail_open_when_enabled_but_no_exporter(self):
        os.environ[ot.ENV_ENABLED] = "1"
        os.environ[ot.ENV_ENDPOINT] = "http://127.0.0.1:1/v1/traces"
        try:
            # 初始化失败也应静默降级：不抛异常、yield None
            with ot.traced("x") as span:
                self.assertIsNone(span)
        finally:
            os.environ.pop(ot.ENV_ENABLED, None)
            os.environ.pop(ot.ENV_ENDPOINT, None)
            ot._state["init"] = None
            ot._state["tracer"] = None
            ot._state["error"] = None

    def test_status_keys(self):
        s = ot.status()
        self.assertIn("enabled", s)
        self.assertIn("endpoint", s)


if __name__ == "__main__":
    unittest.main()
