# -*- coding: utf-8 -*-
"""OS-FIX-JEVLOCK-20261026：jev_backfill 取批/锁顺序 + 重复写守卫回归。

依据：`docs/audit/2026-10-26_JEV板块设计与实现审计.md` §四-②：
原实现先 SELECT 待判批、再抢 ollama_lock（等锁最长 900s）——等锁期间批已过期，
双实例会各自缓存同一批，后抢到锁者重复判读并覆盖已判值。
修复：①先抢锁 → 再 SELECT（占锁失败不连库不取批）；②UPDATE 加 `jev IS NULL
OR jev=''` 守卫（事务内二次校验，不新增 DB 列）；③tick 传 deadline_ts。

测试纪律：**不启动真批处理、不读写 news.db**——桩 news_ai + 桩 sqlite3 连接
（临时库）+ 桩 ollama_lock；被导入的 jev_backfill 模块在 teardown 时移除。
"""
from __future__ import annotations

import json
import sqlite3
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NEWS = Path(__file__).resolve().parent
for _p in (str(NEWS), str(ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class RecordingConn:
    """记录 execute 的 SQL（断言"抢锁失败时没有 SELECT 批"）。"""

    def __init__(self, conn, sink):
        self._conn = conn
        self._sink = sink

    def execute(self, sql, *args, **kwargs):
        self._sink.append(sql)
        return self._conn.execute(sql, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._conn, name)


class FakeNewsAI:
    """news_ai 桩：记录判读调用，支持 hook 在判读瞬间模拟外部动作。"""

    def __init__(self):
        self.calls = []
        self.hook = None

    def jev_judge_one(self, title, stocks):
        self.calls.append((title, list(stocks or [])))
        if self.hook:
            self.hook(title, stocks)
        return {"bias": "bull", "conf": 0.9, "title": title}

    def log(self, msg):        # noqa: D102 - 只吞日志
        pass

    def push_message(self, msg):   # noqa: D102 - 单测不推送
        pass


class FakeLock:
    """ollama_lock 桩：acquire 按预置序列返回（模拟 A 抢到 / B 被拒）。"""

    def __init__(self, acquire_results=(True,)):
        self._results = list(acquire_results)
        self.acquires = 0
        self.releases = 0
        self.acquire_wait_s = []
        self.tick_calls = []

    def acquire(self, purpose, wait_s=0):
        self.acquires += 1
        self.acquire_wait_s.append(wait_s)
        if len(self._results) > 1:
            return self._results.pop(0)
        return self._results[0] if self._results else True

    def release(self):
        self.releases += 1

    def heartbeat(self):
        pass

    def tick(self, purpose, **kwargs):
        self.tick_calls.append((purpose, kwargs))
        return True


@pytest.fixture
def env(monkeypatch, tmp_path):
    dbp = tmp_path / "news.db"
    con = sqlite3.connect(dbp)
    con.execute("CREATE TABLE news (id INTEGER PRIMARY KEY, title TEXT, "
                "stocks TEXT, ts TEXT, jev TEXT)")
    for i in range(1, 4):
        con.execute("INSERT INTO news(id,title,stocks,ts,jev) VALUES(?,?,?,?,NULL)",
                    (i, "n%d" % i, "[]", "2026-10-%02d" % i))
    con.commit()
    fake_ai = FakeNewsAI()
    monkeypatch.setitem(sys.modules, "news_ai", fake_ai)
    sys.modules.pop("jev_backfill", None)
    import jev_backfill as jb                      # noqa: E402
    sink = []

    def _connect(*a, **kw):
        sink.append("CONNECT")
        return RecordingConn(con, sink)

    monkeypatch.setattr(jb, "sqlite3", types.SimpleNamespace(
        connect=_connect, OperationalError=sqlite3.OperationalError))
    try:
        yield types.SimpleNamespace(jb=jb, ai=fake_ai, conn=con, dbp=dbp, sink=sink)
    finally:
        sys.modules.pop("jev_backfill", None)


def _run(monkeypatch, jb, lock):
    monkeypatch.setitem(sys.modules, "ollama_lock", lock)
    monkeypatch.setattr(sys, "argv", ["jev_backfill.py", "--batch", "8",
                                      "--alert-threshold", "0"])
    jb.main()


# --- ① 锁序：占锁失败不取批 -------------------------------------------------
def test_lock_failure_does_not_select_or_judge(env, monkeypatch, capsys):
    lock = FakeLock((False,))
    _run(monkeypatch, env.jb, lock)
    out = capsys.readouterr().out
    assert "BACKFILL skip" in out
    assert lock.acquires == 1
    selects = [s for s in env.sink if isinstance(s, str) and "FROM news" in s]
    assert selects == []          # 旧实现会先 SELECT 再让位 → 这里必须为空
    assert env.ai.calls == []


# --- ② 双实例：A 判读中 B 启动，B 不得重复判同一批 ---------------------------
def test_concurrent_instance_does_not_duplicate_batch(env, monkeypatch, capsys):
    lock = FakeLock((True, False))          # A 抢到锁；B 被拒
    state = {"spawned": False}

    def hook(title, stocks):
        if not state["spawned"]:
            state["spawned"] = True
            _run(monkeypatch, env.jb, lock)  # 第二实例在 A 判读期间启动

    env.ai.hook = hook
    _run(monkeypatch, env.jb, lock)
    out = capsys.readouterr().out
    # 每行只判 1 次、B 一条都没判
    assert len(env.ai.calls) == 3
    titles = [c[0] for c in env.ai.calls]
    assert len(set(titles)) == 3 and set(titles) == {"n1", "n2", "n3"}
    assert "BACKFILL skip" in out
    left = env.conn.execute(
        "SELECT COUNT(*) FROM news WHERE jev IS NULL OR jev=''").fetchone()[0]
    assert left == 0


# --- ③ 事务内二次校验：外部先写的行不被覆盖 ---------------------------------
def test_updates_only_still_pending_rows(env, monkeypatch, capsys):
    lock = FakeLock((True,))
    fired = {"done": False}

    def hook(title, stocks):
        if title == "n1" and not fired["done"]:
            fired["done"] = True
            # 模拟锁被陈旧回收后另一实例先落库
            other = sqlite3.connect(env.dbp)
            other.execute("UPDATE news SET jev=? WHERE id=1",
                          (json.dumps({"bias": "competing"}),))
            other.commit()
            other.close()

    env.ai.hook = hook
    _run(monkeypatch, env.jb, lock)
    out = capsys.readouterr().out
    row = env.conn.execute("SELECT jev FROM news WHERE id=1").fetchone()[0]
    assert json.loads(row)["bias"] == "competing"      # 未被本实例覆盖
    assert "judged=2/3" in out
    assert "dup_skip=1" in out


# --- ④ tick 截止时刻透传（OS-FIX-JEVLOCK §四-③ 接线） ----------------------
def test_locked_tick_forwards_deadline_ts(env):
    class _Lock:
        def __init__(self):
            self.kw = None
            self.heartbeats = 0

        def tick(self, purpose, **kwargs):
            self.kw = kwargs
            return True

        def heartbeat(self):
            self.heartbeats += 1

    lock = _Lock()
    assert env.jb._locked_tick(lock, "jev_backfill", False, False,
                               deadline_ts=123.0) is True
    assert lock.kw == {"deadline_ts": 123.0}
    # M3 暂停旗分支：只 heartbeat，不让位（原 EXEC-R28 语义不变）
    assert env.jb._locked_tick(lock, "jev_backfill", True, False,
                               deadline_ts=123.0) is True
    assert lock.heartbeats == 1
