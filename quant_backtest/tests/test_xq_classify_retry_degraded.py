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


# ── ⑥ v1.4.1 别名短路（degraded 真病灶：event_type 漂移描述词） ──
# 探针实证：answer 完整合法、done_reason=stop，病根是白名单拒绝非截断。

def test_alias_edu_maps_to_chat(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"投教科普","content_type":"edu",'
           '"sentiment":"neutral","confidence":0.95}')
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    parse_ok, event_type, sentiment, _ = fetch(db, "s1")
    assert (parse_ok, event_type, sentiment) == (1, "闲聊杂谈", "neutral")
    con = sqlite3.connect(db)
    assert con.execute("SELECT content_type FROM xq_status_classified"
                       " WHERE status_id='s1'").fetchone()[0] == "edu"
    con.close()


def test_alias_news_maps_to_research(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"个股资讯","content_type":"news",'
           '"sentiment":"neutral","confidence":0.9}')
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    assert fetch(db, "s1")[:3] == (1, "个股研判", "neutral")


def test_alias_hk_ipo_maps_to_strategy(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"港股打新","content_type":"fundamental",'
           '"sentiment":"neutral","confidence":0.9}')
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    assert fetch(db, "s1")[:3] == (1, "交易策略", "neutral")
    # 别名派生覆盖模型的 content_type（别名自带更准形态信息）
    con = sqlite3.connect(db)
    assert con.execute("SELECT content_type FROM xq_status_classified"
                       " WHERE status_id='s1'").fetchone()[0] == "news"
    con.close()


def test_alias_hit_writes_stderr(env, monkeypatch, capsys):
    """v1.4.1 兜底记录：别名命中时 stderr 必须留痕（fail-visible 判例），
    便于事后审计映射命中率与发现新的漂移标签。"""
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"投教科普","content_type":"edu",'
           '"sentiment":"neutral","confidence":0.9}')
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    err = capsys.readouterr().err
    assert "ALIAS-FALLBACK" in err
    assert "投教科普" in err


def test_unknown_event_type_still_degraded(env, monkeypatch):
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"火星文标签","content_type":"edu",'
           '"sentiment":"neutral","confidence":0.9}')
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    # 未收录漂移标签：宁可降级不乱归类（event_type=None，其余字段按既有
    # 行为落账、retry_count 计次）
    assert fetch(db, "s1") == (0, None, "neutral", 1)


@pytest.mark.parametrize("et", xc.EVENT_TYPES)
@pytest.mark.parametrize("st", xc.SENTIMENTS)
def test_legal_enum_pass_through_untouched(env, monkeypatch, capsys, et, st):
    """v1.4.1 回归锚（合法值零影响）：全部 7×3 合法 event_type/sentiment
    组合直通白名单，不经别名短路、值原样落账——追平轮进行中的安全前提。"""
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"%s","content_type":"tech",'
           '"sentiment":"%s","confidence":0.9}' % (et, st))
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    assert fetch(db, "s1")[:3] == (1, et, st)
    assert "ALIAS-FALLBACK" not in capsys.readouterr().err


@pytest.mark.parametrize("ct", xc.CONTENT_TYPES)
def test_legal_content_type_pass_through_untouched(env, monkeypatch, ct):
    """v1.4.1 回归锚（形态列零影响）：六个合法 content_type 原样落账，
    不被别名派生覆盖（合法 event_type 时不触发短路）。"""
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"行情观点","content_type":"%s",'
           '"sentiment":"neutral","confidence":0.9}' % ct)
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    assert fetch(db, "s1")[:3] == (1, "行情观点", "neutral")
    con = sqlite3.connect(db)
    assert con.execute("SELECT content_type FROM xq_status_classified"
                       " WHERE status_id='s1'").fetchone()[0] == ct
    con.close()
