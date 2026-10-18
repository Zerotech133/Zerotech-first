# -*- coding: utf-8 -*-
"""--retry-degraded 通道契约测试（v1.4 DEGRADED 行重标，2026-10-18）。

回归锚：
  ① degraded（parse_ok=0）行被重标通道选中，LLM 成功后原地升级（不新增行）
  ② 仍截断的行保持 degraded 并累加 retry_count
  ③ retry_count>=3 封顶后不再入选（防死循环）
  ④ 升级后行退出待重试集
  ⑤ retry_count 列 ALTER 迁移幂等（跑两遍不炸）
跑法：pytest tests/test_xq_classify_retry_degraded.py -q
LLM 全程 monkeypatch（urllib.request.urlopen），绝不真调 Ollama。
"""
import json
import sqlite3
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import xq_classify as xc

GOOD = ('{"event_type":"行情观点","content_type":"tech",'
        '"sentiment":"bullish","confidence":0.9}')
BAD = '{"event_type":"行情观'   # 模拟 LLM 输出截断 → 不可解析


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body


def patch_llm(monkeypatch, answer: str) -> None:
    """把 Ollama 响应换成固定内容（good=可解析 JSON / bad=截断文本）。"""
    def fake_urlopen(req, timeout=None):
        return _FakeResp(json.dumps({"message": {"content": answer}}).encode("utf-8"))
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """隔离 DB 与状态日志，建源表 xq_status。"""
    monkeypatch.setattr(xc, "DB_PATH", tmp_path / "xueqiu_info.db")
    monkeypatch.setattr(xc, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(xc, "STATUS_LOG", tmp_path / "logs" / "status.log")
    con = sqlite3.connect(xc.DB_PATH)
    con.execute("""CREATE TABLE xq_status (
                     status_id TEXT PRIMARY KEY, title TEXT, desc TEXT,
                     category TEXT, created_at TEXT)""")
    con.commit()
    con.close()
    return tmp_path


def seed_status(db, status_id, title="茅台要涨"):
    con = sqlite3.connect(db)
    con.execute("INSERT INTO xq_status VALUES (?,?,?,?,?)",
                (status_id, title, "正文内容", "stock", "2026-10-18 10:00:00"))
    con.commit()
    con.close()


def seed_degraded(db, status_id, retry_count=0):
    """直接落一条 degraded（parse_ok=0）行，模拟历史 LLM 截断产物。"""
    con = sqlite3.connect(db)
    xc.ensure_table(con)
    con.execute(
        """INSERT OR REPLACE INTO xq_status_classified
           (status_id, parse_ok, model, prompt_version, labeled_at, retry_count)
           VALUES (?,?,?,?,?,?)""",
        (status_id, 0, xc.MODEL, xc.PROMPT_VERSION,
         "2026-10-18 09:00:00", retry_count))
    con.commit()
    con.close()


def fetch(db, status_id):
    con = sqlite3.connect(db)
    r = con.execute(
        "SELECT parse_ok, event_type, sentiment, retry_count"
        " FROM xq_status_classified WHERE status_id=?", (status_id,)).fetchone()
    con.close()
    return r


def retry_candidates(db):
    """与 retry_degraded 同口径的待重试集。"""
    con = sqlite3.connect(db)
    r = con.execute(
        """SELECT COUNT(*) FROM xq_status_classified c
           WHERE c.parse_ok=0 AND COALESCE(c.retry_count,0) < 3""").fetchone()[0]
    con.close()
    return r


# ── ① degraded 被选中，LLM 成功 → 原地升级 ────────────────────

def test_degraded_selected_and_upgraded(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1", retry_count=0)
    patch_llm(monkeypatch, GOOD)
    assert xc.retry_degraded(500) == 0
    parse_ok, event_type, sentiment, _ = fetch(db, "s1")
    assert parse_ok == 1
    assert (event_type, sentiment) == ("行情观点", "bullish")
    # 不新增行：派生表仍只有这一条
    con = sqlite3.connect(db)
    assert con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0] == 1
    con.close()


# ── ② 仍截断 → 保持 degraded 且 retry_count 累加 ──────────────

def test_retry_count_increments_on_still_truncated(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1", retry_count=0)
    patch_llm(monkeypatch, BAD)
    xc.retry_degraded(500)
    assert fetch(db, "s1") == (0, None, None, 1)
    xc.retry_degraded(500)   # 第二轮仍截断
    assert fetch(db, "s1") == (0, None, None, 2)


# ── ③ 3 次封顶 → 不再入选 ────────────────────────────────────

def test_cap_at_3_not_selected(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1", retry_count=3)
    patch_llm(monkeypatch, BAD)
    xc.retry_degraded(500)
    # 封顶行未被触碰：retry_count 不变、仍 degraded
    assert fetch(db, "s1") == (0, None, None, 3)
    assert retry_candidates(db) == 0


def test_cap_boundary_at_2_still_selected(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1", retry_count=2)
    patch_llm(monkeypatch, BAD)
    xc.retry_degraded(500)
    # retry_count=2 < 3 仍在口径内，本轮后封顶
    assert fetch(db, "s1") == (0, None, None, 3)
    assert retry_candidates(db) == 0


# ── ④ 升级后行退出待重试集 ───────────────────────────────────

def test_upgraded_row_exits_retry_set(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1", retry_count=2)   # 已临封顶
    patch_llm(monkeypatch, GOOD)
    xc.retry_degraded(500)
    parse_ok, _, _, retry_count = fetch(db, "s1")
    assert parse_ok == 1
    assert retry_count == 0                  # REPLACE 重置计次
    assert retry_candidates(db) == 0
    # 即便后续 LLM 变坏，也不会把升级行拉回 degraded
    patch_llm(monkeypatch, BAD)
    xc.retry_degraded(500)
    assert fetch(db, "s1")[0] == 1


# ── ⑤ ALTER 迁移幂等（跑两遍不炸） ───────────────────────────

def test_migration_idempotent(env):
    db = xc.DB_PATH
    con = sqlite3.connect(db)
    xc.ensure_table(con)
    xc.ensure_table(con)   # 第二遍：列已存在，必须静默跳过
    cols = [r[1] for r in con.execute("PRAGMA table_info(xq_status_classified)")]
    con.close()
    assert "retry_count" in cols
    assert "content_type" in cols


def test_retry_degraded_twice_fresh_db_no_crash(env, monkeypatch):
    # 全链：全新库上连跑两轮（无候选 → 空转不炸）
    patch_llm(monkeypatch, BAD)
    assert xc.retry_degraded(500) == 0
    assert xc.retry_degraded(500) == 0


def test_empty_body_source_skipped_in_join(env, monkeypatch):
    # 源表缺该 status_id 的派生行（内连接）不会被重标——不炸即可
    db = xc.DB_PATH
    seed_degraded(db, "orphan", retry_count=0)
    patch_llm(monkeypatch, GOOD)
    assert xc.retry_degraded(500) == 0
    assert fetch(db, "orphan")[0] == 0   # 未被动过
