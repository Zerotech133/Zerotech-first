# -*- coding: utf-8 -*-
"""miss_check 的"刚醒宽限"单测（2026-10-24）。

背景：本哨兵用 `now` 判断是否已过宽限期，而 `now` 会被挂起/睡醒打乱。
实测（WO-20261024-02）：13:50:35→14:08:40 挂起 18.1 分钟、15:32:45→15:42:45
挂起 10.0 分钟，醒来后系统时间被硬件钟一次性纠正。跳变直接跨过 deadline，
而 Task Scheduler 的 StartWhenAvailable 还在补跑 → 假 MISS。

这里只测纯函数 `wake_grace_minutes`，以及 `_recent_resume_minutes` 的失败
退化路径（不依赖真实事件日志、不调 schtasks、不发告警）。

跑法：pytest quant_backtest/tests/test_miss_check_wake_grace.py -q
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import miss_check as mc


def test_no_suspend_means_no_extra_grace():
    """没睡过（查不到恢复时间）不加宽限——不能因为探测器坏了就一律放行。"""
    assert mc.wake_grace_minutes(None) == 0.0


def test_just_woke_gets_full_settle_window():
    assert mc.wake_grace_minutes(0) == mc.RESUME_SETTLE_MIN
    assert mc.wake_grace_minutes(5) == mc.RESUME_SETTLE_MIN - 5


def test_grace_shrinks_linearly_toward_settle_window():
    half = mc.RESUME_SETTLE_MIN / 2.0
    assert mc.wake_grace_minutes(half) == pytest.approx(half, abs=1e-6)


def test_grace_disappears_after_settle_window():
    """过了结算窗口必须归零：真漏跑不许被"刚醒"长期掩护。"""
    assert mc.wake_grace_minutes(mc.RESUME_SETTLE_MIN) == 0.0
    assert mc.wake_grace_minutes(mc.RESUME_SETTLE_MIN + 120) == 0.0


def test_negative_minutes_clamped_to_zero():
    """时钟回拨可能算出负数分钟：按"刚醒"处理，不产生超过窗口的宽限。"""
    assert mc.wake_grace_minutes(-10) == mc.RESUME_SETTLE_MIN


def test_resume_probe_degrades_to_none_on_failure(monkeypatch):
    """powershell 不可用/超时 → 返回 None（不加宽限），绝不抛异常打断巡检。"""

    def boom(*args, **kwargs):
        raise OSError("powershell missing")

    monkeypatch.setattr(mc.subprocess, "run", boom)
    assert mc._recent_resume_minutes() is None


def test_resume_probe_ignores_small_clock_corrections(monkeypatch):
    """普通对时（<60s）不算挂起：否则每一轮都会白送 45 分钟宽限。"""

    class FakeProc:
        stdout = (
            b'[{"Id":1,"at":"2026-10-24 16:00:00",'
            b'"Message":"time changed by 300 ms"}]'
        )
        returncode = 0

    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: FakeProc())
    assert mc._recent_resume_minutes() is None


def test_resume_probe_counts_large_correction(monkeypatch):
    """≥60s 的时钟纠正 = 刚才睡过，且时长可信。"""

    class FakeProc:
        stdout = (
            b'[{"Id":1,"at":"2026-10-24 14:08:40",'
            b'"Message":"time changed by 1084752 ms"}]'
        )
        returncode = 0

    monkeypatch.setattr(mc.subprocess, "run", lambda *a, **k: FakeProc())
    from datetime import datetime
    minutes = mc._recent_resume_minutes(datetime(2026, 10, 24, 14, 38, 40))
    assert minutes == pytest.approx(30.0, abs=0.01)
