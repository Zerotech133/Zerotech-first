# -*- coding: utf-8 -*-
"""preflight-dev 单测：xq_preflight_check 判定逻辑（注入假数据，不碰调度面）。"""

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "research"))

from xq_preflight_check import (  # noqa: E402
    judge_last_result,
    judge_next_run,
    judge_heartbeat,
    judge_watch_form,
    parse_sched_time,
)

PY = "C:/Users/t/.workbuddy/binaries/python/envs/default/Scripts/python.exe"


class TestParseSchedTime:
    def test_com_string_with_fake_utc_suffix(self):
        # COM 返回本地时间但带误导性 "+00:00" 尾巴，须取 naive 部分
        assert parse_sched_time("2026-10-18 18:00:00+00:00") == datetime(
            2026, 10, 18, 18, 0, 0
        )

    def test_iso_t_boundary(self):
        assert parse_sched_time("2026-10-18T19:35:00") == datetime(
            2026, 10, 18, 19, 35, 0
        )

    def test_garbage_returns_none(self):
        assert parse_sched_time("never") is None
        assert parse_sched_time("") is None
        assert parse_sched_time(None) is None


class TestJudgeNextRun:
    def test_exact_match_pass(self):
        r = judge_next_run("2026-10-18 18:00:00+00:00", datetime(2026, 10, 18, 18, 0))
        assert r["status"] == "PASS"

    def test_within_tolerance_pass(self):
        r = judge_next_run(
            "2026-10-18 19:05:30", datetime(2026, 10, 18, 19, 5), tolerance_min=1
        )
        assert r["status"] == "PASS"

    def test_off_by_minutes_fail(self):
        r = judge_next_run(
            "2026-10-18 19:00:00", datetime(2026, 10, 18, 19, 5), tolerance_min=0
        )
        assert r["status"] == "FAIL"

    def test_wrong_day_fail(self):
        r = judge_next_run("2026-10-19 13:30:00", datetime(2026, 10, 18, 19, 35))
        assert r["status"] == "FAIL"

    def test_unparseable_unknown(self):
        r = judge_next_run("never", datetime(2026, 10, 18, 19, 35))
        assert r["status"] == "UNKNOWN"


class TestJudgeLastResult:
    def test_zero_pass(self):
        assert judge_last_result(0)["status"] == "PASS"

    def test_not_yet_run_pass(self):
        # 0x41303 = 任务尚未运行过（守望每日化后首跑前属正常形态）
        assert judge_last_result(267011)["status"] == "PASS"

    def test_path_not_found_fail(self):
        r = judge_last_result(3)
        assert r["status"] == "FAIL"
        assert "0x3" in r["detail"]


class TestJudgeWatchForm:
    TASK_TRIGGER_DAILY = 2
    TASK_TRIGGER_TIME = 1

    def test_daily_and_correct_next_pass(self):
        r = judge_watch_form(
            self.TASK_TRIGGER_DAILY,
            "2026-10-18 19:35:00+00:00",
            datetime(2026, 10, 18, 19, 35),
        )
        assert r["status"] == "PASS"

    def test_once_trigger_fail(self):
        # 每日化改动前是 OneTime，若仍是 TIME 触发器则形态不达标
        r = judge_watch_form(
            self.TASK_TRIGGER_TIME,
            "2026-10-18 19:35:00+00:00",
            datetime(2026, 10, 18, 19, 35),
        )
        assert r["status"] == "FAIL"
        assert "daily" in r["detail"].lower() or "每日" in r["detail"]

    def test_daily_but_wrong_time_fail(self):
        r = judge_watch_form(
            self.TASK_TRIGGER_DAILY,
            "2026-10-18 19:00:00+00:00",
            datetime(2026, 10, 18, 19, 35),
        )
        assert r["status"] == "FAIL"


class TestJudgeHeartbeat:
    def _judge(self, line, mtime_dt, now_dt):
        return judge_heartbeat(line, mtime_dt, now_dt, stale_minutes=30)

    def test_fresh_and_ok_pass(self):
        now = datetime(2026, 10, 18, 16, 10)
        r = self._judge(
            "2026-10-18 15:59:16 | retry-degraded end rc=0 upgraded=16 still_degraded=0",
            now - timedelta(minutes=11),
            now,
        )
        assert r["status"] == "PASS"

    def test_stale_hard_fail_unknown(self):
        now = datetime(2026, 10, 18, 16, 10)
        r = self._judge(
            "2026-10-18 15:34:00 | RETRY-HARD-FAIL id=123",
            now - timedelta(minutes=36),
            now,
        )
        assert r["status"] == "UNKNOWN"
        assert "16:00" in r["detail"]

    def test_stale_silence_unknown(self):
        now = datetime(2026, 10, 18, 16, 10)
        r = self._judge(
            "2026-10-18 14:00:00 | OK id=1", now - timedelta(minutes=130), now
        )
        assert r["status"] == "UNKNOWN"

    def test_fresh_but_fail_marker(self):
        now = datetime(2026, 10, 18, 16, 10)
        r = self._judge(
            "2026-10-18 16:05:00 | hard_fail=3", now - timedelta(minutes=5), now
        )
        assert r["status"] == "FAIL"

    def test_no_mtime_unknown(self):
        r = self._judge("", None, datetime(2026, 10, 18, 16, 10))
        assert r["status"] == "UNKNOWN"
