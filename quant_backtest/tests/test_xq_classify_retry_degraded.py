# -*- coding: utf-8 -*-
"""--retry-degraded 通道契约测试（v1.4 DEGRADED 行重标，2026-10-18）。

回归锚：
  ① degraded（parse_ok=0）行被重标通道选中，LLM 成功后原地升级（不新增行）
  ② 仍截断的行保持 degraded 并累加 retry_count
  ③ retry_count>=3 封顶后不再入选（防死循环）
  ④ 升级后行退出待重试集
  ⑤ retry_count 列 ALTER 迁移幂等（跑两遍不炸）
跑法：pytest tests/test_xq_classify_retry_degraded.py -q
LLM 全程 monkeypatch（xc.safe_urlopen），绝不真调 Ollama。

2026-11-08 修接缝（P3-5 xq 41 项根因，非产品 BUG）：xq_classify 自 2026-10-30
（d6505161d）起一律经 safe_urlopen 出网，且 **loopback 目标**（Ollama 就在
127.0.0.1:11434）走 safe_urlopen._NO_PROXY_OPENER（OpenerDirector.open），
根本不经过 urllib.request.urlopen。旧写法
    monkeypatch.setattr(urllib.request, "urlopen", fake)
对本文件完全失效 —— 拦截没生效，测试真调本机 qwen3.5:9b，答案随机 →
38 项假红；单文件耗时由秒级涨到 78s。正解是 patch 被测模块命名空间里的
safe_urlopen（与 test_peak_shift_gates.py patch xc.log / xc.ollama_probe_busy 同法）。
"""
import json
import socket
import sqlite3
import sys
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
    """把 Ollama 响应换成固定内容（good=可解析 JSON / bad=截断文本）。

    接缝是 xc.safe_urlopen（模块级绑定名），不是 urllib.request.urlopen —— 见
    模块 docstring 的 2026-11-08 说明。误改回去会让本文件真调本机模型。
    """
    def fake_safe_urlopen(req, *args, **kwargs):
        return _FakeResp(json.dumps({"message": {"content": answer}}).encode("utf-8"))
    monkeypatch.setattr(xc, "safe_urlopen", fake_safe_urlopen)


@pytest.fixture(autouse=True)
def _hermetic_llm(monkeypatch):
    """① 关掉全局 ollama 租约（并行会话持锁时 acquire 会等满 120s → pytest-timeout）。
    ② fail-visible 兜底：任何真实 connect() 立刻断言失败，替身接缝若再断开，
       会当场报「禁止真实网络」，而不是静默打真服务跑出随机答案。"""
    monkeypatch.setenv("XQ_OLLAMA_LOCK", "0")

    def _no_connect(self, addr, *a, **kw):
        raise AssertionError(
            "测试禁止真实网络连接：%r —— LLM 替身接缝已断，"
            "patch 目标应为 xc.safe_urlopen" % (addr,))

    monkeypatch.setattr(socket.socket, "connect", _no_connect)


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
    assert "norm_tickers" in cols


def test_new_classification_normalizes_in_same_replace(env, monkeypatch):
    seed_status(xc.DB_PATH, "s1", title="贵州茅台600519")
    patch_llm(monkeypatch, GOOD)
    con = sqlite3.connect(xc.DB_PATH)
    xc.ensure_table(con)
    assert xc.classify_one(con, ("s1", "贵州茅台600519", "正文内容", "stock"))
    row = con.execute(
        "SELECT tickers, norm_tickers, parse_ok FROM xq_status_classified"
        " WHERE status_id='s1'").fetchone()
    assert json.loads(row[1]) == xc.normalize_tickers(row[0])
    assert json.loads(row[1]) == [{"raw": "600519", "kind": "stock_a",
                                   "canonical": "SH600519"}]
    assert row[2] == 1
    assert con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0] == 1
    con.rollback()
    assert con.execute("SELECT COUNT(*) FROM xq_status_classified").fetchone()[0] == 0
    con.close()


def test_new_classification_empty_tickers_are_empty_array(env, monkeypatch):
    seed_status(xc.DB_PATH, "s1", title="没有标的词")
    patch_llm(monkeypatch, GOOD)
    con = sqlite3.connect(xc.DB_PATH)
    xc.ensure_table(con)
    assert xc.classify_one(con, ("s1", "没有标的词", "正文内容", "stock"))
    row = con.execute(
        "SELECT tickers, norm_tickers FROM xq_status_classified"
        " WHERE status_id='s1'").fetchone()
    assert row == ("[]", "[]")
    con.close()


def test_retry_replace_refreshes_norm_without_losing_retry_count(env, monkeypatch):
    seed_status(xc.DB_PATH, "s1", title="贵州茅台600519")
    seed_degraded(xc.DB_PATH, "s1", retry_count=2)
    con = sqlite3.connect(xc.DB_PATH)
    con.execute("UPDATE xq_status_classified SET norm_tickers=? WHERE status_id='s1'",
                ('[{"raw":"stale"}]',))
    con.commit()
    patch_llm(monkeypatch, BAD)
    assert xc.classify_one(con, ("s1", "贵州茅台600519", "正文内容", "stock"))
    row = con.execute(
        "SELECT tickers, norm_tickers, retry_count, parse_ok"
        " FROM xq_status_classified WHERE status_id='s1'").fetchone()
    assert json.loads(row[1]) == xc.normalize_tickers(row[0])
    assert row[1] != '[{"raw":"stale"}]'
    assert row[2:] == (2, 0)
    con.close()


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


# ── ⑦ 真实探针样本回放（2026-10-18 degraded-rca 全量 16 行实证）──
# 探针定性 A 类：answer 完整合法 JSON、done_reason=stop（82-90 字符），
# 14:53 落账轮跑的是无别名的旧 HEAD（36a442a3），v1.4.1 提交（15:22/15:37）
# 晚于落账从未生效。下列为探针原文逐字 fixture（非构造样本）。
REAL_PROBE_ANSWERS = [
    # 136684573 / 136226346 等 11 行：投教科普
    ('{"event_type":"投教科普","content_type":"edu","sentiment":"neutral",'
     '"confidence":0.95}', "闲聊杂谈", "edu", "neutral", 0.95),
    # 136356182 等 4 行：个股资讯
    ('{"event_type":"个股资讯","content_type":"news","sentiment":"neutral",'
     '"confidence":0.95}', "个股研判", "news", "neutral", 0.95),
    # 135857044：港股打新
    ('{"event_type":"港股打新","content_type":"fundamental","sentiment":"neutral",'
     '"confidence":0.95}', "交易策略", "news", "neutral", 0.95),
]


@pytest.mark.parametrize("answer,et,ct,st,conf", REAL_PROBE_ANSWERS)
def test_real_probe_answer_replayed(env, monkeypatch, answer, et, ct, st, conf):
    """真实探针样本回放：完整 JSON + 漂移词，经别名短路升级 parse_ok=1，
    四字段（event_type/content_type/sentiment/confidence）逐项对账。"""
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1", retry_count=2)   # 真实存量：已临封顶的最后一次机会
    patch_llm(monkeypatch, answer)
    assert xc.retry_degraded(500) == 0
    parse_ok, got_et, got_st, retry_count = fetch(db, "s1")
    assert (parse_ok, got_et, got_st, retry_count) == (1, et, st, 0)
    con = sqlite3.connect(db)
    row = con.execute(
        "SELECT content_type, confidence FROM xq_status_classified"
        " WHERE status_id='s1'").fetchone()
    con.close()
    assert row == (ct, conf)


def test_real_probe_confidence_out_of_range_still_upgraded(env, monkeypatch):
    """探针同形态但 confidence 越界（>1）：confidence 落 None 不降级
    parse_ok（与既有口径一致——confidence 非判级字段）。"""
    db = xc.DB_PATH
    seed_status(db, "s1")
    seed_degraded(db, "s1")
    ans = ('{"event_type":"投教科普","content_type":"edu",'
           '"sentiment":"bullish","confidence":1.5}')
    patch_llm(monkeypatch, ans)
    xc.retry_degraded(500)
    parse_ok, event_type, _, _ = fetch(db, "s1")
    assert (parse_ok, event_type) == (1, "闲聊杂谈")
    con = sqlite3.connect(db)
    assert con.execute("SELECT confidence FROM xq_status_classified"
                       " WHERE status_id='s1'").fetchone()[0] is None
    con.close()


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
