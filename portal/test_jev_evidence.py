# -*- coding: utf-8 -*-
"""api_jev_evidence 单测：消息流聚合/时间线/结论/参数。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_evidence as ae


@pytest.fixture()
def env(tmp_path, monkeypatch):
    ndb = tmp_path / "news.db"
    con = sqlite3.connect(str(ndb))
    con.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER,"
        " created_at TEXT, stocks TEXT, jev TEXT)")
    rows = [
        # 11-05：2 条回购利好（不同渠道）
        ("a", "sina", "2026-11-05 09:00", "A公司发布回购计划", "1", "bull"),
        ("b", "eastmoney", "2026-11-05 14:00", "B公司回购力度加大", "2", "bull"),
        # 11-06：3 条（1 空 2 中）
        ("c", "sina", "2026-11-06 10:00", "C公司回购被质疑", "3", "bear"),
        ("d", "announcement", "2026-11-06 16:00", "D公司回购公告", "4", "neutral"),
        ("e", "sina", "2026-11-06 20:00", "E公司例行回购", "5", "neutral"),
        # 无关行
        ("f", "sina", "2026-11-06 21:00", "完全无关标题", "6", "bull"),
    ]
    for nid, src, ts, title, mark, bias in rows:
        comps = ['300001'] if mark in "123" else []
        jev = json.dumps({"bias": bias, "conf": 0.8, "companies": comps},
                         ensure_ascii=False)
        con.execute(
            "INSERT INTO news(id, source, ts, title, content, hit, pushed,"
            " digested, created_at, stocks, jev) VALUES(?,?,?,?,'',0,0,0,?,'[]',?)",
            (nid, src, ts, title, ts, jev))
    con.commit(); con.close()

    monkeypatch.setattr(ae, "NEWS_DB", str(ndb))
    # 后验库指到不存在路径（available False 分支）
    monkeypatch.setattr(ae, "STATION", str(tmp_path))
    # datamart 面换假件
    import types
    fake = types.ModuleType("datamart")
    import pandas as pd
    def _read(name, codes=None, start=None, end=None, limit=None, **k):
        if name == "daily":
            return pd.DataFrame([{"trade_date": 20261105, "pct_chg": 2.5},
                                 {"trade_date": 20261106, "pct_chg": -1.0}])
        return pd.DataFrame()
    fake.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake)
    yield


def test_flow_aggregation(env):
    d = ae.compute("回购", 7, "day")
    f = d["flow"]
    assert f["total"] == 5 and f["is_code"] is False
    assert f["first"] == "2026-11-05 09:00" and f["last"] == "2026-11-06 20:00"
    assert f["duration_h"] == pytest.approx(35.0, abs=0.2)
    assert f["bias"] == {"bull": 2, "bear": 1, "neutral": 2, "judged": 5}
    srcs = {s["source"]: s["n"] for s in f["sources"]}
    assert srcs["sina"] == 3 and srcs["announcement"] == 1
    assert d["schema"] == "jev_evidence.v1"


def test_timeline_buckets(env):
    d = ae.compute("回购", 7, "day")
    tl = {t["t"]: t for t in d["timeline"]}
    assert tl["2026-11-05"]["n"] == 2 and tl["2026-11-05"]["bull"] == 2
    assert tl["2026-11-06"]["n"] == 3 and tl["2026-11-06"]["bear"] == 1


def test_targets_and_impact(env):
    d = ae.compute("回购", 7, "day")
    assert d["targets"] and d["targets"][0]["code"] == "300001"
    imp = {x["date"]: x for x in d["impact"]}
    assert imp["20261105"]["pct_chg"] == 2.5 and imp["20261105"]["news_n"] == 2
    assert imp["20261106"]["pct_chg"] == -1.0


def test_verdict_text(env):
    v = ae.compute("回购", 7, "day")["verdict"]
    assert "消息规模" in v and "持续" in v and "渠道" in v


def test_code_query_and_empty(env):
    d = ae.compute("300001", 7, "day")
    assert d["flow"]["is_code"] is True and d["flow"]["total"] == 3
    d2 = ae.compute("不存在的词组xyzq", 7, "day")
    assert d2["flow"]["total"] == 0 and "不存在" in d2["verdict"]


def test_hour_bucket_guard(env):
    # 7 天窗强制 hour → 降级 day（防桶爆炸）
    d = ae.compute("回购", 7, "hour")
    assert d["bucket"] == "day"


def test_bad_params(env):
    with pytest.raises(ValueError):
        ae.compute("", 7, "day")
    with pytest.raises(ValueError):
        ae.compute("x", 999, "day")
