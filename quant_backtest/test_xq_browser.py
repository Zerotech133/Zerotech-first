# -*- coding: utf-8 -*-
"""xq_browser.py 单测（mock 实采，不开浏览器、不发真实网络请求）。

覆盖：cookie 缓存往返/TTL/坏文件/缺 token 降级 + harvest 入口
（缓存命中免实采 / 实采成功落缓存 / 无 token 与异常降级）。
浏览器实采（_harvest_live）由 S4U 冒烟与 spike 脚本覆盖
（C:/Temp/xq_s4u_smoke.py、xq_waf_spike*.py），不在 pytest 面。
"""
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xq_browser as B


def _tok_cookies(tok: str = "a" * 40) -> list[dict]:
    return [{"name": "xq_a_token", "value": tok, "domain": ".xueqiu.com"},
            {"name": "acw_tc", "value": "x", "domain": ".xueqiu.com"}]


class TestCache(unittest.TestCase):
    """cookie 缓存：TTL 往返 / 过期 / 坏文件 / 缺 token。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = os.path.join(self._tmp, "cache.json")

    def test_roundtrip(self):
        self.assertTrue(B._save_cache(_tok_cookies(), self.path))
        got = B._load_cache(self.path)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["name"], "xq_a_token")

    def test_expired(self):
        B._save_cache(_tok_cookies(), self.path)
        with open(self.path, encoding="utf-8") as f:
            data = json.load(f)
        data["harvested_at"] -= B.CACHE_TTL_SEC + 1
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        self.assertEqual(B._load_cache(self.path), [])

    def test_bad_json(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{not json")
        self.assertEqual(B._load_cache(self.path), [])
        self.assertFalse(os.path.exists(self.path + ".tmp"))

    def test_missing_token_rejected(self):
        B._save_cache([{"name": "acw_tc", "value": "x"}], self.path)
        self.assertEqual(B._load_cache(self.path), [])


class TestHarvest(unittest.TestCase):
    """harvest_cookies 入口：缓存优先 / 实采落盘 / 降级语义。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self.path = os.path.join(self._tmp, "cache.json")

    def test_cache_hit_skips_live(self):
        B._save_cache(_tok_cookies(), self.path)
        with mock.patch.object(B, "_harvest_live") as m:
            got = B.harvest_cookies(path=self.path)
        self.assertEqual(len(got), 2)
        m.assert_not_called()               # TTL 内免开浏览器

    def test_live_success_saves_cache(self):
        with mock.patch.object(B, "_harvest_live",
                               return_value=_tok_cookies()) as m:
            got = B.harvest_cookies(path=self.path)
        self.assertEqual(got[0]["value"], "a" * 40)
        m.assert_called_once()
        self.assertEqual(len(B._load_cache(self.path)), 2)   # 已落缓存

    def test_live_no_token_degrades(self):
        with mock.patch.object(B, "_harvest_live",
                               return_value=[{"name": "acw_tc",
                                              "value": "x"}]):
            got = B.harvest_cookies(force=True, path=self.path)
        self.assertEqual(got, [])           # 挑战未过 → 降级
        self.assertEqual(B._load_cache(self.path), [])  # 不写缓存

    def test_live_crash_degrades(self):
        with mock.patch.object(B, "_harvest_live",
                               side_effect=RuntimeError("boom")):
            got = B.harvest_cookies(force=True, path=self.path)
        self.assertEqual(got, [])           # 异常吞掉，绝不 raise

    def test_force_bypasses_cache(self):
        B._save_cache(_tok_cookies("b" * 40), self.path)
        with mock.patch.object(B, "_harvest_live",
                               return_value=_tok_cookies("c" * 40)) as m:
            got = B.harvest_cookies(force=True, path=self.path)
        m.assert_called_once()
        self.assertEqual(got[0]["value"], "c" * 40)   # 强刷生效


if __name__ == "__main__":
    unittest.main(verbosity=2)
