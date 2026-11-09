# -*- coding: utf-8 -*-
"""api_jev_story 单测：事件串联/双轨/信源战绩/验证计数/结论。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_story as st


@pytest.fixture()
def env(tmp_path, monkeypatch):
    ndb = tmp_path / "news.db"
    con = sqlite3.connect(str(ndb))
    con.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER,"
        " created_at TEXT, stocks TEXT, jev TEXT)")
    data = [
        # (id, source, ts, title, bias, companies, 是否进后验库, hit)
        ("a", "sina",     "2026-11-05 09:00", "利好A", "bull", ["300001"], True, True),
        ("b", "sina",     "2026-11-05 15:00", "利好B", "bull", ["300001"], True, False),
        ("c", "eastmoney","2026-11-06 10:00", "利空C", "bear", ["300001"], True, False),
        ("d", "eastmoney","2026-11-06 14:00", "中性D", "neutral", ["300001"], False, None),
        ("e", "sina",     "2026-11-06 16:00", "无关E", "bull", ["000002"], False, None),
    ]
    for nid, src, ts, title, bias, comps, _, _ in data:
        jev = json.dumps({"bias": bias, "conf": 0.8, "companies": comps},
                         ensure_ascii=False)
        con.execute(
            "INSERT INTO news(id, source, ts, title, content, hit, pushed,"
            " digested, created_at, stocks, jev) VALUES(?,?,?,?,'',0,0,0,?,'[]',?)",
            (nid, src, ts, title, ts, jev))
    con.commit(); con.close()

    adb = tmp_path / "jev_audit.db"
    con = sqlite3.connect(str(adb))
    con.execute("CREATE TABLE dec_audit(seq INTEGER, news_id TEXT, ts TEXT,"
                " trade_day TEXT, source TEXT, bias TEXT, conf REAL,"
                " event_type TEXT, companies TEXT, model TEXT,"
                " prompt_version TEXT, ret REAL, bench REAL, excess REAL,"
                " hit INTEGER, posterior_at TEXT, updated_at TEXT,"
                " created_at TEXT, horizon TEXT, prob TEXT)")
    # a 命中、b 不中、c 不中（后验口径）
    con.execute("INSERT INTO dec_audit(news_id, trade_day, companies, ret, excess, hit) VALUES('a','20261106','[\"300001\"]',2.0,1.5,1)")
    con.execute("INSERT INTO dec_audit(news_id, trade_day, companies, ret, excess, hit) VALUES('b','20261106','[\"300001\"]',-1.0,-0.5,0)")
    con.execute("INSERT INTO dec_audit(news_id, trade_day, companies, ret, excess, hit) VALUES('c','20261106','[\"300001\"]',0.5,0.2,0)")
    con.commit(); con.close()

    monkeypatch.setattr(st, "NEWS_DB", str(ndb))
    monkeypatch.setattr(st, "AUDIT_DB", str(adb))
    import types
    fake = types.ModuleType("datamart")
    import pandas as pd
    def _read(name, codes=None, start=None, end=None, limit=None, **k):
        if name == "daily":
            return pd.DataFrame([
                {"trade_date": 20261105, "close": 10.0, "pct_chg": 2.0, "vol": 1000.0},
                {"trade_date": 20261106, "close": 9.8, "pct_chg": -2.0, "vol": 900.0},
            ])
        return pd.DataFrame()
    fake.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake)
    yield


def test_timeline_ordered_and_filtered(env):
    d = st.compute("300001", 30, 50)
    tl = d["timeline"]
    assert len(tl) == 4  # e（000002）被滤掉
    assert tl[0]["ts"] <= tl[-1]["ts"]  # 时间正序
    assert all(e["source"] in ("sina", "eastmoney") for e in tl)


def test_posterior_aligned(env):
    d = st.compute("300001", 30, 50)
    by_id = {e["id"]: e for e in d["timeline"]}
    assert by_id["a"]["hit"] is True and by_id["a"]["ret"] == 2.0
    assert by_id["b"]["hit"] is False
    assert by_id["c"]["hit"] is False  # 后验证伪（bear 但 ret>0）
    # d 无后验记录（audit 只建了 a/b/c）→ 待验或次日兜底
    assert by_id["d"]["hit"] is None or by_id["d"]["ret"] is not None


def test_source_scoreboard(env):
    d = st.compute("300001", 30, 50)
    srcs = {s["source"]: s for s in d["sources"]}
    assert srcs["sina"]["n"] == 2 and srcs["sina"]["hit"] == 1
    assert srcs["eastmoney"]["n"] == 2
    # sina 准确率 1/2=0.5 排前（eastmoney 0/1=0）
    assert d["sources"][0]["source"] == "sina"


def test_confirm_counts(env):
    d = st.compute("300001", 30, 50)
    cf = d["confirm"]
    # a 后验命中=证实；b/c 后验 hit=0=证伪；d 无后验无次日价=待验
    assert cf["confirmed"] == 1 and cf["refuted"] == 2


def test_verdict_mentions(env):
    v = st.compute("300001", 30, 50)["verdict"]
    assert "串起" in v and "证实" in v and "信源" in v


def test_quotes_track(env):
    d = st.compute("300001", 30, 50)
    assert len(d["quotes"]) == 2 and d["quotes"][0]["pct_chg"] == 2.0


def test_bad_code(env):
    with pytest.raises(ValueError):
        st.compute("abc", 30, 50)
