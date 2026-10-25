# -*- coding: utf-8 -*-
"""night_batch 无推进退避重试回归（K-ORG-20261025-NIGHTBATCH）。

背景：原逻辑"连续 2 轮无推进即退出"。2026-10-25 09:21 实测，一次瞬时故障
（JEV 云侧空返 + 与 M3 抢 ollama 锁）就让 2 万条积压停摆 1 小时（09:21–10:40）。
改为线性退避重试后，瞬时故障只会让本轮慢下来，不会让整条长跑退出。
"""
from __future__ import annotations

import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import night_batch  # noqa: E402


def test_retry_until_limit_then_give_up():
    assert night_batch.stall_decision(1, 3, 300) == (True, 300.0)
    assert night_batch.stall_decision(2, 3, 300) == (True, 600.0)
    keep, wait = night_batch.stall_decision(3, 3, 300)
    assert keep is False and wait == 0.0


def test_zero_retries_disables_retry():
    assert night_batch.stall_decision(1, 0, 300) == (False, 0.0)


def test_backoff_scales_with_count_and_base():
    assert night_batch.stall_decision(1, 5, 60) == (True, 60.0)
    assert night_batch.stall_decision(3, 5, 60) == (True, 180.0)


def test_negative_backoff_clamped_to_zero():
    keep, wait = night_batch.stall_decision(1, 3, -10)
    assert keep is True and wait == 0.0
