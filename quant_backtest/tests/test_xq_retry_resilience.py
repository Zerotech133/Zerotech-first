# -*- coding: utf-8 -*-
"""v1.4.2 轮级兜底单测（2026-10-18）：retry-degraded / run 静默死亡修复。

背景（15:24 实证）：classify_one 的 DB 写段或循环收尾抛异常时直接穿出
retry_degraded/run，进程无收行死亡（status log 无 end 行，巡检失明）。
修复后契约：任何异常路径 end 收行必达；单条 DB 故障按 hard-fail 计不杀轮。

全部假 LLM / 假 con / 临时 DB，不碰真库不真调 Ollama。
跑法：pytest quant_backtest/tests/test_xq_retry_resilience.py -q
"""
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_classify as xc


@pytest.fixture(autouse=True)
def _no_ollama_lock(monkeypatch):
    """审计修复（2026-10-31）：run() 单测不依赖全局 ollama 锁。

    并行会话（JEV/M3）持锁时，xc.run 的 acquire 默认等待 120s 会触发 pytest-timeout
    → quant suite rc=1；本文件用例只验证 run 的 SQL/收行语义，锁不参与。
    """
    monkeypatch.setenv("XQ_OLLAMA_LOCK", "0")


@pytest.fixture()
def tmp_db(monkeypatch, tmp_path):
    """临时库 + 隔离日志路径；返回 (db_path, lines)。lines 收集 log() 输出。"""
    db = tmp_path / "xq.db"
    lines = []
    monkeypatch.setattr(xc, "DB_PATH", db)
    monkeypatch.setattr(xc, "LOG_DIR", tmp_path)
    monkeypatch.setattr(xc, "STATUS_LOG", tmp_path / "status.log")
    monkeypatch.setattr(xc, "log", lambda msg: lines.append(msg))
    con = sqlite3.connect(str(db))
    con.execute("CREATE TABLE xq_status (status_id TEXT PRIMARY KEY, "
                "title TEXT, desc TEXT, category TEXT, created_at TEXT)")
    con.commit()
    con.close()
    return db, lines


def _seed_rows(db, n, with_classified=True):
    con = sqlite3.connect(str(db))
    xc.ensure_table(con)
    for i in range(n):
        con.execute("INSERT INTO xq_status VALUES (?,?,?,?,?)",
                    (str(1000 + i), "t%d" % i, "d%d" % i, "104", "2026-10-18"))
        if with_classified:
            con.execute("INSERT INTO xq_status_classified (status_id, parse_ok, "
                        "retry_count) VALUES (?,0,0)", (str(1000 + i),))
    con.commit()
    con.close()


def _patch_retry_select_nodberror(monkeypatch):
    """retry_degraded 的候选 SELECT 带 JOIN xq_status；临时库两表都有，无需打补丁。"""
    pass


# ---------- retry_degraded：异常穿透不得吞收行 ----------

def test_retry_classify_one_raises_still_emits_end_line(tmp_db, monkeypatch):
    db, lines = tmp_db
    _seed_rows(db, 3)

    def boom(con, row):
        raise RuntimeError("simulated mid-round crash")
    monkeypatch.setattr(xc, "classify_one", boom)

    rc = xc.retry_degraded(limit=10)
    assert rc == 0
    assert any(l.startswith("RETRY-ABORT-ERROR RuntimeError") for l in lines)
    end = [l for l in lines if l.startswith("retry-degraded end rc=0")]
    assert len(end) == 1                      # 收行必达
    assert "degraded_left=-1" in end[0]       # 异常取数哨兵值


def test_retry_db_write_fail_counts_as_hard_not_kill(tmp_db, monkeypatch):
    """classify_one 返回 False（DB 故障新语义）→ 计 hard，轮继续到收行。"""
    db, lines = tmp_db
    _seed_rows(db, 3)
    monkeypatch.setattr(xc, "classify_one", lambda con, row: False)

    rc = xc.retry_degraded(limit=10)
    assert rc == 0
    end = [l for l in lines if l.startswith("retry-degraded end rc=0")][0]
    assert "hard_fail=3" in end and "degraded_left=3" in end  # 正常取数路径


def test_retry_normal_round_end_line_shape(tmp_db, monkeypatch):
    db, lines = tmp_db
    _seed_rows(db, 2)
    monkeypatch.setattr(xc, "classify_one", lambda con, row: True)

    rc = xc.retry_degraded(limit=10)
    assert rc == 0
    end = [l for l in lines if l.startswith("retry-degraded end rc=0")][0]
    # 既有消费契约：rc=0 + 四个计数字段，字段顺序不变
    assert "upgraded=" in end and "still_degraded=" in end \
        and "hard_fail=" in end and "degraded_left=" in end


# ---------- classify_one：DB 写段故障隔离 ----------

class _FakeResp:
    def __init__(self, content):
        self._c = content

    def read(self):
        return ('{"message":{"content":%s}}' % _json_str(self._c)).encode()


def _json_str(s):
    import json
    return json.dumps(s)


class _BrokenCon:
    """execute 即抛——模拟 DB locked / disk I/O error。"""

    def execute(self, *a, **k):
        raise sqlite3.OperationalError("database is locked")


def test_classify_one_db_fail_returns_false(tmp_db, monkeypatch):
    db, lines = tmp_db
    good = '{"event_type":"行情观点","sentiment":"neutral","content_type":"news"}'
    monkeypatch.setattr(xc.urllib.request, "urlopen",
                        lambda req, timeout: _FakeResp(good))
    rc = xc.classify_one(_BrokenCon(), ("9001", "t", "d", "104"))
    assert rc is False                        # False（hard 计数）而非异常穿透


# ---------- run()：同刀同治 ----------

def test_run_crash_mid_round_still_emits_end_line(tmp_db, monkeypatch):
    db, lines = tmp_db
    _seed_rows(db, 3, with_classified=False)
    calls = {"n": 0}

    def flaky(con, row):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return True
    monkeypatch.setattr(xc, "classify_one", flaky)

    rc = xc.run(limit=10)
    assert rc == 1                            # 异常按需关注语义
    assert any(l.startswith("ABORT-ERROR OperationalError") for l in lines)
    end = [l for l in lines if l.startswith("round end rc=")]
    assert len(end) == 1                      # 收行必达
    assert "derived_total=-1" in end[0]


def test_run_normal_end_line_shape(tmp_db, monkeypatch):
    db, lines = tmp_db
    _seed_rows(db, 2, with_classified=False)
    monkeypatch.setattr(xc, "classify_one", lambda con, row: True)

    rc = xc.run(limit=10)
    assert rc == 0
    end = [l for l in lines if l.startswith("round end rc=0")][0]
    assert "labeled_this=2" in end and "hard_fail=0" in end
