# -*- coding: utf-8 -*-
"""OS-FIX-JEVLOCK-20261026：ollama_lock.tick 阻塞上限回归。

依据：`docs/audit/2026-10-26_JEV板块设计与实现审计.md` §四-③：
旧 tick 最坏阻塞 = max_tries 6 × (TICK_WAIT_S 900 + 5) + YIELD_S 20 ≈ 5450s，
远超子任务 hard timeout（JEV 夜间 deadline 1500 / 小时轮 1800，外层 +300/+600）
→ 被硬杀即丢组 + 锁遗留。修复：tick 接受 `deadline_ts`，单次等待/冷却都不得
越过它；未传时用 TICK_TOTAL_BUDGET_S(1200s) 兜底；超时打字段日志后返回 False。
本测试全部用桩 acquire/小数值，不碰真锁文件、不 sleep 真时长。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import ollama_lock  # noqa: E402


def _force_yield(monkeypatch):
    monkeypatch.setattr(ollama_lock, "should_yield", lambda lease_s=None: True)


def _never_acquire(monkeypatch):
    calls = []

    def _acq(purpose, wait_s=0):
        calls.append(wait_s)
        return False

    monkeypatch.setattr(ollama_lock, "acquire", _acq)
    return calls


def test_no_wait_when_lease_not_full(monkeypatch):
    calls = []
    monkeypatch.setattr(ollama_lock, "should_yield", lambda lease_s=None: False)
    monkeypatch.setattr(ollama_lock, "acquire",
                        lambda purpose, wait_s=0: calls.append(1) or True)
    assert ollama_lock.tick("jev_backfill", deadline_ts=time.time() + 60) is True
    assert calls == []


def test_tick_returns_before_deadline(monkeypatch, capsys):
    _force_yield(monkeypatch)
    _never_acquire(monkeypatch)
    t0 = time.time()
    ok = ollama_lock.tick("jev_backfill", yield_s=0.01, wait_s=5.0,
                          max_tries=100, deadline_ts=t0 + 0.15)
    elapsed = time.time() - t0
    assert ok is False
    assert elapsed < 1.0, "tick 必须在 deadline 附近返回（实测 %.2fs）" % elapsed
    out = capsys.readouterr().out
    assert "OLLAMA_LOCK tick timeout" in out
    assert "purpose=jev_backfill" in out
    assert "tries=" in out and "deadline_left=" in out and "waited=" in out


def test_deadline_already_passed_returns_immediately(monkeypatch, capsys):
    _force_yield(monkeypatch)
    _never_acquire(monkeypatch)
    t0 = time.time()
    assert ollama_lock.tick("jev_backfill", yield_s=0.01, wait_s=5.0,
                            deadline_ts=t0 - 1.0) is False
    assert time.time() - t0 < 1.0
    assert "tick timeout" in capsys.readouterr().out


def test_default_total_budget_caps_legacy_worst_case(monkeypatch, capsys):
    """未传 deadline_ts 的旧调用方（如 M3）也必须有总等待上限，防 5450s 阻塞。"""
    _force_yield(monkeypatch)
    _never_acquire(monkeypatch)
    monkeypatch.setattr(ollama_lock, "TICK_TOTAL_BUDGET_S", 0.12)
    t0 = time.time()
    assert ollama_lock.tick("k266_m3", yield_s=0.01, wait_s=5.0,
                            max_tries=100) is False
    elapsed = time.time() - t0
    assert elapsed < 1.0, "总预算未生效（实测 %.2fs）" % elapsed
    assert "tick timeout" in capsys.readouterr().out


def test_tick_reacquires_after_giving_way(monkeypatch):
    """正常路径不变：让位后抢回成功 → True，且不打印超时日志。"""
    _force_yield(monkeypatch)
    monkeypatch.setattr(ollama_lock, "release", lambda: None)
    seq = {"n": 0}

    def _acq(purpose, wait_s=0):
        seq["n"] += 1
        return seq["n"] >= 2            # 第一次让位后失败，第二次抢回

    monkeypatch.setattr(ollama_lock, "acquire", _acq)
    assert ollama_lock.tick("jev_backfill", yield_s=0.0, wait_s=0.01,
                            max_tries=5) is True
    assert seq["n"] == 2


def test_tick_wait_never_exceeds_remaining_budget(monkeypatch):
    """每次 acquire 的 wait_s 必须被剩余预算夹住（不单次睡满 900s）。"""
    _force_yield(monkeypatch)
    monkeypatch.setattr(ollama_lock, "TICK_DEADLINE_MARGIN_S", 0.0)
    waits = _never_acquire(monkeypatch)
    t0 = time.time()
    assert ollama_lock.tick("jev_backfill", yield_s=0.01, wait_s=900.0,
                            max_tries=100, deadline_ts=t0 + 0.2) is False
    elapsed = time.time() - t0
    assert waits, "acquire 至少被调用一次"
    assert max(waits) <= 0.25, "单次等待未被 deadline 夹住: %r" % waits
    assert elapsed < 1.5, "总等待未被 deadline 夹住（实测 %.2fs）" % elapsed


def test_constants_kept_below_hard_timeouts():
    """兜底预算必须小于子任务硬超时下限（JEV 夜间 deadline 1500s）。"""
    assert ollama_lock.TICK_TOTAL_BUDGET_S < 1500
