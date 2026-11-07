# -*- coding: utf-8 -*-
"""`/api/jev/wordcloud` 单测（WO-INTEL-WORDC1-JEV-20261107-001 治理线实现）。

临时 news.db 验证分桶/降权/停用词/malformed/参数校验，不依赖生产库
（monkeypatch 模块常量 NEWS_DB；缓存键隔离）。
"""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_jev_wordcloud as M  # noqa: E402


def _make_db(tmp_path: Path, rows: list[tuple]) -> Path:
    news = tmp_path / "news_collector" / "news.db"
    news.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(news))
    c.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER,"
        " created_at TEXT, stocks TEXT, jev TEXT)"
    )
    c.executemany(
        "INSERT INTO news (id, source, ts, title, jev) VALUES (?,?,?,?,?)", rows)
    c.commit()
    c.close()
    return news


TS = "2026-11-07 08:00:00"


def _j(bias="bull", conf=0.9, event_type="price"):
    return json.dumps({"bias": bias, "conf": conf, "event_type": event_type})


@pytest.fixture()
def small_db(tmp_path, monkeypatch):
    rows = []
    # bull 3 条（涨停 板块 冲高 / 一条带停用词的）
    for i, t in enumerate(["宁德时代涨停带动板块冲高", "半导体板块大涨冲高", "券商冲高涨停潮"]):
        rows.append(("b%d" % i, "sina", TS, t, _j("bull")))
    # bear 3 条（低置信 1 条触发降权）
    rows.append(("s0", "sina", TS, "光伏组件价格下跌亏损扩大", _j("bear", conf=0.4)))
    rows.append(("s1", "sina", TS, "锂矿价格下跌产能过剩", _j("bear")))
    rows.append(("s2", "sina", TS, "出口订单下滑亏损预警", _j("bear")))
    # neutral 1 条
    rows.append(("n0", "sina", TS, "公司发布公告将于明日召开股东大会", _j("neutral")))
    # malformed：非法 JSON + 未知 bias 各 1
    rows.append(("m0", "sina", TS, "坏JSON行标题测试", "{not-json"))
    rows.append(("m1", "sina", TS, "未知bias行标题测试", _j("mixed")))
    # 未判 2 条
    rows.append(("u0", "sina", TS, "美联储加息预期升温", None))
    rows.append(("u1", "sina", TS, "原油库存超预期", None))
    db = _make_db(tmp_path, rows)
    monkeypatch.setattr(M, "NEWS_DB", str(db))
    M._CACHE.update(key=None, at=0.0, data=None)  # 清缓存防串
    return db


def test_buckets_counts_and_top(small_db):
    d = M.compute(days=30, top_n=10, event_top=2, event_words=5)
    assert d["ok"] is True and d["schema"] == "jev_wordcloud.v1"
    assert d["buckets"]["bull"]["count"] == 3
    assert d["buckets"]["bear"]["count"] == 3
    assert d["buckets"]["neutral"]["count"] == 1
    assert d["malformed"] == 2 and d["total_judged"] == 7
    # 注意：样本 < MIN_BUCKET_N(1000) → enough=False，top 为空（诚实占位，不造假分布）
    assert d["buckets"]["bull"]["enough"] is False and d["buckets"]["bull"]["top"] == []
    assert "样本不足" in d["buckets"]["bull"]["note"]


def test_stopwords_and_dedup(small_db):
    """停用词复用 wordc1_cloud 口径；标题级去重（同一词只计一次）。"""
    d = M.compute(days=30, top_n=80, event_top=0, event_words=5)
    # 直接绕开 enough 门检验内层计数：临时抬 MIN_BUCKET_N
    old = M.MIN_BUCKET_N
    M.MIN_BUCKET_N = 1
    try:
        d2 = M.compute(days=30, top_n=80, event_top=0, event_words=5)
    finally:
        M.MIN_BUCKET_N = old
    words = {x["w"] for x in d2["buckets"]["bull"]["top"]}
    assert words, "bull top 不应为空"
    assert "的" not in words and "了" not in words, "停用词必须被过滤"
    assert all(x["n"] <= 3 for x in d2["buckets"]["bull"]["top"]), "标题级去重：单词权重 ≤ 条数"


def test_conf_downweight(small_db):
    old = M.MIN_BUCKET_N
    M.MIN_BUCKET_N = 1
    try:
        d = M.compute(days=30, top_n=80, event_top=0, event_words=5)
    finally:
        M.MIN_BUCKET_N = old
    bear = {x["w"]: x["n"] for x in d["buckets"]["bear"]["top"]}
    # 「下跌」出现在低置信行(0.4)与正常行 → 权重 1.5；「产能」只在正常行 → 1.0
    assert abs(bear.get("下跌", 0) - 1.5) < 1e-6
    assert bear.get("产能") == 1.0


def test_unjudged_sampled(small_db):
    d = M.compute(days=30, top_n=10, event_top=0, event_words=5)
    assert d["unjudged"]["sampled"] == 2
    assert all(x["n"] >= 1 for x in d["unjudged"]["top"])


def test_event_matrix(small_db):
    d = M.compute(days=30, top_n=10, event_top=3, event_words=5)
    ets = [e["event_type"] for e in d["event_matrix"]]
    assert "price" in ets
    for e in d["event_matrix"]:
        assert len(e["words"]) <= 5 and e["count"] >= 1


def test_param_validation(small_db):
    with pytest.raises(ValueError):
        M._as_int({"days": ["0"]}, "days", 90, 1, 730)
    with pytest.raises(ValueError):
        M._as_int({"days": ["99999"]}, "days", 90, 1, 730)
    with pytest.raises(ValueError):
        M._as_int({"days": ["abc"]}, "days", 90, 1, 730)
    assert M._as_int({"days": ["180"]}, "days", 90, 1, 730) == 180


def test_readonly_uri(small_db):
    """写守卫：连接串必须带 mode=ro（只读纪律，工单验收判据 4）。"""
    import inspect
    src = inspect.getsource(M.compute)
    assert "mode=ro" in src
