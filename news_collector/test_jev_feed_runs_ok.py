# -*- coding: utf-8 -*-
"""jev_feed 失败可见性回归（2026-10-26 JEV 审计 §四-⑤）。

覆盖：源失败（dm.read 抛异常 / 返回 None）→ runs.ok=0、detail.errors 带
source/stage 明细、FEED FAILED、rc=1、失败源水位不推进；真实空数据
（空 DataFrame）→ runs.ok=1、rc=0；判定阶段整批失败 → runs.ok=0；
单条判定失败只留 judge_missing 明细不硬失败。

只使用临时 sqlite DB + monkeypatch，不读不写生产 news.db、不起采集/推送。
"""

import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import jev_feed as J  # noqa: E402

#: 与 news_collector.init_db() 对齐的最小 schema（news 含 stocks/jev 升级列）
SCHEMA = """
CREATE TABLE news (
  id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT, content TEXT,
  hit INTEGER DEFAULT 0, pushed INTEGER DEFAULT 0, digested INTEGER DEFAULT 0,
  created_at TEXT, stocks TEXT, jev TEXT, tag_conf TEXT);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, ts TEXT,
  ok INTEGER, new_count INTEGER, push_count INTEGER, detail TEXT);
"""

#: feed_source/pandas 路径所需最小列（notice 源）
NOTICE_ROW = {
    "title": "半导体龙头封板：设备国产化订单落地",
    "url": "https://example.test/notice/1",
    "notice_date": "20261025",
    "pub_time": "10:30",
    "content": "测试内容",
    "ts_code": "000001.SZ",
}


def _empty_df():
    return pd.DataFrame()


def _one_row_df():
    return pd.DataFrame([NOTICE_ROW])


class JevFeedRunsOkTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="jev_feed_runs_ok_")
        self.dbpath = os.path.join(self.tmpdir, "news.db")
        con = sqlite3.connect(self.dbpath)
        try:
            con.executescript(SCHEMA)
            con.commit()
        finally:
            con.close()
        # 隔离：DB 指向临时库；sleep/log 静音（不写生产 news.db / ai.log）
        self._saved_db = J.DB
        J.DB = self.dbpath
        self.addCleanup(lambda: setattr(J, "DB", self._saved_db))
        # 夜间守卫隔离：真实夜批文件/时间窗不应影响既有用例（默认判读路径）
        self._real_in_night_window = J._in_night_window
        _night = mock.patch.object(J, "_in_night_window", lambda: False)
        _night.start()
        self.addCleanup(_night.stop)
        for patcher in (
            mock.patch.object(J.time, "sleep", lambda _s: None),
            mock.patch.object(J.news_ai, "log", lambda _msg: None),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    # ------------------------------------------------------------------ 工具
    def _latest_run(self):
        con = sqlite3.connect(self.dbpath)
        try:
            row = con.execute(
                "SELECT ok, new_count, detail FROM runs ORDER BY id DESC LIMIT 1"
            ).fetchone()
        finally:
            con.close()
        if row is None:
            return None
        return {"ok": row[0], "new_count": row[1], "detail": json.loads(row[2] or "{}")}

    def _count(self, sql, args=()):
        con = sqlite3.connect(self.dbpath)
        try:
            return con.execute(sql, args).fetchone()[0]
        finally:
            con.close()

    def _run(self, argv=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = J.main(list(argv or []))
        return rc, out.getvalue(), err.getvalue()

    def _patch_read(self, side_effect):
        p = mock.patch.object(J.dm, "read", side_effect=side_effect)
        p.start()
        self.addCleanup(p.stop)

    def _patch_judge(self, side_effect):
        p = mock.patch.object(J.news_ai, "jev_judge_batch", side_effect=side_effect)
        p.start()
        self.addCleanup(p.stop)

    # ------------------------------------------------------------------ 用例
    def test_source_exception_marks_runs_failed_and_no_watermark(self):
        """一源抛异常：ok=0 / rc=1 / FEED FAILED / 该源水位不推进。"""
        self._patch_read([RuntimeError("tushare 连接超时"), _empty_df()])
        rc, out, err = self._run()
        self.assertEqual(rc, 1, "源失败必须 rc=1（原实现恒 0）")
        run = self._latest_run()
        self.assertEqual(run["ok"], 0, "源失败必须 runs.ok=0（原实现恒 1）")
        errs = run["detail"]["errors"]
        self.assertEqual(len(errs), 1)
        self.assertEqual(errs[0]["source"], "news")
        self.assertEqual(errs[0]["stage"], "feed")
        self.assertIn("tushare 连接超时", errs[0]["error"])
        self.assertEqual(run["detail"]["wm"], {}, "失败源不得推进水位")
        self.assertIn("FEED FAILED", out)
        self.assertIn("FEED FAILED", err)

    def test_none_return_is_source_failure(self):
        """dm.read 返回 None = 源不可用/未就绪 → 失败可见（不是成功空数据）。"""
        self._patch_read([None, _empty_df()])
        rc, _out, _err = self._run()
        self.assertEqual(rc, 1)
        run = self._latest_run()
        self.assertEqual(run["ok"], 0)
        errs = run["detail"]["errors"]
        self.assertEqual(errs[0]["source"], "news")
        self.assertIn("返回 None", errs[0]["error"])

    def test_all_empty_is_success(self):
        """真实空数据（空 DataFrame，含首跑无增量）→ ok=1 / rc=0。"""
        self._patch_read([_empty_df(), _empty_df()])
        self._patch_judge(lambda items: {})
        rc, out, _err = self._run()
        self.assertEqual(rc, 0)
        run = self._latest_run()
        self.assertEqual(run["ok"], 1)
        self.assertEqual(run["detail"]["errors"], [])
        self.assertEqual(run["new_count"], 0)
        self.assertIn("FEED done", out)

    def test_failed_source_keeps_wm_success_source_advances(self):
        """news 失败、notice 成功：只推进 notice 水位；notice 行仍入库。"""

        def read(alias, **_kw):
            if alias == "news":
                raise TimeoutError("news 源不可达")
            return _one_row_df()

        self._patch_read(read)
        self._patch_judge(lambda items: {it["id"]: {"bias": "bull"} for it in items})
        rc, _out, _err = self._run()
        self.assertEqual(rc, 1)
        run = self._latest_run()
        self.assertEqual(run["ok"], 0)
        self.assertEqual(
            run["detail"]["wm"], {"notice": "20261025"}, "仅成功源可推进水位"
        )
        self.assertNotIn("news", run["detail"]["wm"])
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM news WHERE source='xd_notice'"), 1
        )
        self.assertEqual(run["detail"]["src"]["notice"]["judged"], 1)
        self.assertEqual(self._count("SELECT COUNT(*) FROM runs"), 1)

    def test_judge_all_fail_marks_runs_failed(self):
        """判定阶段整批 0 条判成（模型挂）→ ok=0 / stage=judge（不再静默计成功）。"""
        self._patch_read([_empty_df(), _one_row_df()])
        self._patch_judge(lambda items: {})
        rc, out, _err = self._run()
        self.assertEqual(rc, 1)
        run = self._latest_run()
        self.assertEqual(run["ok"], 0)
        errs = run["detail"]["errors"]
        self.assertEqual(errs[0]["stage"], "judge")
        self.assertEqual(run["detail"]["src"]["notice"].get("judged"), None)
        # 判定失败不影响数据层水位（数据已入库，下轮按 jev IS NULL 重判）
        self.assertEqual(run["detail"]["wm"], {"notice": "20261025"})
        self.assertIn("FEED FAILED", out)

    def test_partial_judge_missing_is_recorded_not_fatal(self):
        """单条判定失败：judge_missing 留明细，不把整轮判失败（避免噪声红）。"""
        two_rows = pd.DataFrame(
            [
                NOTICE_ROW,
                dict(
                    NOTICE_ROW,
                    url="https://example.test/notice/2",
                    title="另一条公告",
                    notice_date="20261025",
                ),
            ]
        )
        self._patch_read([_empty_df(), two_rows])
        self._patch_judge(
            lambda items: {items[0]["id"]: {"bias": "bull"}} if items else {}
        )
        rc, _out, _err = self._run()
        self.assertEqual(rc, 0, "单条失败不应整轮失败")
        run = self._latest_run()
        self.assertEqual(run["ok"], 1)
        self.assertEqual(run["detail"]["src"]["notice"]["judge_missing"], 1)
        self.assertEqual(run["detail"]["src"]["notice"]["judged"], 1)

    def test_dryrun_writes_no_runs_row(self):
        """--dryrun 保持原语义：不写 runs、rc=0。"""
        self._patch_read([_empty_df(), _empty_df()])
        rc, out, _err = self._run(["--dryrun"])
        self.assertEqual(rc, 0)
        self.assertEqual(self._count("SELECT COUNT(*) FROM runs"), 0)
        self.assertIn("DRYRUN", out)

    # ------------------------------------- OS-FIX-JEVFEEDNIGHT 夜间守卫
    def test_in_night_window_boundaries(self):
        from datetime import datetime

        check = self._real_in_night_window
        # 隔离本机真实 pause/lock（夜批正在跑时文件存在，不应影响纯时间窗断言）
        with (
            mock.patch.object(J, "NIGHT_PAUSE_FLAG", "Z:/nonexistent-pause"),
            mock.patch.object(J, "NIGHT_LOCK", "Z:/nonexistent-lock"),
        ):
            self.assertTrue(check(datetime(2026, 10, 27, 21, 45)))
            self.assertTrue(check(datetime(2026, 10, 27, 23, 0)))
            self.assertTrue(check(datetime(2026, 10, 27, 3, 0)))
            self.assertFalse(check(datetime(2026, 10, 27, 7, 5)))
            self.assertFalse(check(datetime(2026, 10, 27, 12, 0)))
            with mock.patch.dict(os.environ, {"JEV_FEED_FORCE_JUDGE": "1"}):
                self.assertFalse(check(datetime(2026, 10, 27, 23, 0)))

    def test_judge_pending_skips_in_night_window(self):
        """夜间窗口内 judge_pending 不判读（数据保留 jev IS NULL 给夜批）。"""
        con = sqlite3.connect(self.dbpath)
        try:
            con.execute(
                "INSERT INTO news(id, source, ts, title, stocks, jev) "
                "VALUES (?,?,?,?,?,NULL)",
                ("xd:news:night1", "xd_news", "2026-10-26 22:00", "夜间待判新闻", "[]"),
            )
            con.commit()
        finally:
            con.close()
        self._patch_judge(lambda items: {"不该被调用": {"bias": "bull"}})
        with mock.patch.object(J, "_in_night_window", lambda: True):
            res = J.judge_pending("news", 10)
        self.assertEqual(res["judged"], 0)
        self.assertTrue(res.get("skipped_night"))
        self.assertEqual(
            self._count("SELECT COUNT(*) FROM news WHERE jev IS NULL"),
            1,
            "跳读不应改库，夜批仍能按 jev IS NULL 取到",
        )


if __name__ == "__main__":
    unittest.main()
