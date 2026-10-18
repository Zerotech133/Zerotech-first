# -*- coding: utf-8 -*-
"""xq_sleep_aware 单测（2026-10-19）：睡眠窗配对 / 净时长 / 三态判活。

用注入假事件列表的方式测试（不实查事件日志、不 mock time.sleep——
判例：全局 patch time.sleep 会引爆别处 daemon 僵尸线程）。
文件 mtime 用 os.utime 注入，时间戳全部相对 now 动态生成
（判例：夹具时间参与新鲜度判定一律动态生成）。
跑法：pytest quant_backtest/tests/test_xq_sleep_aware.py -q
"""
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import xq_sleep_aware as sa

SLEEP, WAKE = 42, 1


def ev(id_, minutes_ago, now):
    return (id_, now - timedelta(minutes=minutes_ago))


def test_pair_basic_two_events():
    now = datetime.now()
    events = [ev(SLEEP, 150, now), ev(WAKE, 20, now)]
    windows = sa.pair_sleep_windows(events, now=now)
    assert len(windows) == 1
    assert windows[0] == (now - timedelta(minutes=150), now - timedelta(minutes=20))


def test_pair_unclosed_falls_back_to_now():
    now = datetime.now()
    events = [ev(SLEEP, 30, now)]  # 只睡未醒
    windows = sa.pair_sleep_windows(events, now=now)
    assert windows == [(now - timedelta(minutes=30), now)]


def test_pair_multiple_windows_and_orphan_wake():
    now = datetime.now()
    events = [
        ev(WAKE, 500, now),             # 无主唤醒，忽略
        ev(SLEEP, 400, now), ev(WAKE, 380, now),
        ev(SLEEP, 100, now), ev(WAKE, 40, now),
    ]
    windows = sa.pair_sleep_windows(events, now=now)
    assert windows == [
        (now - timedelta(minutes=400), now - timedelta(minutes=380)),
        (now - timedelta(minutes=100), now - timedelta(minutes=40)),
    ]


def test_effective_runtime_subtracts_sleep():
    now = datetime.now()
    start, end = now - timedelta(hours=4), now
    windows = [(now - timedelta(hours=2), now - timedelta(hours=1))]  # 睡 1h
    assert sa.effective_runtime(start, end, windows=windows) == timedelta(hours=3)
    # 睡眠窗外无重叠：不扣
    windows_out = [(now - timedelta(hours=10), now - timedelta(hours=9))]
    assert sa.effective_runtime(start, end, windows=windows_out) == timedelta(hours=4)


def test_sleep_windows_filters_by_hours():
    now = datetime.now()
    events = [
        ev(SLEEP, 100, now), ev(WAKE, 90, now),   # 在 2h 内
        ev(SLEEP, 500, now), ev(WAKE, 480, now),  # 在 2h 外
    ]
    windows = sa.sleep_windows(2.0, events=events, now=now)
    assert windows == [(now - timedelta(minutes=100), now - timedelta(minutes=90))]


def test_is_frozen_alive_three_states():
    now = datetime.now()
    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "heartbeat.tmp")
        open(path, "w").close()
        # ALIVE：5 分钟前刚写过
        os.utime(path, ((now - timedelta(minutes=5)).timestamp(),) * 2)
        assert sa.is_frozen_alive(path, max_idle_min=20, windows=[], now=now) == sa.ALIVE
        # STALLED_SLEEP：mtime 落在睡眠窗内，裸 idle 3h，扣除 2.5h 睡眠后净 30min>20min？
        # 构造：mtime=窗内(睡前1h 处)，窗再睡 2h，净 idle=1h-0? 精确算：
        # 窗 [now-2h, now-10min]，mtime=now-1h（窗内），覆盖 (mtime,now]∩窗=50min，
        # 裸 idle=60min，净=10min ≤ 20min → STALLED_SLEEP
        os.utime(path, ((now - timedelta(minutes=60)).timestamp(),) * 2)
        windows = [(now - timedelta(hours=2), now - timedelta(minutes=10))]
        assert sa.is_frozen_alive(path, max_idle_min=20, windows=windows, now=now) == sa.STALLED_SLEEP
        # DEAD：mtime 3h 前，无睡眠窗可解释
        os.utime(path, ((now - timedelta(hours=3)).timestamp(),) * 2)
        assert sa.is_frozen_alive(path, max_idle_min=20, windows=[], now=now) == sa.DEAD


def test_is_frozen_alive_missing_file_is_dead():
    now = datetime.now()
    assert sa.is_frozen_alive("Z:/no/such/file.xyz", windows=[], now=now) == sa.DEAD


def test_non_windows_sleep_windows_empty(monkeypatch):
    monkeypatch.setattr(sa, "IS_WINDOWS", False)
    now = datetime.now()
    events = [ev(SLEEP, 30, now), ev(WAKE, 10, now)]
    assert sa.sleep_windows(2.0, events=events, now=now) == []
