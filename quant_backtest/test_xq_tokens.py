# -*- coding: utf-8 -*-
"""xq_tokens.py 单测 + init_ball 池接线集成（不发真实网络请求）。

覆盖：pick 冷却过滤/最早使用优先/全冷却降级、mark_fail/mark_ok、
save/load 持久化往返、坏配置容错、掩码、init_ball 池优先与失效
换号（RESEARCH_MEDIACRAWLER §E P1-4）。pytest 与 unittest 双兼容。
"""
import json
import os
import sys
import tempfile
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X
import xueqiu_sync as S
import xq_tokens as T


def _entry(name="tok-1", token="a" * 40, last_used=0.0, fails=0,
           cd=0.0) -> T.TokenEntry:
    return T.TokenEntry(name=name, token=token, last_used=last_used,
                        fail_count=fails, cooldown_until=cd)


def _fake_ball(**attrs) -> types.ModuleType:
    m = types.ModuleType("pysnowball")
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


_PROBE_OK = {"ok": True, "latency_ms": 1, "ts": "x", "err": ""}


class TestTokenPool(unittest.TestCase):
    """TokenPool 核心语义。"""

    def test_pick_empty_pool(self):
        self.assertIsNone(T.TokenPool([]).pick())

    def test_pick_skips_cooldown(self):
        hot = _entry("hot", cd=time.time() + 600)
        ok = _entry("ok")
        self.assertEqual(T.TokenPool([hot, ok]).pick().name, "ok")

    def test_pick_least_recently_used(self):
        old = _entry("old", last_used=100.0)
        never = _entry("never", last_used=0.0)
        recent = _entry("recent", last_used=900.0)
        self.assertEqual(T.TokenPool([recent, old, never]).pick().name,
                         "never")

    def test_pick_all_cooldown_returns_none(self):
        p = T.TokenPool([_entry("a", cd=time.time() + 600),
                         _entry("b", cd=time.time() + 600)])
        self.assertIsNone(p.pick())

    def test_mark_fail_cooldowns(self):
        e = _entry()
        p = T.TokenPool([e])
        p.mark_fail(e, cooldown=60)
        self.assertEqual(e.fail_count, 1)
        self.assertFalse(e.healthy())

    def test_mark_ok_clears(self):
        e = _entry(fails=3, cd=time.time() + 600)
        T.TokenPool([e]).mark_ok(e)
        self.assertEqual(e.fail_count, 0)
        self.assertTrue(e.healthy())

    def test_save_load_roundtrip(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "xq_tokens.json")
        e1 = _entry("t1", "a" * 40, last_used=111.0, fails=1)
        e2 = _entry("t2", "b" * 40)
        p = T.TokenPool([e1, e2], path)
        p.mark_fail(e2, cooldown=120)
        p.save()
        p2 = T.load_tokens(path)
        self.assertEqual(len(p2.entries), 2)
        by = {e.name: e for e in p2.entries}
        self.assertEqual(by["t1"].last_used, 111.0)
        self.assertEqual(by["t1"].fail_count, 1)
        self.assertFalse(by["t2"].healthy())        # 冷却状态持久化
        self.assertEqual(by["t2"].token, "b" * 40)

    def test_load_bad_json_empty(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "bad.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write("{not json")
        pool = T.load_tokens(path)
        self.assertFalse(pool.enabled())

    def test_load_missing_file_empty(self):
        pool = T.load_tokens(os.path.join(tempfile.mkdtemp(), "no.json"))
        self.assertFalse(pool.enabled())

    def test_load_skips_bad_entries(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "x.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump([{"name": "ok-1", "token": "c" * 40},
                       {"name": "no-token"},
                       {"token": "d" * 40},
                       "not-a-dict"], f)
        pool = T.load_tokens(path)
        self.assertEqual(len(pool.entries), 1)
        self.assertEqual(pool.entries[0].name, "ok-1")

    def test_mask_never_full(self):
        m = T.mask("abcdefghijklmnop")
        self.assertTrue(m.startswith("abcd"))
        self.assertNotIn("abcdefghijklmnop", m)
        self.assertEqual(T.mask(""), "(empty)")


class TestInitBallPoolWiring(unittest.TestCase):
    """init_ball 池接线：池优先 / 失效换号 / 未启用降级三级链。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (X.OUT_DIR, X.MANIFEST, X.INFO_DB)
        X.OUT_DIR = os.path.join(self._tmp, "xueqiu")
        X.MANIFEST = os.path.join(X.OUT_DIR, "_manifest.json")
        X.INFO_DB = os.path.join(X.OUT_DIR, "xueqiu_info.db")
        os.environ.pop("XUEQIUTOKEN", None)

    def tearDown(self):
        X.OUT_DIR, X.MANIFEST, X.INFO_DB = self._orig
        os.environ.pop("XUEQIUTOKEN", None)

    def _mf(self) -> X.XqManifest:
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        return mf

    def test_pool_hit_valid_token(self):
        """池启用 + probe valid → 用池 token、mark_ok、token_name=池名。"""
        e = _entry("pool-1", "p" * 40)
        pool = T.TokenPool([e], os.path.join(self._tmp, "t.json"))
        fake = _fake_ball(set_token=lambda t: None)
        mf = self._mf()
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_tokens.get_pool", return_value=pool), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=_PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value={"ok": True,
                                                "valid": True,
                                                "latency_ms": 1,
                                                "ts": "x", "err": ""}), \
                mock.patch.object(X, "load_token",
                                  return_value=""), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            ctx = S.init_ball(mf)
        self.assertEqual(ctx["token"], "p" * 40)
        self.assertEqual(e.fail_count, 0)
        self.assertGreater(e.last_used, 0)
        self.assertEqual(mf.health().get("token_name"), "pool-1")

    def test_pool_rotate_on_400016(self):
        """首条 400016 → mark_fail 冷却 + 换第二条 → valid。"""
        e1 = _entry("dead", "d" * 40)
        e2 = _entry("alive", "v" * 40)
        pool = T.TokenPool([e1, e2], os.path.join(self._tmp, "t.json"))
        fake = _fake_ball(set_token=lambda t: None)
        dead = {"ok": True, "valid": False, "latency_ms": 1, "ts": "x",
                "err": "error_code=400016 token 失效"}
        alive = {"ok": True, "valid": True, "latency_ms": 1, "ts": "x",
                 "err": ""}
        mf = self._mf()
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_tokens.get_pool", return_value=pool), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=_PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  side_effect=[dead, alive]), \
                mock.patch.object(X, "load_token",
                                  return_value=""), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            ctx = S.init_ball(mf)
        self.assertEqual(ctx["token"], "v" * 40)
        self.assertEqual(e1.fail_count, 1)
        self.assertFalse(e1.healthy())
        self.assertEqual(e2.fail_count, 0)
        self.assertEqual(mf.health().get("token_name"), "alive")

    def test_pool_exhausted_last_probe_dead(self):
        """两条全失效 → 池尽返回，末次 probe 仍 dead（main 据此跳批）。"""
        e1 = _entry("d1", "1" * 40)
        e2 = _entry("d2", "2" * 40)
        pool = T.TokenPool([e1, e2], os.path.join(self._tmp, "t.json"))
        fake = _fake_ball(set_token=lambda t: None)
        dead = {"ok": True, "valid": False, "latency_ms": 1, "ts": "x",
                "err": "error_code=400016"}
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_tokens.get_pool", return_value=pool), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=_PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=dead), \
                mock.patch.object(X, "load_token",
                                  return_value=""), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            ctx = S.init_ball(self._mf())
        self.assertTrue(ctx["token_probe"]["ok"])   # ok+非valid → 跳批
        self.assertFalse(ctx["token_probe"]["valid"])
        self.assertEqual(e1.fail_count, 1)
        self.assertEqual(e2.fail_count, 1)

    def test_pool_disabled_falls_back_to_chain(self):
        """未启用池 → 三级链 load_token，token_name=chain。"""
        pool = T.TokenPool([], os.path.join(self._tmp, "t.json"))
        fake = _fake_ball(set_token=lambda t: None)
        mf = self._mf()
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_tokens.get_pool", return_value=pool), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=_PROBE_OK), \
                mock.patch.object(X, "load_token",
                                  return_value="c" * 40), \
                mock.patch.object(X, "token_probe",
                                  return_value={"ok": True,
                                                "valid": True,
                                                "latency_ms": 1,
                                                "ts": "x", "err": ""}), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            ctx = S.init_ball(mf)
        self.assertEqual(ctx["token"], "c" * 40)
        self.assertEqual(mf.health().get("token_name"), "chain")

    def test_chain_token_dead_no_rotate(self):
        """三级链单 token 失效 → 无替代者，probe 结果如实返回（不换）。"""
        pool = T.TokenPool([], os.path.join(self._tmp, "t.json"))
        fake = _fake_ball(set_token=lambda t: None)
        dead = {"ok": True, "valid": False, "latency_ms": 1, "ts": "x",
                "err": "error_code=400016"}
        with mock.patch.dict(sys.modules, {"pysnowball": fake}), \
                mock.patch("xq_tokens.get_pool", return_value=pool), \
                mock.patch.object(X, "quotec_probe",
                                  return_value=_PROBE_OK), \
                mock.patch.object(X, "token_probe",
                                  return_value=dead), \
                mock.patch.object(X, "load_token",
                                  return_value="c" * 40), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            ctx = S.init_ball(self._mf())
        self.assertEqual(ctx["token"], "c" * 40)    # 原样返回
        self.assertFalse(ctx["token_probe"]["valid"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
