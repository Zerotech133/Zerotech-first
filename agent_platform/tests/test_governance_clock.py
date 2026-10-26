"""治理时钟测试（WO-LEGION-CLOCK-20261026）。

覆盖：锚点解析、到点触发一次（幂等/去重）、错过超时不补跑、心跳落盘、
日志 JSONL、单实例锁（含陈旧锁回收）、dry-run。
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from ops import governance_clock as GC  # noqa: E402


def _ts_at(hour: int, minute: int) -> float:
    now = dt.datetime.now().astimezone()
    return now.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp()


class _Runner:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd):
        self.calls.append(list(cmd))
        return {"ok": True, "rc": 0, "out_tail": "stub"}


def test_parse_anchors_ok_and_bad():
    assert GC.parse_anchors("08:46:scan,18:05:closeout") == [
        ("08:46", "scan"), ("18:05", "closeout")]
    with pytest.raises(ValueError):
        GC.parse_anchors("08:46")
    with pytest.raises(ValueError):
        GC.parse_anchors("08:46:nope")


def test_tick_fires_scan_once(tmp_path):
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "clock.log"
    runner = _Runner()
    state = {}
    anchors = GC.parse_anchors("08:46:scan,18:05:closeout")
    now = _ts_at(8, 47)

    fired = GC.run_tick(state, now_fn=lambda: now, runner=runner,
                        anchors=anchors, state_path=state_path,
                        log_path=log_path)
    assert [r["action"] for r in fired] == ["scan"]
    assert len(runner.calls) == 1
    assert state["last_tick"] == now
    assert state_path.is_file()
    lines = [json.loads(x) for x in log_path.read_text(encoding="utf-8").splitlines()]
    assert lines and lines[0]["action"] == "scan" and lines[0]["ok"] is True

    # 同一 tick 再跑：不重复触发（day|HH:MM 去重）
    fired2 = GC.run_tick(state, now_fn=lambda: now, runner=runner,
                         anchors=anchors, state_path=state_path,
                         log_path=log_path)
    assert fired2 == [] and len(runner.calls) == 1


def test_closeout_anchor_and_missed(tmp_path):
    runner = _Runner()
    state = {}
    anchors = GC.parse_anchors("08:46:scan,18:05:closeout")
    now = _ts_at(18, 6)
    fired = GC.run_tick(state, now_fn=lambda: now, runner=runner,
                        anchors=anchors, state_path=tmp_path / "s.json",
                        log_path=tmp_path / "c.log")
    actions = [r["action"] for r in fired]
    assert "closeout" in actions
    # 08:46 已过 >120min：不补跑，记 skipped_missed
    scan_rec = [r for r in fired if r["action"] == "scan"][0]
    assert scan_rec.get("skipped_missed") is True
    assert len(runner.calls) == 1


def test_missed_anchor_does_not_run(tmp_path):
    runner = _Runner()
    state = {}
    anchors = GC.parse_anchors("08:46:scan,18:05:closeout")
    now = _ts_at(12, 0)  # 08:46 已过 194min；18:05 未到
    fired = GC.run_tick(state, now_fn=lambda: now, runner=runner,
                        anchors=anchors, state_path=tmp_path / "s.json",
                        log_path=tmp_path / "c.log")
    assert runner.calls == []
    assert len(fired) == 1 and fired[0].get("skipped_missed") is True
    assert any(k.endswith("|08:46") for k in state["fired"])


def test_dry_run_does_not_execute(tmp_path):
    runner = _Runner()
    state = {}
    now = _ts_at(8, 47)
    fired = GC.run_tick(state, now_fn=lambda: now, runner=runner, dry_run=True,
                        anchors=GC.parse_anchors("08:46:scan"),
                        state_path=tmp_path / "s.json")
    assert runner.calls == []
    assert fired[0]["dry_run"] is True


def test_lock_single_instance_and_stale(tmp_path):
    lock = tmp_path / "clock.lock"
    first = GC.acquire_lock(lock)
    assert first is not None and lock.is_file()
    assert GC.acquire_lock(lock) is None  # 已被持有
    GC.release_lock(first)
    assert not lock.exists()
    # 陈旧锁（>6h）自动回收
    lock.write_text("{}", encoding="utf-8")
    old = 0  # epoch 0 -> 极旧
    os.utime(lock, (old, old))
    assert GC.acquire_lock(lock) is not None
    GC.release_lock(lock)


def test_loaded_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    GC.save_state(p, {"last_tick": 1.5, "fired": {"2026-10-26|08:46": {"ok": True}}})
    loaded = GC.load_state(p)
    assert loaded["last_tick"] == 1.5
    assert "2026-10-26|08:46" in loaded["fired"]
    # 坏 JSON 不抛，按空态处理
    p.write_text("{broken", encoding="utf-8")
    assert GC.load_state(p) == {}


# ---------------- WO-LEGION-CLOCKANCHOR-20261026：17:50 预扫描 ----------------

def test_default_anchors_include_prescan_and_ai():
    """默认锚点：08:46 扫描、17:50 预扫描、18:05 收口、18:10 AI 建议（GOVAI-2）。"""
    anchors = GC.parse_anchors(GC.DEFAULT_ANCHORS)
    assert anchors == [("08:46", "scan"), ("17:50", "scan"),
                       ("18:05", "closeout"), ("18:10", "ai_advice")]
    assert [a for a, act in anchors if act == "scan"] == ["08:46", "17:50"]


def test_ai_advice_action_maps_to_gov_ai():
    cmd = " ".join(GC.ACTIONS["ai_advice"])
    assert "gov_ai.py" in cmd and "--action" in cmd and "all" in cmd


def test_ai_advice_fires_after_closeout(tmp_path):
    """18:11：17:50 预扫（21min 内仍补跑）、18:05 收口、18:10 AI 建议各触发一次。"""
    runner = _Runner()
    state = {}
    fired = GC.run_tick(state, now_fn=lambda: _ts_at(18, 11), runner=runner,
                        anchors=GC.parse_anchors(GC.DEFAULT_ANCHORS),
                        state_path=tmp_path / "s.json",
                        log_path=tmp_path / "c.log")
    actions = [r["action"] for r in fired if not r.get("skipped_missed")]
    assert actions == ["scan", "closeout", "ai_advice"]
    assert "gov_ai.py" in " ".join(runner.calls[-1])
    assert len(runner.calls) == 3


def test_prescan_fires_before_closeout(tmp_path):
    """17:50 预扫描先触发；18:06 收口再触发（各自只一次）。"""
    runner = _Runner()
    state = {}
    anchors = GC.parse_anchors(GC.DEFAULT_ANCHORS)
    GC.run_tick(state, now_fn=lambda: _ts_at(17, 51), runner=runner,
                anchors=anchors, state_path=tmp_path / "s.json",
                log_path=tmp_path / "c.log")
    assert [c[-2] for c in runner.calls][-1:] == ["--window"]  # 最后一次是 scan
    fired2 = GC.run_tick(state, now_fn=lambda: _ts_at(18, 6), runner=runner,
                         anchors=anchors, state_path=tmp_path / "s.json",
                         log_path=tmp_path / "c.log")
    actions = [r["action"] for r in fired2]
    assert actions == ["closeout"]
    assert len(runner.calls) == 2
