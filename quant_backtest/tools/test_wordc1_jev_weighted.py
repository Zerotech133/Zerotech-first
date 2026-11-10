# -*- coding: utf-8 -*-
"""WORDC1 JEV 判定加权词云扩展的单测（工单 WO-INTEL-WORDC1-JEV-20261107-001）。

验收判据（工单 §验收，黄忠逐条打勾）：
  1. 双源词表 JSON schema 只增不改（既有键原样保留，新增仅 jev_weighted）
  2. bias 分桶 3 主桶 + 1 未判对照桶，逐桶带样本数；malformed 不静默丢
  3. 停用词口径冻结（STOPWORDS 快照 171 词，不增不减）
  4. mode=ro 纪律（全部 sqlite 连接必须只读 URI）
  5. conf<0.5 判定降权 50% 且写入 meta
"""
import json
import os
import sqlite3

import pytest

import wordc1_cloud as M


# ---------- 夹具 ----------

def _make_dbs(tmp_path):
    """news.db：8 行（bull×2 含低置信、bear×1、neutral×1、缺 bias×1、坏 JSON×1、未判×2）"""
    news = tmp_path / "news.db"
    con = sqlite3.connect(str(news))
    con.execute("CREATE TABLE news (ts TEXT, title TEXT, source TEXT, jev TEXT)")
    rows = [
        ("2026-11-06 10:00:00", "某公司营收大幅增长利润创新高", "xd_news",
         '{"bias":"bull","conf":0.9,"event_type":"earnings"}'),
        ("2026-11-06 11:00:00", "增长势头延续订单饱满", "xd_news",
         '{"bias":"bull","conf":0.3,"event_type":"equity"}'),
        ("2026-11-06 12:00:00", "股价下跌业绩不及预期", "xd_news",
         '{"bias":"bear","conf":0.8,"event_type":"price"}'),
        ("2026-11-06 13:00:00", "公司发布公告说明进展", "xd_news",
         '{"bias":"neutral","conf":0.7,"event_type":"other"}'),
        ("2026-11-06 14:00:00", "缺 bias 字段的判定行", "xd_news",
         '{"conf":0.9}'),
        ("2026-11-06 15:00:00", "坏 JSON 行", "xd_news", '{oops'),
        ("2026-11-06 16:00:00", "未判新闻一", "xd_news", None),
        ("2026-11-06 17:00:00", "未判新闻二", "xd_news", ""),
    ]
    con.executemany("INSERT INTO news VALUES (?,?,?,?)", rows)
    con.commit()
    con.close()
    xq = tmp_path / "xueqiu.db"
    con = sqlite3.connect(str(xq))
    con.execute("CREATE TABLE xq_status (title TEXT, desc TEXT)")
    con.execute("INSERT INTO xq_status VALUES ('雪球增长观点', 'body')")
    con.commit()
    con.close()
    return str(news), str(xq)


@pytest.fixture()
def env(tmp_path, monkeypatch):
    news, xq = _make_dbs(tmp_path)
    out = tmp_path / "outputs"
    monkeypatch.setattr(M, "NEWS_DB", news)
    monkeypatch.setattr(M, "XQ_DB", xq)
    monkeypatch.setattr(M, "OUT_DIR", str(out))
    monkeypatch.setattr(M, "TODAY", "2026-11-07")
    monkeypatch.setattr(M, "HTML_PATH", os.path.join(str(out), "t.html"))
    monkeypatch.setattr(M, "JSON_PATH", os.path.join(str(out), "t.json"))
    return out


def _read(out):
    return json.loads((out / "t.json").read_text(encoding="utf-8"))


# ---------- 1. schema 只增不改 ----------

def test_default_mode_has_no_jev_weighted(env):
    M.main([])
    d = _read(env)
    assert set(d.keys()) == {"xueqiu", "news", "overlap_top20", "meta"}


def test_jev_weighted_only_adds_key(env):
    M.main([])
    base = _read(env)
    M.main(["--jev-weighted", "--days", "0"])
    d = _read(env)
    assert set(d.keys()) == {"xueqiu", "news", "overlap_top20", "meta", "jev_weighted"}
    for k in ("xueqiu", "news", "overlap_top20", "meta"):
        assert d[k] == base[k], f"既有键 {k} 被改动——违反 schema 只增不改"


# ---------- 2. bias 分桶 3+1、malformed 不静默丢 ----------

def test_bias_buckets_counts_and_malformed(env):
    M.main(["--jev-weighted", "--days", "0"])
    jw = _read(env)["jev_weighted"]
    b = jw["buckets"]
    assert set(b.keys()) == {"bull", "bear", "neutral"}
    assert b["bull"]["count"] == 2
    assert b["bear"]["count"] == 1
    assert b["neutral"]["count"] == 1
    assert jw["meta"]["judged_rows"] == 6
    assert jw["meta"]["malformed"] == 2  # 缺 bias + 坏 JSON，如实上报
    u = jw["unjudged"]
    assert u["total"] == 2 and u["sampled"] == 2


def test_bucket_top_words_present(env):
    M.main(["--jev-weighted", "--days", "0"])
    jw = _read(env)["jev_weighted"]
    bull_words = [x["word"] for x in jw["buckets"]["bull"]["top"]]
    assert bull_words, "bull 桶 Top 词为空"
    assert any("增长" in w for w in bull_words)


# ---------- 3. 停用词口径冻结 ----------

def test_stopwords_frozen():
    assert len(M.STOPWORDS) == 171, "STOPWORDS 口径变更须走 INTEL0 受理（工单判据 3）"
    assert "公告" in M.STOPWORDS and "增长" not in M.STOPWORDS


# ---------- 4. mode=ro 纪律 ----------

def test_all_sqlite_connections_readonly(env, monkeypatch):
    uris = []
    real_connect = sqlite3.connect

    def spy(path, *a, **kw):
        uris.append(str(path))
        return real_connect(path, *a, **kw)

    monkeypatch.setattr(M.sqlite3, "connect", spy)
    M.main(["--jev-weighted", "--days", "0"])
    assert uris, "未捕获到任何 sqlite 连接"
    assert all("mode=ro" in u for u in uris), f"存在非只读连接: {uris}"


# ---------- 5. conf<0.5 降权 50% ----------

def test_low_conf_downweight(env):
    M.main(["--jev-weighted", "--days", "0"])
    jw = _read(env)["jev_weighted"]
    counts = {x["word"]: x["count"] for x in jw["buckets"]["bull"]["top"]}
    # 高置信行 token 全权重 1.0；低置信行 token 减半 0.5（分词切法以 jieba 实际为准，
    # 断言「存在 0.5 权重 token」而非具体词面）
    assert any(abs(c - 1.0) < 1e-9 for c in counts.values()), f"高置信全权重缺失: {counts}"
    assert any(abs(c - 0.5) < 1e-9 for c in counts.values()), \
        f"低置信降权未生效: {counts}"
    assert jw["meta"]["low_conf_downweighted"] == 1
    assert jw["meta"]["conf_downweight"] == 0.5


def test_html_contains_jev_section(env):
    M.main(["--jev-weighted", "--days", "0"])
    html = (env / "t.html").read_text(encoding="utf-8")
    assert "JEV 判定加权词频" in html and "bull" in html and "bear" in html
