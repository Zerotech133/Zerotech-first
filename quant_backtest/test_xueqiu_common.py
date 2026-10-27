# -*- coding: utf-8 -*-
"""xueqiu_common.py 单测（不发真实网络请求）。

覆盖：符号归一 / manifest 往返 / 退避封顶 / 原子 parquet / SQLite upsert
幂等 / token 读取（设计 §2.1 测试项）。pytest 与 unittest 双兼容。
"""
import os
import sys
import time
import unittest
from unittest import mock

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X


class TestNormSymbol(unittest.TestCase):
    """norm_symbol / to_ts_code 双向归一。"""

    def test_ts_suffix(self):
        self.assertEqual(X.norm_symbol("000338.SZ"), "SZ000338")
        self.assertEqual(X.norm_symbol("600519.SH"), "SH600519")

    def test_prefixed(self):
        self.assertEqual(X.norm_symbol("sz000338"), "SZ000338")
        self.assertEqual(X.norm_symbol("SH600519"), "SH600519")

    def test_bare_digits(self):
        self.assertEqual(X.norm_symbol("000338"), "SZ000338")   # 0 头=深
        self.assertEqual(X.norm_symbol("600519"), "SH600519")   # 6 头=沪
        self.assertEqual(X.norm_symbol("300750"), "SZ300750")   # 3 头=深

    def test_invalid(self):
        for bad in ("", "abc", "12345", "1234567", "SH60051", None,
                    "HK00700", "US.AAPL"):
            self.assertEqual(X.norm_symbol(bad), "", msg=repr(bad))

    def test_to_ts_code(self):
        self.assertEqual(X.to_ts_code("SZ000338"), "000338.SZ")
        self.assertEqual(X.to_ts_code("SH600519"), "600519.SH")
        self.assertEqual(X.to_ts_code("junk"), "")
        self.assertEqual(X.to_ts_code(X.norm_symbol("000338.SZ")),
                         "000338.SZ")     # 双向往返


class TestManifestRoundtrip(unittest.TestCase):
    """XqManifest 落盘→重载往返 + 原子写。"""

    def _tmp_path(self):
        import tempfile
        d = tempfile.mkdtemp()
        return os.path.join(d, "_manifest.json")

    def test_roundtrip_and_mark(self):
        p = self._tmp_path()
        mf = X.XqManifest(p)
        mf.load()
        self.assertEqual(mf.get("xq_quote_snap"), {})
        mf.mark("xq_quote_snap", done=True, rows=612, file="d_20261016.parquet",
                status="ok")
        mf.mark("xq_northbound", done=False, rows=0, status="empty",
                note="空返回不落片")
        mf.mark_health("quotec_probe", {"ok": True, "latency_ms": 412})
        self.assertTrue(mf.save())
        # 重载校验往返保真
        mf2 = X.XqManifest(p)
        data = mf2.load()
        self.assertTrue(data["xq_quote_snap"]["done"])
        self.assertEqual(data["xq_quote_snap"]["rows"], 612)
        self.assertEqual(data["xq_northbound"]["status"], "empty")
        self.assertEqual(data["health"]["quotec_probe"]["latency_ms"], 412)
        # mark 增量语义：未给字段保留原值，last_run 刷新
        mf2.mark("xq_quote_snap", rows=700, status="ok")
        self.assertEqual(mf2.get("xq_quote_snap")["file"],
                         "d_20261016.parquet")
        self.assertEqual(mf2.get("xq_quote_snap")["rows"], 700)

    def test_load_corrupt_returns_skeleton(self):
        p = self._tmp_path()
        with open(p, "w", encoding="utf-8") as f:
            f.write("{broken json")
        mf = X.XqManifest(p)
        data = mf.load()
        self.assertEqual(data, {"health": {}})   # 损坏降级为骨架不 raise


class TestBackoffCap(unittest.TestCase):
    """backoff_sleep 封顶 300s + 单调递增到顶。"""

    def test_cap_300(self):
        slept = []
        orig = time.sleep
        try:
            time.sleep = lambda s: slept.append(s)
            X.backoff_sleep(0)                    # 10 + [0,2)
            X.backoff_sleep(1)                    # 20 + [0,2)
            X.backoff_sleep(5)                    # 2^5*10=320 → 300
            X.backoff_sleep(20)                   # 天文数字 → 300
        finally:
            time.sleep = orig
        self.assertAlmostEqual(slept[0], 10, delta=2)
        self.assertAlmostEqual(slept[1], 20, delta=2)
        self.assertAlmostEqual(slept[2], 300, delta=2)
        self.assertAlmostEqual(slept[3], 300, delta=2)
        self.assertLessEqual(max(slept), 302)     # 封顶+抖动上界


class TestAtomicParquet(unittest.TestCase):
    """原子 parquet：成功后无 .tmp 残留；内容往返一致。"""

    def test_write_and_read(self):
        import tempfile
        d = tempfile.mkdtemp()
        path = os.path.join(d, "sub", "d_20261016.parquet")
        df = pd.DataFrame({"symbol": ["SZ000338"], "ts_code": ["000338.SZ"],
                           "current": [15.2], "timestamp": ["2026-10-16"]})
        X.atomic_parquet(df, path)
        self.assertTrue(os.path.exists(path))
        self.assertFalse(os.path.exists(path + ".tmp"))   # tmp 已清理
        back = pd.read_parquet(path)
        self.assertEqual(len(back), 1)
        self.assertEqual(back.loc[0, "ts_code"], "000338.SZ")


class TestUpsertSqlite(unittest.TestCase):
    """SQLite upsert 幂等 + 建索引。"""

    def test_idempotent_repeat(self):
        import tempfile
        db = os.path.join(tempfile.mkdtemp(), "xueqiu_info.db")
        rows1 = [{"comment_id": "a1", "symbol": "SH600519",
                  "created_at": "2026-10-16 09:30:00", "title": "t1",
                  "desc": "", "user_id": "u1", "view_count": 5,
                  "fetched_at": "2026-10-16T18:00:00"}]
        n1 = X.upsert_sqlite(db, "xq_comment", rows1, ["comment_id"])
        self.assertEqual(n1, 1)
        # 同 pk 再写：REPLACE 不增行
        rows2 = [dict(rows1[0], title="t1-updated")]
        n2 = X.upsert_sqlite(db, "xq_comment", rows2, ["comment_id"])
        self.assertEqual(n2, 1)
        import sqlite3
        conn = sqlite3.connect(db)
        cnt = conn.execute("SELECT COUNT(*) FROM xq_comment").fetchone()[0]
        title = conn.execute(
            "SELECT title FROM xq_comment").fetchone()[0]
        idx = [r[1] for r in conn.execute(
            "PRAGMA index_list(xq_comment)")]
        conn.close()
        self.assertEqual(cnt, 1)               # 幂等：仍 1 行
        self.assertEqual(title, "t1-updated")  # 后写覆盖
        self.assertIn("idx_xq_comment_sym", idx)   # 复合索引已建

    def test_empty_rows(self):
        self.assertEqual(X.upsert_sqlite("whatever.db", "xq_status",
                                         [], ["status_id"]), 0)


class TestLoadToken(unittest.TestCase):
    """token 读取：有值注入环境变量；空占位返回空串。"""

    def test_placeholder_returns_empty(self):
        import tempfile
        p = os.path.join(tempfile.mkdtemp(), ".env.local")
        with open(p, "w", encoding="utf-8") as f:
            f.write("XT_DATA_KEY=xxx\nXQ_A_TOKEN=\n")
        os.environ.pop("XUEQIUTOKEN", None)
        self.assertEqual(X.load_token(p), "")
        self.assertNotIn("XUEQIUTOKEN", os.environ)

    def test_valid_token_sets_env(self):
        import tempfile
        tok = "a" * 40
        p = os.path.join(tempfile.mkdtemp(), ".env.local")
        with open(p, "w", encoding="utf-8") as f:
            f.write("# comment\nXQ_A_TOKEN=%s\n" % tok)
        self.assertEqual(X.load_token(p), tok)
        self.assertEqual(os.environ.get("XUEQIUTOKEN"), tok)
        os.environ.pop("XUEQIUTOKEN", None)


class TestIpBlocked(unittest.TestCase):
    """P0-1 熔断原语：异常判定 / trip→active 往返 / 过期自愈。"""

    def _tmp_mf(self):
        import tempfile
        mf = X.XqManifest(os.path.join(tempfile.mkdtemp(),
                                       "_manifest.json"))
        mf.load()
        return mf

    def test_looks_ip_blocked(self):
        self.assertTrue(X.looks_ip_blocked(
            Exception("Seek IP Blacklisted (IP = 1.2.3.4)")))
        self.assertTrue(X.looks_ip_blocked(
            Exception("seek ip blacklisted")))
        for e in (Exception("400016 token 失效"),
                  Exception("JSONDecodeError"), None, ""):
            self.assertFalse(X.looks_ip_blocked(e))

    def test_trip_then_active(self):
        mf = self._tmp_mf()
        self.assertEqual(X.ip_blocked_active(mf), "")   # 初始未熔断
        until = X.ip_block_trip(mf, minutes=30)
        self.assertTrue(until)
        desc = X.ip_blocked_active(mf)
        self.assertIn("剩约", desc)
        self.assertIn(until[:10], desc)                 # 到期日在描述里
        # 落盘重载后仍生效
        mf.save()
        mf2 = X.XqManifest(mf.path)
        mf2.load()
        self.assertTrue(X.ip_blocked_active(mf2))

    def test_expired_self_heals(self):
        """冷却到期 → active 返回 ""（熔断自愈，不人工解锁）。"""
        from datetime import datetime, timedelta
        mf = self._tmp_mf()
        past = (datetime.now() - timedelta(minutes=1)
                ).isoformat(timespec="seconds")
        mf.mark_health("ip_blocked_until", past)
        self.assertEqual(X.ip_blocked_active(mf), "")

    def test_bad_format_self_heals(self):
        mf = self._tmp_mf()
        mf.mark_health("ip_blocked_until", "not-a-datetime")
        self.assertEqual(X.ip_blocked_active(mf), "")


class TestTokenProbe(unittest.TestCase):
    """P1-5：token_probe 三态（valid / 400016 失效 / 网络故障）。

    用 sys.modules 注入 fake pysnowball（项目既有模式；勿用
    builtins.__import__ 劫持——恢复失败会全局污染后续测试）。
    """

    @staticmethod
    def _run(fn) -> dict:
        import types
        from unittest import mock
        m = types.ModuleType("pysnowball")
        m.kline = fn
        with mock.patch.dict(sys.modules, {"pysnowball": m}):
            return X.token_probe("SH600519")

    def test_valid(self):
        out = self._run(lambda *a, **k: {"error_code": 0,
                                         "error_description": None,
                                         "data": {"item": []}})
        self.assertTrue(out["ok"])
        self.assertTrue(out["valid"])
        self.assertEqual(out["err"], "")

    def test_invalid_400016(self):
        out = self._run(lambda *a, **k: {
            "error_code": 400016, "error_description": "token 失效",
            "data": None})
        self.assertTrue(out["ok"])          # HTTP 200 往返正常
        self.assertFalse(out["valid"])      # 但 token 明确失效
        self.assertIn("400016", out["err"])

    def test_http_error_net_fail(self):
        def boom(*a, **k):
            raise RuntimeError("502 Bad Gateway")
        out = self._run(boom)
        self.assertFalse(out["ok"])
        self.assertFalse(out["valid"])      # valid 仅在 ok=True 有意义
        self.assertIn("502", out["err"])

    def test_bad_response_type(self):
        out = self._run(lambda *a, **k: "not-a-dict")
        self.assertFalse(out["ok"])
        self.assertEqual(out["err"], "bad_response")


class _CookieJar(dict):
    """伪 requests cookie jar：set/get 语义（四级链测试用）。"""

    def set(self, name, value, domain=""):
        self[name] = value

    def get(self, name, default=""):
        return dict.get(self, name, default)


class _Sess4L:
    """带 headers/cookies 的伪 Session（ensure_session_cookies 面）。"""

    def __init__(self):
        self.headers = {}
        self.cookies = _CookieJar()


class TestEnsureSessionL3Playwright(unittest.TestCase):
    """四级链 L3（Playwright 匿名预热）接线：L2 浏览器提取失败后
    调 xq_browser.harvest_cookies 注入；失败落 L4 warm。全程 mock
    （不开浏览器、不触 rookiepy 真实读取）。"""

    def setUp(self):
        import os as _os
        self._env_bak = _os.environ.get("XUEQIUTOKEN")
        _os.environ.pop("XUEQIUTOKEN", None)

    def tearDown(self):
        import os as _os
        if self._env_bak is not None:
            _os.environ["XUEQIUTOKEN"] = self._env_bak
        else:
            _os.environ.pop("XUEQIUTOKEN", None)

    def _patch_rookiepy_dead(self):
        """让 L2 rookiepy 路径全部失败（假模块 sys.modules 注入，
        判例：不用 builtins.__import__ 劫持），直落 L3。"""
        import sys
        import types
        fake = types.ModuleType("rookiepy")

        def _dead(*a, **kw):
            raise RuntimeError("test: rookiepy disabled")

        fake.edge = _dead
        fake.chrome = _dead
        fake.firefox = _dead
        return mock.patch.dict(sys.modules, {"rookiepy": fake})

    def test_l3_harvest_injects_token(self):
        sess = _Sess4L()
        with self._patch_rookiepy_dead(), \
                mock.patch("xq_browser.harvest_cookies",
                           return_value=[{"name": "xq_a_token",
                                          "value": "d" * 40,
                                          "domain": ".xueqiu.com"}]):
            tok = X.ensure_session_cookies(sess, "")
        self.assertEqual(tok, "d" * 40)
        self.assertEqual(sess.cookies.get("xq_a_token"), "d" * 40)
        import os as _os
        self.assertEqual(_os.environ.get("XUEQIUTOKEN"), "d" * 40)

    def test_l3_empty_falls_to_warm(self):
        sess = _Sess4L()
        with self._patch_rookiepy_dead(), \
                mock.patch("xq_browser.harvest_cookies", return_value=[]), \
                mock.patch.object(X, "warm_session",
                                  return_value="e" * 40):
            tok = X.ensure_session_cookies(sess, "")
        self.assertEqual(tok, "e" * 40)     # L4 warm 兜底生效

    def test_l3_crash_falls_to_warm(self):
        sess = _Sess4L()
        with self._patch_rookiepy_dead(), \
                mock.patch("xq_browser.harvest_cookies",
                           side_effect=RuntimeError("boom")), \
                mock.patch.object(X, "warm_session", return_value=""):
            tok = X.ensure_session_cookies(sess, "")
        self.assertEqual(tok, "")           # L3 崩溃不阻塞，L4 空


class TestWafDetect(unittest.TestCase):
    """P2-8：阿里云 WAF 挑战页识别。

    2026-10-17 探测实证：雪球 HTML 页面（首页/个股页）全 WAF 化——
    裸 requests 只得 200+JS 挑战页，无匿名 xq_a_token 可种（三级链
    第 3 级失效）；API JSON 端点未被拦。HTML 降级在 requests 语境
    不可行（Playwright 通路 L 级暂缓）。
    """

    WAF_BODY = ('<textarea id="renderData" style="display:none">'
                '{"_waf_bd8ce2ce37":"LGy/0ELX"}</textarea>'
                '<meta name="aliyun_waf_aa" content="371ac51">')

    class _WR:
        def __init__(self, status_code=200, text=""):
            self.status_code = status_code
            self.text = text

    class _Sess:
        def __init__(self, resp):
            self._resp = resp
            self.headers = {}
            self.cookies = {}

        def get(self, url, timeout=15):
            return self._resp

    def test_looks_waf_true(self):
        self.assertTrue(X.looks_waf_challenged(self.WAF_BODY))

    def test_looks_waf_false(self):
        self.assertFalse(X.looks_waf_challenged(""))
        self.assertFalse(X.looks_waf_challenged('{"error_code":0}'))
        self.assertFalse(X.looks_waf_challenged("<html>正常页</html>"))

    def test_warm_session_waf_page_returns_empty(self):
        """WAF 挑战页：无 token 可种 → 返回 ""（不 raise，告警路径）。"""
        sess = self._Sess(self._WR(200, self.WAF_BODY))
        self.assertEqual(X.warm_session(sess), "")

    def test_warm_session_resp_without_text(self):
        """无 text 属性的伪响应（存量测试桩）不炸——getattr 容错。"""
        sess = self._Sess(_BareResp())
        self.assertEqual(X.warm_session(sess), "")


class _BareResp:
    """无 text 属性的 200 响应（对齐 test_xueqiu_crawl._Resp 形态）。"""
    status_code = 200

    def json(self):
        return {}


if __name__ == "__main__":
    unittest.main(verbosity=2)
