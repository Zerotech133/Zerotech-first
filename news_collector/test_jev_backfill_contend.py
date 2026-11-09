# -*- coding: utf-8 -*-
"""RFC-20261106：_decide_skip 积压出口单测（fake 锁 + tmp 库，不连 news.db、不占锁）。

背景：M3 持锁时 _m3_active_skip 整轮让位，闸门3 靠"judge 延迟>6s"判 busy，
回填不发请求 → 闸门3 永不触发 → M3 一直持锁（2026-11-06 白天 5 轮空转实证）。
修复：积压 >= M3_CONTEND_MIN_BACKLOG 时改为参与锁竞争。
"""
import os
import sqlite3
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import jev_backfill as jb  # noqa: E402


class _FakeLock:
    """ollama_lock 的最小替身：只提供 holder_info()。"""

    def __init__(self, purpose="k266_m3", alive=True, pid=123):
        self._info = {"purpose": purpose, "pid": pid, "alive": alive}

    def holder_info(self):
        return dict(self._info)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("JEV_SKIP_IF_M3", "JEV_M3_CONTEND_MIN_BACKLOG", "JEV_CONTEND_WAIT_S"):
        monkeypatch.delenv(k, raising=False)


def test_skip_when_backlog_below_threshold():
    # 积压 100 < 150 → 维持旧行为：整轮让位
    skip, contend = jb._decide_skip(_FakeLock(), 100)
    assert skip is True
    assert contend is False


def test_contend_when_backlog_at_threshold():
    # 积压正好 150（边界）→ 参与竞争
    skip, contend = jb._decide_skip(_FakeLock(), 150)
    assert skip is False
    assert contend is True


def test_contend_when_backlog_high():
    skip, contend = jb._decide_skip(_FakeLock(), 883)
    assert skip is False
    assert contend is True


def test_no_skip_when_holder_not_m3():
    # holder 是 jev_night 等非 M3 目的 → 一律正常抢锁，无 contend 语义
    skip, contend = jb._decide_skip(_FakeLock(purpose="jev_night"), 9999)
    assert skip is False
    assert contend is False


def test_no_skip_when_holder_dead():
    skip, contend = jb._decide_skip(_FakeLock(alive=False), 9999)
    assert skip is False
    assert contend is False


def test_env_skip_disabled_short_circuits():
    # jev_night 路径：JEV_SKIP_IF_M3=0 → 恒不 skip（行为与 2026-10-30 版本一致）
    os.environ["JEV_SKIP_IF_M3"] = "0"
    try:
        skip, contend = jb._decide_skip(_FakeLock(), 100)
        assert skip is False
        assert contend is False
    finally:
        os.environ.pop("JEV_SKIP_IF_M3", None)


def test_none_backlog_keeps_old_behavior():
    # 库不可用（left=None）→ 退回保守旧行为：见 M3 就让
    skip, contend = jb._decide_skip(_FakeLock(), None)
    assert skip is True
    assert contend is False


def test_env_threshold_override(monkeypatch):
    # 回滚开关：阈值调到 999999 → 恢复"见 M3 就让位"
    monkeypatch.setattr(jb, "M3_CONTEND_MIN_BACKLOG", 999999)
    skip, contend = jb._decide_skip(_FakeLock(), 883)
    assert skip is True
    assert contend is False


def test_env_int_fallback_on_garbage(monkeypatch):
    monkeypatch.setenv("JEV_M3_CONTEND_MIN_BACKLOG", "not-a-number")
    assert jb._env_int("JEV_M3_CONTEND_MIN_BACKLOG", 150) == 150
    monkeypatch.setenv("JEV_CONTEND_WAIT_S", "")
    assert jb._env_int("JEV_CONTEND_WAIT_S", 600) == 600


def test_backlog_left_counts_null_and_empty(tmp_path):
    p = tmp_path / "news.db"
    con = sqlite3.connect(str(p))
    con.execute("CREATE TABLE news (id TEXT, jev TEXT)")
    con.executemany(
        "INSERT INTO news VALUES (?,?)",
        [("a", None), ("b", ""), ("c", "ok"), ("d", "  ")],
    )
    con.commit()
    con.close()
    # NULL 与空串计为待判；纯空格不算（与判定落库口径一致）
    assert jb._backlog_left(str(p)) == 2


def test_backlog_left_missing_table_raises(tmp_path):
    p = tmp_path / "empty.db"
    sqlite3.connect(str(p)).close()
    with pytest.raises(Exception):
        jb._backlog_left(str(p))
