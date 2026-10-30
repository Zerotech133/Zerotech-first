# -*- coding: utf-8 -*-
"""JEV 回填主循环控制函数单测（K-ORG-20261026-JEVLOOPTEST）。

覆盖主循环里最容易静默出错、又不依赖 DB/Ollama 的控制面：
  · 条间 sleep 解析与夹取（JEV_ITEM_SLEEP_S）
  · M3 暂停旗判定
  · 灰度开关 JEV_PREFILTER / 让位回滚 JEV_LOCK_YIELD_PAUSED
  · 租约 tick/heartbeat 分支（M3 暂停时不让位、deadline_ts 透传）
  · 批判 sleep 的 8 条上限 / 失败重试一次
  · 积压告警节流（_alert_ok/_alert_mark）
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "news_collector"))

import jev_backfill as jb  # noqa: E402


# ── sleep 解析 ────────────────────────────────────────────────
def test_item_sleep_default(monkeypatch):
    monkeypatch.delenv("JEV_ITEM_SLEEP_S", raising=False)
    assert jb._item_sleep_seconds() == 0.2


def test_item_sleep_valid(monkeypatch):
    monkeypatch.setenv("JEV_ITEM_SLEEP_S", "1.5")
    assert jb._item_sleep_seconds() == 1.5


def test_item_sleep_clamped(monkeypatch):
    monkeypatch.setenv("JEV_ITEM_SLEEP_S", "99")
    assert jb._item_sleep_seconds() == 5.0
    monkeypatch.setenv("JEV_ITEM_SLEEP_S", "-3")
    assert jb._item_sleep_seconds() == 0.0


def test_item_sleep_invalid_falls_back(monkeypatch):
    for bad in ("abc", "nan", ""):
        monkeypatch.setenv("JEV_ITEM_SLEEP_S", bad)
        assert jb._item_sleep_seconds() == 0.2


# ── 暂停旗 ────────────────────────────────────────────────────
def test_m3_paused(tmp_path):
    flag = tmp_path / "m3.pause"
    assert jb.m3_paused(flag) is False
    flag.write_text("1", encoding="utf-8")
    assert jb.m3_paused(flag) is True


# ── 灰度/让位开关 ─────────────────────────────────────────────
@pytest.mark.parametrize("value,expected", [
    ("1", True), ("true", True), ("YES", True), ("on", True),
    ("0", False), ("", False), ("no", False), ("off", False),
])
def test_prefilter_enabled(monkeypatch, value, expected):
    monkeypatch.setenv("JEV_PREFILTER", value)
    assert jb.prefilter_enabled() is expected


@pytest.mark.parametrize("value,expected", [
    ("1", False), ("true", False),
    ("0", True), ("false", True), ("no", True), ("off", True),
])
def test_lease_yield_disabled(monkeypatch, value, expected):
    monkeypatch.setenv("JEV_LOCK_YIELD_PAUSED", value)
    assert jb._lease_yield_disabled() is expected


def test_should_skip_lease_yield():
    assert jb._should_skip_lease_yield(True, False) is True
    assert jb._should_skip_lease_yield(False, False) is False
    assert jb._should_skip_lease_yield(True, True) is False
    assert jb._should_skip_lease_yield(False, True) is False


# ── 租约 tick 分支 ────────────────────────────────────────────
class _FakeLock:
    def __init__(self):
        self.hb = 0
        self.tick_calls = []

    def heartbeat(self):
        self.hb += 1

    def tick(self, purpose, deadline_ts=None):
        self.tick_calls.append((purpose, deadline_ts))
        return True


def test_locked_tick_heartbeats_when_m3_paused():
    lock = _FakeLock()
    assert jb._locked_tick(lock, "jeval", paused=True, disabled=False) is True
    assert lock.hb == 1
    assert lock.tick_calls == []


def test_locked_tick_uses_tick_with_deadline():
    lock = _FakeLock()
    assert jb._locked_tick(lock, "jeval", paused=True, disabled=True,
                           deadline_ts=123.0) is True
    assert lock.hb == 0
    assert lock.tick_calls == [("jeval", 123.0)]


def test_locked_tick_uses_tick_without_deadline():
    lock = _FakeLock()
    assert jb._locked_tick(lock, "jeval", paused=False, disabled=False) is True
    assert lock.tick_calls == [("jeval", None)]


# ── 批判 sleep（8 条上限 + 失败重试一次） ────────────────────
def test_judge_batch_sleep_retry_and_cap(monkeypatch):
    calls = []

    def fake_judge(title, stocks):
        calls.append(title)
        if len(calls) == 1:
            return None                      # 首次失败 → 应重试一次
        return {"bias": "bull"}

    monkeypatch.setattr(jb.news_ai, "jev_judge_one", fake_judge)
    items = [{"id": i, "title": "t%d" % i, "stocks": []} for i in range(10)]
    out = jb._judge_batch_sleep(items, sleep_s=0)
    assert calls[0] == "t0" and calls[1] == "t0"   # 第一条失败后重试
    assert set(out.keys()) == set(range(8))         # 8 条上限：只判前 8 条
    assert len(calls) == 9                          # 1 失败 + 重试 1 + 其余 7


# ── 告警节流 ──────────────────────────────────────────────────
def test_alert_throttle(tmp_path, monkeypatch):
    state = tmp_path / "alert_state.json"
    monkeypatch.setattr(jb, "ALERT_STATE", str(state))
    monkeypatch.setattr(jb, "ALERT_COOLDOWN_S", 6 * 3600)
    assert jb._alert_ok(throttle=False) is True
    assert jb._alert_ok(throttle=True) is True      # 文件缺失 → 可告警
    jb._alert_mark()
    assert jb._alert_ok(throttle=True) is False     # 刚发过 → 节流
    state.write_text('{"last_alert_ts": 0}', encoding="utf-8")
    assert jb._alert_ok(throttle=True) is True      # 冷却已过


def test_judge_batch_sleep_honours_deadline(monkeypatch):
    import time as _time
    import jev_backfill as jb
    calls = {"n": 0}

    def fake_judge(*a, **k):
        calls["n"] += 1
        return {"bias": "bull", "conf": 0.9}

    monkeypatch.setattr(jb.news_ai, "jev_judge_one", fake_judge)
    items = [{"id": "n%d" % i, "title": "t", "stocks": []} for i in range(8)]
    out = jb._judge_batch_sleep(items, sleep_s=0, deadline_ts=_time.time() - 1)
    assert out == {}
    assert calls["n"] == 0
