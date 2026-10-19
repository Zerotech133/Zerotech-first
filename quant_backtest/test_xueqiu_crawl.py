# -*- coding: utf-8 -*-
"""xueqiu_crawl.py 单测（mock HTTP，不发真实网络请求）。

覆盖：养 cookie / 429 退避 / SQLite 幂等 upsert
（设计 §2.1 测试项 test_warm_session/test_429_backoff/test_comment_upsert）。
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import xueqiu_common as X
import xueqiu_crawl as C


class _Resp:
    """伪 requests.Response（可控状态码/JSON）。"""

    def __init__(self, status_code=200, js=None):
        self.status_code = status_code
        self._js = js if js is not None else {}

    def json(self):
        if self._js is None:
            raise ValueError("not json")
        return self._js


class _Sess:
    """伪 requests.Session：按脚本回放响应序列。"""

    def __init__(self, script):
        self.script = list(script)          # 每次调 get 弹出一个
        self.calls = []
        self.headers = {}
        self.cookies = {}

    def get(self, url, params=None, timeout=15, headers=None):
        # headers：对齐真实 requests API（xq_get 现携 Referer）
        self.calls.append((url, params))
        return self.script.pop(0)


class _TmpEnv(unittest.TestCase):
    """隔离 OUT_DIR/INFO_DB 到临时目录。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._orig = (X.OUT_DIR, X.MANIFEST, X.INFO_DB)
        X.OUT_DIR = os.path.join(self._tmp, "xueqiu")
        X.MANIFEST = os.path.join(X.OUT_DIR, "_manifest.json")
        X.INFO_DB = os.path.join(X.OUT_DIR, "xueqiu_info.db")

    def tearDown(self):
        X.OUT_DIR, X.MANIFEST, X.INFO_DB = self._orig


class TestWarmSession(_TmpEnv):
    """test_warm_session：首页种 cookie 提取 xq_a_token。"""

    def test_warm_ok(self):
        sess = requests_stub_with_cookie("a" * 40)
        tok = X.warm_session(sess)
        self.assertEqual(tok, "a" * 40)

    def test_warm_fail_empty(self):
        sess = _Sess([_Resp(200, {})])
        tok = X.warm_session(sess)
        self.assertEqual(tok, "")           # 失败不 raise，返回空串

    def test_crawler_init_with_warm(self):
        """XqCrawler.__init__ 三级链取 cookie（失败不阻塞）。

        补丁目标跟随三级链改造（2026-10-17）：_warm 现走
        X.ensure_session_cookies（warm_session 只是 L3 兜底内调）。
        """
        with mock.patch.object(X, "ensure_session_cookies",
                               return_value="b" * 40):
            c = C.XqCrawler()
        self.assertEqual(c.token, "b" * 40)


def requests_stub_with_cookie(tok: str):
    sess = _Sess([_Resp(200, {})])
    sess.cookies = {"xq_a_token": tok}
    return sess


class Test429Backoff(_TmpEnv):
    """test_429_backoff：429→指数退避→重试→成功。"""

    def test_429_then_ok(self):
        ok_js = {"error_code": 0, "data": {"items": [{"id": 1}]}}
        sess = _Sess([_Resp(429), _Resp(429), _Resp(200, ok_js)])
        slept = []
        orig = time.sleep
        try:
            time.sleep = lambda s: slept.append(s)
            data = X.xq_get(sess, "https://stock.xueqiu.com/v4/x.json")
        finally:
            time.sleep = orig
        self.assertIsNotNone(data)
        self.assertEqual(data["items"][0]["id"], 1)
        self.assertEqual(len(slept), 2)     # 两次 429 各退避一次
        self.assertGreaterEqual(slept[0], 10)   # attempt0: 2^0*10=10
        self.assertGreaterEqual(slept[1], 20)   # attempt1: 2^1*10=20
        self.assertLessEqual(slept[1], 22)      # +抖动上界

    def test_429_exhausted_returns_none(self):
        """重试到顶仍 429 → None（不 raise，调用方记 failed 继续）。"""
        sess = _Sess([_Resp(429)] * X.MAX_RETRY)
        with mock.patch.object(X, "backoff_sleep", lambda *a: None):
            data = X.xq_get(sess, "https://x.json")
        self.assertIsNone(data)

    def test_error_code_returns_none(self):
        sess = _Sess([_Resp(200, {"error_code": 400016,
                                  "error_description": "token 失效"})])
        data = X.xq_get(sess, "https://x.json")
        self.assertIsNone(data)             # 业务错不重试


class TestCommentUpsert(_TmpEnv):
    """test_comment_upsert：评论 SQLite 幂等 + 复合索引。"""

    def _crawler(self):
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            return C.XqCrawler()

    def test_upsert_idempotent(self):
        c = self._crawler()
        rows = [{
            "comment_id": "s1", "created_at": "2026-10-16 10:00:00",
            "title": "讨论", "desc": "看好", "user_id": "u1",
            "target": "SH600519", "view_count": 3, "comment_count": 1,
            "retweet_count": 0, "like_count": 5, "reward_count": 0,
            "source": "android", "category": 0, "symbol": "SH600519",
            "fetched_at": "2026-10-16 18:00:00",
        }]
        self.assertEqual(c.save("xq_comment", rows), 1)
        self.assertEqual(c.save("xq_comment", rows), 1)   # 幂等
        import sqlite3
        conn = sqlite3.connect(X.INFO_DB)
        cnt = conn.execute("SELECT COUNT(*) FROM xq_comment").fetchone()[0]
        idx = [r[1] for r in conn.execute("PRAGMA index_list(xq_comment)")]
        conn.close()
        self.assertEqual(cnt, 1)
        self.assertIn("idx_xq_comment_sym", idx)   # (symbol,created_at) 索引

    def test_fetch_comments_upsert_roundtrip(self):
        """P1-NEW 回归（QA Round-2）：fetch_comments 产出键与 xq_comment
        DDL 对齐（comment_id 主键，无 status_id 列）→ 落库不再
        OperationalError。（QA 若代写同名用例，此例可去重）"""
        items = [{"id": "s1", "title": "t", "desc": "d",
                  "created_at": 1760582400000, "user": {"user_id": "u1"},
                  "target": "/SH600519/...", "view_count": 1,
                  "comment_count": 2, "retweet_count": 0, "like_count": 3,
                  "reward_count": 0, "source": "web"}]
        js = {"error_code": 0, "data": {"items": items}}
        sess = _Sess([_Resp(200, js)])
        c = self._crawler()
        c.sess = sess
        with mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rows = c.fetch_comments("SH600519", max_pages=1)
        self.assertEqual(len(rows), 1)
        self.assertNotIn("status_id", rows[0])       # 键已对齐 DDL
        self.assertEqual(rows[0]["comment_id"], "s1")
        self.assertEqual(rows[0]["symbol"], "SH600519")
        self.assertEqual(c.save("xq_comment", rows), 1)   # 落库不抛
        import sqlite3
        conn = sqlite3.connect(X.INFO_DB)
        n = conn.execute("SELECT COUNT(*) FROM xq_comment").fetchone()[0]
        conn.close()
        self.assertEqual(n, 1)

    def test_fetch_comments_dedup(self):
        """跨页同帖 id 去重 + 空页终止。"""
        items = [{"id": "s1", "title": "t", "desc": "d",
                  "created_at": 1760582400000, "user": {"user_id": "u1"},
                  "target": "/SH600519/...", "view_count": 1,
                  "comment_count": 2, "retweet_count": 0, "like_count": 3,
                  "reward_count": 0, "source": "web"}]
        js1 = {"error_code": 0, "data": {"items": items}}
        js2 = {"error_code": 0, "data": {"items": items}}   # 第二页同帖
        sess = _Sess([_Resp(200, js1), _Resp(200, js2)])
        c = self._crawler()
        c.sess = sess
        with mock.patch.object(X, "jitter_sleep", lambda *a: None):
            rows = c.fetch_comments("SH600519", max_pages=3)
        self.assertEqual(len(rows), 1)      # 跨页去重
        self.assertEqual(rows[0]["symbol"], "SH600519")
        self.assertEqual(rows[0]["user_id"], "u1")

    def test_fetch_timeline_rows(self):
        items = [{"id": "st1", "title": "大盘讨论", "desc": "…",
                  "created_at": 1760582400000, "user_id": "u9",
                  "view_count": 100, "comment_count": 10,
                  "retweet_count": 2, "like_count": 30, "reward_count": 0,
                  "source": "web"}]
        js = {"error_code": 0, "data": {"items": items}}
        sess = _Sess([_Resp(200, js)])
        c = self._crawler()
        c.sess = sess
        rows = c.fetch_timeline(category=102, count=20, max_pages=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status_id"], "st1")
        self.assertEqual(rows[0]["category"], 102)

    def test_fetch_timeline_nested_data(self):
        """P3 嵌套结构（2026-10-17 实测判例）：外层 id=feed 壳，真实帖
        在内层 data JSON——解析内层得真实 status_id/时间/标题/计数，
        created_at 非空（旧解析落库全空壳行的回归锁）。"""
        inner = {"id": 172229155, "title": "一分钟读懂招股书",
                 "description": "正文摘要", "created_at": 1613812244000,
                 "user": {"id": 7961123322}, "view_count": 3454919,
                 "reply_count": 88, "retweet_count": 3, "like_count": 21,
                 "source": "xueqiu.com"}
        body = {"list": [{"id": "feed1", "data": json.dumps(inner)}],
                "next_max_id": "257156"}
        with mock.patch.object(C.X, "xq_get", return_value=body):
            c = self._crawler()
            rows = c.fetch_timeline(category=102, count=20, max_pages=1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status_id"], "172229155")  # 内层真 id
        self.assertEqual(rows[0]["title"], "一分钟读懂招股书")
        self.assertRegex(rows[0]["created_at"], r"^\d{4}-\d{2}-\d{2} ")
        self.assertEqual(rows[0]["user_id"], "7961123322")
        self.assertEqual(rows[0]["view_count"], 3454919)
        self.assertEqual(rows[0]["comment_count"], 88)  # reply_count 归一

    def test_fetch_timeline_nested_bad_data_skipped(self):
        """data 解析失败宁缺毋滥：不回退外层壳 id（防空壳行回流）。"""
        body = {"list": [{"id": "feed1", "data": "{bad json"}]}
        with mock.patch.object(C.X, "xq_get", return_value=body):
            c = self._crawler()
            rows = c.fetch_timeline(category=102, count=20, max_pages=1)
        self.assertEqual(rows, [])

    def test_fetch_timeline_cursor_pagination(self):
        """P3 游标翻页：匿名 page 无效，按官方 next_max_id 推进；
        游标耗尽自然终止；跨步 status_id 去重。"""
        inner1 = {"id": 1, "title": "新帖", "created_at": 1760582400000}
        inner2 = {"id": 2, "title": "次新帖", "created_at": 1760496000000}
        bodies = [
            {"list": [{"id": "f1", "data": json.dumps(inner1)}],
             "next_max_id": "257156"},
            {"list": [{"id": "f2", "data": json.dumps(inner2)}]},
        ]
        calls = []

        def fake_xq_get(sess, url, params=None, **kw):
            calls.append(dict(params or {}))
            return bodies[len(calls) - 1]

        with mock.patch.object(C.X, "xq_get", side_effect=fake_xq_get), \
                mock.patch.object(X, "jitter_sleep", lambda *a: None):
            c = self._crawler()
            rows = c.fetch_timeline(category=102, count=20, max_pages=3)
        self.assertEqual([r["status_id"] for r in rows], ["1", "2"])
        self.assertEqual(len(calls), 2)              # 游标耗尽即停
        self.assertNotIn("max_id", calls[0])         # 首步不带游标
        self.assertEqual(calls[1]["max_id"], "257156")  # 官方游标推进

    def test_crawl_all_end_to_end(self):
        """timeline+hot 全链（mock HTTP）→ SQLite 落库 + manifest。"""
        items = [{"id": "st1", "title": "热帖", "desc": "d",
                  "created_at": 1760582400000, "user_id": "u1",
                  "view_count": 9, "comment_count": 1, "retweet_count": 0,
                  "like_count": 2, "reward_count": 0, "source": "web"}]
        js_t = {"error_code": 0, "data": {"items": items}}
        js_h = {"error_code": 0, "data": {"items": items}}
        sess = _Sess([_Resp(200, js_t), _Resp(200, js_h)])
        with mock.patch.object(X, "warm_session", return_value="c" * 40):
            c = C.XqCrawler()
        c.sess = sess
        with mock.patch.object(X, "jitter_sleep", lambda *a: None), \
                mock.patch.object(C, "XqCrawler", lambda: c):
            res = C.crawl_all(categories=(102,), hot=True)
        self.assertEqual(res["xq_status"], 1)
        self.assertEqual(res["xq_hot_post"], 1)
        import sqlite3
        conn = sqlite3.connect(X.INFO_DB)
        n1 = conn.execute("SELECT COUNT(*) FROM xq_status").fetchone()[0]
        n2 = conn.execute("SELECT COUNT(*) FROM xq_hot_post").fetchone()[0]
        conn.close()
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 1)


class TestMainCli(_TmpEnv):
    """CLI 冒烟：--mode comments 空池正常退出。"""

    def test_comments_empty_pool(self):
        with mock.patch.object(X, "pool_codes", return_value=[]):
            rc = C.main(["--mode", "comments"])
        self.assertEqual(rc, 0)


class TestFailSemantics(_TmpEnv):
    """P1 回归（审计）：xq_get 失败（None）≠ 真空空页——manifest 记
    failed 而非 ok/empty，且全表失败时 main rc=1（fail 而非伪成功）。"""

    def _crawler(self, script):
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            c = C.XqCrawler()
        c.sess = _Sess(script)
        return c

    def test_fetch_timeline_error_returns_none(self):
        """首页 429 到顶 → fetch_timeline 返回 None（错误信号）。"""
        c = self._crawler([_Resp(429)] * (X.MAX_RETRY + 1))
        with mock.patch.object(X, "backoff_sleep", lambda *a: None):
            self.assertIsNone(c.fetch_timeline(102, max_pages=1))

    def test_fetch_timeline_partial_pages_keep_rows(self):
        """首页成功、次页失败 → 保留已采行（部分成功，不升 failed）。"""
        js = {"error_code": 0, "data": {"items": [
            {"id": "st1", "title": "t", "created_at": 1760582400000}]}}
        c = self._crawler([_Resp(200, js)] + [_Resp(429)] * X.MAX_RETRY)
        with mock.patch.object(X, "backoff_sleep", lambda *a: None):
            rows = c.fetch_timeline(102, max_pages=2)
        self.assertEqual(len(rows), 1)

    def test_crawl_all_request_fail_marks_failed(self):
        """timeline+hot 请求全败 → manifest failed + failed_tables 上报。"""
        c = self._crawler([_Resp(429)] * (2 * X.MAX_RETRY + 4))
        with mock.patch.object(X, "jitter_sleep", lambda *a: None), \
                mock.patch.object(X, "backoff_sleep", lambda *a: None), \
                mock.patch.object(C, "XqCrawler", lambda: c):
            res = C.crawl_all(categories=(102,), hot=True, pages=1)
        self.assertIn("xq_status", res["failed_tables"])
        self.assertIn("xq_hot_post", res["failed_tables"])
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_status")["status"], "failed")
        self.assertEqual(mf.get("xq_hot_post")["status"], "failed")

    def test_main_timeline_fail_rc1(self):
        c = self._crawler([_Resp(429)] * (X.MAX_RETRY + 4))
        with mock.patch.object(X, "jitter_sleep", lambda *a: None), \
                mock.patch.object(X, "backoff_sleep", lambda *a: None), \
                mock.patch.object(C, "XqCrawler", lambda: c):
            rc = C.main(["--mode", "timeline", "--pages", "1"])
        self.assertEqual(rc, 1)

    def test_main_takeover_fail_rc1(self):
        """takeover 全链路皆败（直爬 0 行 + 替代源亦挂）→ rc=1（不再伪成功）。

        T4 契约同步（2026-10-19）：路由器上线后「直爬败」不再直接 rc=1，
        需替代源兜底亦挂才 fail——故本用例 mock fetch_snapshot 亦挂；
        替代源接管成功路径另见 test_main_takeover_alt_source_rescue。
        """
        c = self._crawler([_Resp(429)] * (X.MAX_RETRY + 2))
        with mock.patch.object(X, "backoff_sleep", lambda *a: None), \
                mock.patch("xq_quote_router.fetch_snapshot",
                           return_value=([], "", ["alt down"])), \
                mock.patch.object(C, "XqCrawler", lambda: c):
            rc = C.main(["--mode", "takeover", "--symbols", "SH600519"])
        self.assertEqual(rc, 1)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_quote_snap")["status"], "failed")

    def test_main_takeover_alt_source_rescue(self):
        """T4（2026-10-19）：直爬全败 + 替代源供数 → rc=0 落片。

        替代源接管成功 → manifest ok + note 报来源（消费端无感切换）。
        """
        c = self._crawler([_Resp(429)] * (X.MAX_RETRY + 2))
        alt_rows = [{"symbol": "SH600519", "ts_code": "600519.SH",
                     "current": 1500.0, "percent": 1.2, "chg": 18.0,
                     "volume": 100, "amount": 1.5e8, "pe": None,
                     "pb": None, "market_capital": None,
                     "float_market_capital": None,
                     "trade_date": "20261019",
                     "timestamp": "2026-10-19 15:00:03"}]
        with mock.patch.object(X, "backoff_sleep", lambda *a: None), \
                mock.patch("xq_quote_router.fetch_snapshot",
                           return_value=(alt_rows, "tushare", [])), \
                mock.patch.object(X, "atomic_parquet") as writer, \
                mock.patch.object(C, "XqCrawler", lambda: c):
            rc = C.main(["--mode", "takeover", "--symbols", "SH600519"])
        self.assertEqual(rc, 0)
        writer.assert_called_once()
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        ent = mf.get("xq_quote_snap")
        self.assertEqual(ent["status"], "ok")
        self.assertIn("替代源(tushare)", ent["note"])

    def test_main_comments_all_fail_rc1(self):
        """全部码评论请求失败 → rc=1；manifest xq_comment=failed。"""
        c = self._crawler([_Resp(429)] * (X.MAX_RETRY + 2))
        with mock.patch.object(X, "jitter_sleep", lambda *a: None), \
                mock.patch.object(X, "backoff_sleep", lambda *a: None), \
                mock.patch.object(C, "XqCrawler", lambda: c):
            rc = C.main(["--mode", "comments", "--symbols", "SH600519",
                         "--pages", "1"])
        self.assertEqual(rc, 1)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_comment")["status"], "failed")
        self.assertIn("SH600519", mf.get("xq_comment")["note"])


class TestIpBlocked403(_TmpEnv):
    """P0-1：403 熔断冷却——xq_get raise 不重试 / 冷却入口零请求 /
    运行中 trip 停轮（RESEARCH_MEDIACRAWLER_XUEQIU_20261017.md §E P0-1）。"""

    class _R403:
        """带 text 的 403 伪响应（雪球拉黑页为纯文本，非 JSON）。"""
        status_code = 403
        text = "Seek IP Blacklisted (IP = 1.2.3.4)"

        def json(self):
            raise ValueError("not json")

    def test_xq_get_403_raises_no_retry(self):
        """403 → raise XqIpBlocked，且绝不重试（单响应脚本被弹空）。"""
        sess = _Sess([self._R403()])
        with self.assertRaises(X.XqIpBlocked):
            X.xq_get(sess, "https://xueqiu.com/v4/x.json")
        self.assertEqual(sess.script, [])       # 只打了一次，无重试

    def _trip_manifest(self):
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        X.ip_block_trip(mf)
        mf.save()
        return mf

    def test_crawl_all_cooldown_entry_zero_requests(self):
        """冷却生效 → 全表 ip_blocked、零 HTTP 请求、failed_tables 全表。"""
        self._trip_manifest()
        sess = _Sess([])                        # 空脚本：弹任一响应即炸
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            c = C.XqCrawler()
        c.sess = sess
        with mock.patch.object(C, "XqCrawler", lambda: c):
            res = C.crawl_all(categories=(102,), hot=True)
        self.assertEqual(sess.script, [])       # 一个请求都没发
        self.assertEqual(len(res["failed_tables"]), 2)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_status")["status"], "ip_blocked")
        self.assertEqual(mf.get("xq_hot_post")["status"], "ip_blocked")

    def test_crawl_all_403_trip_stops_round(self):
        """运行中 403 → trip 写 health、hot 不再请求、两表 ip_blocked。"""
        sess = _Sess([self._R403()])            # timeline 第一页即 403
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            c = C.XqCrawler()
        c.sess = sess
        with mock.patch.object(C, "XqCrawler", lambda: c):
            res = C.crawl_all(categories=(102,), hot=True)
        self.assertEqual(sess.script, [])       # hot 未发请求（停轮）
        self.assertEqual(len(res["failed_tables"]), 2)
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_status")["status"], "ip_blocked")
        self.assertEqual(mf.get("xq_hot_post")["status"], "ip_blocked")
        self.assertTrue((mf.health() or {}).get("ip_blocked_until"))

    def test_crawl_symbols_403_trip_breaks(self):
        """逐码模式 403 → trip + break，剩余码不请求，status=ip_blocked。

        pages=1 保证一码一请求（翻页会让 403 在首码第 2 页提前弹出）。
        """
        items = [{"id": "st1", "title": "t", "created_at": 1760582400000}]
        ok_js = {"error_code": 0, "data": {"items": items}}
        sess = _Sess([_Resp(200, ok_js), self._R403(),
                      _Resp(200, ok_js)])       # 码1成功→码2熔断→码3不该被弹
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            c = C.XqCrawler()
        c.sess = sess
        with mock.patch.object(X, "jitter_sleep", lambda *a: None), \
                mock.patch.object(C, "XqCrawler", lambda: c):
            res = C.crawl_symbols(["SH600519", "SZ000338", "SH600036"],
                                  pages=1)
        self.assertEqual(res["codes"], 1)       # 首码成功
        self.assertEqual(len(sess.script), 1)   # 第三码未发请求
        mf = X.XqManifest(X.MANIFEST)
        mf.load()
        self.assertEqual(mf.get("xq_comment")["status"], "ip_blocked")

    def test_main_comments_cooldown_rc1(self):
        """冷却中 --mode comments → rc=1（wrapper 告警），零请求。"""
        self._trip_manifest()
        sess = _Sess([])
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            c = C.XqCrawler()
        c.sess = sess
        with mock.patch.object(C, "XqCrawler", lambda: c):
            rc = C.main(["--mode", "comments", "--symbols", "SH600519"])
        self.assertEqual(rc, 1)
        self.assertEqual(sess.script, [])

    def test_main_all_cooldown_rc1(self):
        """冷却中 --mode all → rc=1（全表 failed_tables→告警语义）。"""
        self._trip_manifest()
        sess = _Sess([])
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            c = C.XqCrawler()
        c.sess = sess
        with mock.patch.object(C, "XqCrawler", lambda: c):
            rc = C.main(["--mode", "all"])
        self.assertEqual(rc, 1)
        self.assertEqual(sess.script, [])


class TestUaStickinessCrawl(_TmpEnv):
    """P2-7 接线：XqCrawler UA 随出口绑定；直连回默认 UA。

    操纵 xq_proxy._POOL 单例（get_pool 缓存后直返），tearDown 恢复。
    """

    def setUp(self):
        super().setUp()
        import xq_proxy as P
        self._P = P
        self._orig_pool = P._POOL

    def tearDown(self):
        self._P._POOL = self._orig_pool
        super().tearDown()

    def _crawler(self):
        with mock.patch.object(X, "ensure_session_cookies", return_value=""):
            return C.XqCrawler()

    def test_default_ua_when_no_pool(self):
        """空池（直连）→ DEFAULT_UA，写入 sess.headers。"""
        self._P._POOL = self._P.ProxyPool([])
        c = self._crawler()
        self.assertEqual(c._ua, self._P.DEFAULT_UA)
        self.assertEqual(c.sess.headers.get("User-Agent"),
                         self._P.DEFAULT_UA)
        self.assertFalse(c.sess.proxies)        # 直连不注入代理

    def test_ua_binds_to_exit(self):
        """有出口 → UA=出口专属（ua_for(entry)），代理同时注入。"""
        e = self._P.ProxyEntry(name="home-1", url="http://u:p@1.1.1.1:8080")
        self._P._POOL = self._P.ProxyPool([e])
        c = self._crawler()
        self.assertEqual(c._ua, self._P.ua_for(e))
        self.assertEqual(c.sess.headers.get("User-Agent"), c._ua)
        self.assertTrue(c.sess.proxies)


class TestCliCrashGuard(_TmpEnv):
    """P2-9：CLI 入口意外异常兜底 rc=1（不裸崩，摘要输出）。"""

    def test_comments_crash_rc1(self):
        with mock.patch.object(C, "crawl_symbols",
                               side_effect=RuntimeError("boom")):
            rc = C.main(["--mode", "comments", "--symbols", "SH600519"])
        self.assertEqual(rc, 1)

    def test_takeover_crash_rc1(self):
        with mock.patch.object(C, "takeover_quote",
                               side_effect=RuntimeError("boom")):
            rc = C.main(["--mode", "takeover", "--symbols", "SH600519"])
        self.assertEqual(rc, 1)

    def test_crawl_all_crash_rc1(self):
        with mock.patch.object(C, "crawl_all",
                               side_effect=RuntimeError("boom")):
            rc = C.main(["--mode", "all", "--pages", "1"])
        self.assertEqual(rc, 1)


def _mock_takeover_crawler(tmp_path, monkeypatch):
    """隔离快照测试：不构造 Session，不 warm/token/网络，不写生产片。"""
    monkeypatch.setattr(X, "OUT_DIR", str(tmp_path))
    monkeypatch.setattr(X, "MANIFEST", str(tmp_path / "_manifest.json"))
    monkeypatch.setattr(X, "INFO_DB", str(tmp_path / "xueqiu_info.db"))
    crawler = mock.Mock()
    crawler.mf = X.XqManifest()
    monkeypatch.setattr(C, "XqCrawler", lambda: crawler)
    return crawler


def _snapshot_row(symbol, ts_code):
    """构造路由器快照回包，便于验证 symbol 与 ts_code 双重一致。"""
    return {"symbol": symbol, "ts_code": ts_code, "current": 10.0,
            "timestamp": "2026-10-19 15:00:00", "trade_date": "20261019"}


def test_takeover_direct_filters_unrequested_index_and_mismatched(
        tmp_path, monkeypatch):
    """直爬只能请求个股并落请求内匹配行；SZ000001 不误杀。"""
    _mock_takeover_crawler(tmp_path, monkeypatch)
    calls = []
    captured = []

    def fake_get(sess, url, params):
        calls.append(params["symbol"])
        return {"items": [
            {"symbol": "SH000001", "timestamp": 1792441200000},
            {"symbol": "SH600036", "timestamp": 1792441200000},
            {"symbol": "SH600519", "ts_code": "000001.SZ",
             "timestamp": 1792441200000},
            {"symbol": "SZ000001", "timestamp": 1792441200000},
            {"symbol": "SH600519", "timestamp": 1792441200000}]}

    monkeypatch.setattr(X, "xq_get", fake_get)
    monkeypatch.setattr(X, "atomic_parquet",
                        lambda frame, path: captured.append((frame.copy(), path)))
    with mock.patch("xq_quote_router.fetch_snapshot") as alt:
        count = C.takeover_quote(["SH000001", "SH600519", "SZ000001"])
    alt.assert_not_called()
    assert calls == ["SH600519,SZ000001"]
    assert count == 2
    frame, path = captured[0]
    assert set(frame["symbol"]) == {"SH600519", "SZ000001"}
    assert dict(zip(frame["symbol"], frame["ts_code"])) == {
        "SH600519": "600519.SH", "SZ000001": "000001.SZ"}
    assert str(tmp_path) in path


def test_takeover_alternative_filters_contaminated_rows(tmp_path, monkeypatch):
    """直爬失败后，路由器混入指数/未请求/错配行均不可落片。"""
    crawler = _mock_takeover_crawler(tmp_path, monkeypatch)
    monkeypatch.setattr(X, "xq_get", lambda *args, **kwargs: None)
    captured = []
    monkeypatch.setattr(X, "atomic_parquet",
                        lambda frame, path: captured.append(frame.copy()))
    alt_rows = [_snapshot_row("SH000001", "000001.SH"),
                _snapshot_row("SH600036", "600036.SH"),
                _snapshot_row("SH600519", "000001.SZ"),
                _snapshot_row("SZ000001", "000001.SZ")]
    with mock.patch("xq_quote_router.fetch_snapshot",
                    return_value=(alt_rows, "akshare", ["主源不可用"])) as alt:
        count = C.takeover_quote(["SH000001", "SH600519", "SZ000001"])
    alt.assert_called_once_with(["SH600519", "SZ000001"])
    assert count == 1
    assert list(captured[0]["symbol"]) == ["SZ000001"]
    assert list(captured[0]["ts_code"]) == ["000001.SZ"]
    assert "替代源(akshare)" in crawler.mf.get("xq_quote_snap")["note"]


def test_takeover_all_polluted_preserves_failure_and_errors(tmp_path,
                                                            monkeypatch):
    """两源只有污染行视同全败，保留替代源错误且绝不写空片。"""
    crawler = _mock_takeover_crawler(tmp_path, monkeypatch)
    monkeypatch.setattr(X, "xq_get", lambda *args, **kwargs: {
        "items": [{"symbol": "SH000001"}, {"symbol": "SH600036"}]})
    writer = mock.Mock()
    monkeypatch.setattr(X, "atomic_parquet", writer)
    with mock.patch("xq_quote_router.fetch_snapshot", return_value=(
            [_snapshot_row("SH000001", "000001.SH"),
             _snapshot_row("SH600036", "600036.SH"),
             _snapshot_row("SH600519", "000001.SZ")],
            "tushare", ["tushare down", "akshare down"])) as alt:
        rc = C.main(["--mode", "takeover", "--symbols",
                     "SH000001,SH600519"])
    assert rc == 1
    alt.assert_called_once_with(["SH600519"])
    writer.assert_not_called()
    assert not (tmp_path / "xq_quote_snap").exists()
    state = crawler.mf.get("xq_quote_snap")
    assert state["status"] == "failed" and state["rows"] == 0
    assert "tushare down" in state["note"]
    assert "akshare down" in state["note"]


def test_takeover_cooldown_polluted_alternative_keeps_ip_blocked(
        tmp_path, monkeypatch):
    """冷却期不碰直爬；备用污染无片，仍以 ip_blocked 告警。"""
    crawler = _mock_takeover_crawler(tmp_path, monkeypatch)
    direct = mock.Mock(side_effect=AssertionError("cooldown must skip xq_get"))
    writer = mock.Mock()
    monkeypatch.setattr(X, "ip_blocked_active", lambda mf: "cooldown")
    monkeypatch.setattr(X, "xq_get", direct)
    monkeypatch.setattr(X, "atomic_parquet", writer)
    with mock.patch("xq_quote_router.fetch_snapshot", return_value=(
            [_snapshot_row("SH000001", "000001.SH")], "akshare",
            ["fallback rejected"])) as alt:
        rc = C.main(["--mode", "takeover", "--symbols",
                     "SH000001,SH600519"])
    assert rc == 1
    alt.assert_called_once_with(["SH600519"])
    direct.assert_not_called()
    writer.assert_not_called()
    assert crawler.mf.get("xq_quote_snap")["status"] == "ip_blocked"
    note = crawler.mf.get("xq_quote_snap")["note"]
    assert "冷却期雪球域跳过" in note
    assert "直爬全批失败" not in note
    assert "fallback rejected" in note


def test_takeover_trip_pollution_retains_circuit_breaker(tmp_path,
                                                         monkeypatch):
    """直爬 403 熔断后仍调用备援；污染全部剔除且状态不降成 failed。"""
    crawler = _mock_takeover_crawler(tmp_path, monkeypatch)
    direct = mock.Mock(side_effect=X.XqIpBlocked("403 blocked"))
    writer = mock.Mock()
    monkeypatch.setattr(X, "xq_get", direct)
    monkeypatch.setattr(X, "atomic_parquet", writer)
    with mock.patch("xq_quote_router.fetch_snapshot", return_value=(
            [_snapshot_row("SH000001", "000001.SH")], "akshare",
            ["alt down"])) as alt:
        count = C.takeover_quote(["SH600519", "SH600036"])
    assert count == 0
    assert direct.call_count == 1
    alt.assert_called_once_with(["SH600519", "SH600036"])
    writer.assert_not_called()
    state = crawler.mf.get("xq_quote_snap")
    assert state["status"] == "ip_blocked"
    assert "alt down" in state["note"]
    assert crawler.mf.health()["ip_blocked_until"]


class TestTakeoverInvalidInputs(_TmpEnv):
    """无有效股票须在 warm/联网/备用源之前失败，并保留旧总账。"""

    def test_empty_index_and_invalid_preserve_manifest_without_side_effects(self):
        cases = ([], ["SH000001", "SZ399001"],
                 ["unknown", "123", "BJ430047"])
        for symbols in cases:
            with self.subTest(symbols=symbols):
                mf = X.XqManifest()
                mf.mark_health("ip_blocked_until", "2099-01-01 00:00:00")
                mf.mark("xq_hot_post", done=True, rows=7, status="ok",
                        file="old_hot.parquet")
                mf.mark("xq_quote_snap", done=True, rows=4, status="ok",
                        file="d_20261018.parquet")
                self.assertTrue(mf.save())
                with mock.patch.object(C, "XqCrawler") as constructor, \
                        mock.patch.object(C.requests, "Session") as session, \
                        mock.patch("socket.socket.connect") as network, \
                        mock.patch("xq_quote_router.fetch_snapshot") as alt, \
                        mock.patch.object(X, "atomic_parquet") as writer:
                    self.assertEqual(C.takeover_quote(symbols), 0)
                constructor.assert_not_called()
                session.assert_not_called()
                network.assert_not_called()
                alt.assert_not_called()
                writer.assert_not_called()
                self.assertFalse(os.path.exists(X.INFO_DB))
                self.assertFalse(os.path.exists(
                    os.path.join(X.OUT_DIR, "xq_quote_snap")))
                saved = X.XqManifest()
                saved.load()
                self.assertEqual(saved.health(), mf.health())
                self.assertEqual(saved.get("xq_hot_post"),
                                 mf.get("xq_hot_post"))
                state = saved.get("xq_quote_snap")
                self.assertFalse(state["done"])
                self.assertEqual(state["rows"], 0)
                self.assertEqual(state["status"], "failed")
                self.assertEqual(state["file"], "")
                self.assertIn("无有效请求股票", state["note"])

    def test_empty_cli_pool_and_index_cli_return_failure_without_network(self):
        for argv in (["--mode", "takeover"],
                     ["--mode", "takeover", "--symbols", "SH000001"]):
            with self.subTest(argv=argv), \
                    mock.patch.object(X, "pool_codes", return_value=[]), \
                    mock.patch.object(C, "XqCrawler") as constructor, \
                    mock.patch("socket.socket.connect") as network, \
                    mock.patch("xq_quote_router.fetch_snapshot") as alt:
                self.assertEqual(C.main(argv), 1)
                constructor.assert_not_called()
                network.assert_not_called()
                alt.assert_not_called()

    def test_mixed_input_reaches_crawler_and_alternative_with_valid_stock(self):
        crawler = mock.Mock()
        crawler.mf = X.XqManifest()
        alt_row = _snapshot_row("SZ000001", "000001.SZ")
        with mock.patch.object(C, "XqCrawler", return_value=crawler) as ctor, \
                mock.patch.object(X, "xq_get", return_value=None) as direct, \
                mock.patch("xq_quote_router.fetch_snapshot", return_value=(
                    [alt_row], "akshare", [])) as alt, \
                mock.patch.object(X, "atomic_parquet") as writer:
            self.assertEqual(C.takeover_quote(
                ["SH000001", "bad", "000001.SZ", "BJ430047"]), 1)
        ctor.assert_called_once_with()
        self.assertEqual(direct.call_args.kwargs["params"]["symbol"],
                         "SZ000001")
        alt.assert_called_once_with(["SZ000001"])
        writer.assert_called_once()
        state = X.XqManifest()
        state.load()
        self.assertEqual(state.get("xq_quote_snap")["status"], "ok")


if __name__ == "__main__":
    unittest.main(verbosity=2)
