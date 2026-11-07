# -*- coding: utf-8 -*-
"""api_jev_target 单测：归一/四面聚合/形态量价/降级/缓存隔离/参数校验。

v2 增补：情绪面独立取数（含未判定行与标题命中）、quotes.form 形态量价、
block_status 逐块可用性、缓存 key 含数据源（换库不返旧缓存）。
"""
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_target as at

NAME = "贵州茅台"


def _ts(days_ago=1, hour=10):
    """用相对时间造 ts，避免写死日期变成时间炸弹（days 窗口过滤会失效）。"""
    t = dt.datetime.now() - dt.timedelta(days=days_ago)
    return t.strftime("%Y-%m-%d") + " %02d:00" % hour


def _daily_rows(n=22, with_vol=True):
    rows = []
    for i in range(n):
        close = round(10 + i * 0.1, 2)
        pre = round(10 + (i - 1) * 0.1, 2)
        if i >= n - 3:
            pct = 1.0                       # 末端三连阳（精确三根，边界不给歧义）
        elif i == n - 4:
            pct = -0.5
        else:
            pct = -0.5 if i % 2 else 1.0
        vol = float(3000 if i >= n - 5 else 1000)               # 末端放量
        rows.append({
            "trade_date": 20260101 + i,
            "open": close, "high": round(close * 1.02, 2),
            "low": round(close * 0.99, 2), "close": close,
            "pre_close": pre, "change": round(close - pre, 2),
            "pct_chg": pct,
            "vol": vol if with_vol else None,
            "amount": (vol * 100) if with_vol else None,
        })
    return rows


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # news.db 假库：2 条已判定 + 1 条未判定但标题含公司名 + 1 条无关
    ndb = tmp_path / "news.db"
    con = sqlite3.connect(str(ndb))
    con.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER,"
        " created_at TEXT, stocks TEXT, jev TEXT)")
    j1 = json.dumps({"bias": "bull", "conf": 0.8, "event_type": "policy",
                     "stocks": ["600519.SH"], "companies": ["600519"]},
                    ensure_ascii=False)
    j2 = json.dumps({"bias": "bear", "conf": 0.4, "event_type": "earnings",
                     "stocks": [], "companies": ["600519"],
                     "pref": {"reason": "x"}}, ensure_ascii=False)
    j3 = json.dumps({"bias": "bull", "companies": ["000001"]}, ensure_ascii=False)
    ins = ("INSERT INTO news VALUES(?,?,?,?, 'c',0,0,0,?,?,?)")
    con.execute(ins, ("a", "sina", _ts(1), "利好茅台", _ts(1), "[]", j1))
    con.execute(ins, ("b", "eastmoney", _ts(2), "利空茅台", _ts(2), "[]", j2))
    # d: 未判定，仅标题含公司名 —— 情绪面必须收进来，判定面必须收不进来
    con.execute(ins, ("d", "chinanews", _ts(3), NAME + "三季报点评", _ts(3), "[]", None))
    # e: 无关股票
    con.execute(ins, ("e", "sina", _ts(1), "平安银行增持", _ts(1), "[]", j3))
    con.commit(); con.close()

    # audit 假库
    adb = tmp_path / "jev_audit.db"
    con = sqlite3.connect(str(adb))
    con.execute("CREATE TABLE dec_audit(seq INTEGER, news_id TEXT, companies TEXT,"
                " ret REAL, hit INTEGER)")
    con.execute("INSERT INTO dec_audit VALUES(1,'a','[\"600519\"]',2.5,1)")
    con.execute("INSERT INTO dec_audit VALUES(2,'b','[\"600519\"]',-1.0,0)")
    con.commit(); con.close()

    monkeypatch.setattr(at, "NEWS_DB", str(ndb))
    monkeypatch.setattr(at, "AUDIT_DB", str(adb))

    import types
    import pandas as pd
    fake_dm = types.ModuleType("datamart")

    def _read(name, codes=None, start=None, end=None, limit=None, **k):
        if name == "stock_basic":
            return pd.DataFrame([{"symbol": "600519", "name": NAME,
                                  "industry": "白酒", "area": "贵州",
                                  "list_date": "20010827", "list_status": "L"}])
        if name == "daily":
            return pd.DataFrame(_daily_rows())
        return pd.DataFrame()

    fake_dm.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake_dm)
    yield ndb, adb


class _H:
    def __init__(self):
        self.status = None
        self.body = None

    def _json(self, obj, status=200):
        self.status, self.body = status, obj


# ------------------------------------------------------------------ 基础
def test_norm_code(env):
    assert at._norm_code("600519") == ("600519.SH", "600519")
    assert at._norm_code("000001.SZ") == ("000001.SZ", "000001")
    assert at._norm_code("830001") == ("830001.BJ", "830001")
    assert at._norm_code("abc") is None


def test_compute_aggregates_four_blocks(env):
    d = at.compute("600519", 30, 10)
    assert d["schema"] == "jev_target.v2" and d["ts_code"] == "600519.SH"
    assert d["profile"]["available"] and d["profile"]["name"] == NAME
    assert d["quotes"]["available"] and d["quotes"]["last_close"] == 12.1
    j = d["judgments"]
    assert j["total"] == 2 and j["dist"] == {"bull": 1, "bear": 1, "neutral": 0}
    assert j["net_bias"] == 0.0
    assert j["posterior"]["judged_total"] == 2 and j["posterior"]["verified"] == 2
    assert j["posterior"]["hit_rate"] == 0.5
    ids = [it["id"] for it in j["items"]]
    assert "e" not in ids and "d" not in ids  # 无关行 / 未判定行都不进判定面


def test_block_status_all_true(env):
    d = at.compute("600519", 30, 10)
    assert d["block_status"] == {"profile": True, "quotes": True,
                                 "sentiment": True, "judgments": True}


# ------------------------------------------------------------------ 情绪面（v2 核心）
def test_sentiment_is_independent_of_judgments(env):
    """情绪面必须看到判定面看不到的未判定消息，否则就是换名。"""
    d = at.compute("600519", 30, 10)
    s, j = d["sentiment"], d["judgments"]
    assert s["available"] is True
    assert s["total"] == 3, "a/b 两条已判定 + d 一条标题命中"
    assert j["total"] == 2
    assert s["unjudged"] == 1, "d 无 jev，应计入未判定"
    assert s["judged"] == 2
    assert s["dist"] == {"bull": 1, "bear": 1, "neutral": 0}


def test_sentiment_title_match_declared(env):
    d = at.compute("600519", 30, 10)
    mb = d["sentiment"]["match_basis"]
    assert NAME in mb["title_field"] and "1 条" in mb["title_field"]
    assert "非独立文本情感模型" in mb["polarity_from"]  # 不冒充情感打分


def test_sentiment_mixes_and_heat(env):
    d = at.compute("600519", 30, 10)
    s = d["sentiment"]
    srcs = {x["k"]: x["n"] for x in s["source_mix"]}
    assert srcs == {"sina": 1, "eastmoney": 1, "chinanews": 1}
    assert {x["k"] for x in s["event_mix"]} == {"policy", "earnings"}
    assert sum(x["n"] for x in s["daily_heat"]) == s["total"]
    assert s["peak_day"]["n"] >= 1
    assert s["conf_avg"] == pytest.approx(0.6, abs=1e-6)


def test_sentiment_reps_capped(env):
    d = at.compute("600519", 30, 100)   # limit 很大，代表标题仍只各 3
    reps = d["sentiment"]["reps"]
    assert len(reps["bull"]) <= 3 and len(reps["bear"]) <= 3
    assert reps["bull"][0]["evidence_item"].startswith("/api/jev/evidence/item?id=")


# ------------------------------------------------------------------ 形态量价
def test_form_derived(env):
    f = at.compute("600519", 30, 10)["quotes"]["form"]
    assert f["available"] is True
    assert f["label"] == "高位放量上行", f
    assert f["pos_in_range"] == 1.0
    assert f["vol_ratio_5_20"] == pytest.approx(2.0, abs=0.01)
    assert f["streak"] == 3 and f["streak_dir"] == "up"
    assert f["above_ma20"] is True
    assert f["ma5"] and f["ma20"]
    assert f["basis"]["note"] and len(f["basis"]["range"]) == 2


def test_form_degrades_without_volume(env, monkeypatch):
    import pandas as pd
    import types
    fake = types.ModuleType("datamart")
    rows = _daily_rows(with_vol=False)

    def _read(name, codes=None, start=None, end=None, limit=None, **k):
        if name == "stock_basic":
            return pd.DataFrame([{"symbol": "600519", "name": NAME,
                                  "industry": "白酒", "area": "贵州",
                                  "list_date": "20010827", "list_status": "L"}])
        return pd.DataFrame(rows)

    fake.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake)
    f = at.compute("600519", 30, 10)["quotes"]["form"]
    assert f["available"] is True          # 无量的品种仍能判形态
    assert f["vol_ratio_5_20"] is None
    assert "量能未知" in f["label"]        # 不装作有量能


def test_form_needs_two_bars(env, monkeypatch):
    import pandas as pd
    import types
    fake = types.ModuleType("datamart")
    one = _daily_rows()[:1]

    def _read(name, codes=None, start=None, end=None, limit=None, **k):
        return pd.DataFrame(one)

    fake.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake)
    q = at.compute("600001", 30, 10)["quotes"]
    assert q["available"] is True
    assert q["form"]["available"] is False and q["form"]["reason"]


# ------------------------------------------------------------------ 溯源与缓存
def test_evidence_item_path_on_each_judgment(env):
    d = at.compute("600519", 30, 10)
    for it in d["judgments"]["items"]:
        assert it["evidence_item"].endswith("&code=600519")
        assert it["id"] in it["evidence_item"]


def test_cache_isolated_by_datasource(env, tmp_path, monkeypatch):
    """换了库不该返回旧缓存——原实现 key 不含数据源，测试会被他测污染。"""
    d1 = at.compute("600519", 30, 10)
    assert d1["sentiment"]["total"] == 3

    ndb2 = tmp_path / "news2.db"
    con = sqlite3.connect(str(ndb2))
    con.execute("CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT,"
                " title TEXT, content TEXT, hit INTEGER, pushed INTEGER,"
                " digested INTEGER, created_at TEXT, stocks TEXT, jev TEXT)")
    j = json.dumps({"bias": "bull", "companies": ["600519"]}, ensure_ascii=False)
    con.execute("INSERT INTO news VALUES('z','sina',?, '另一条', 'c',0,0,0,?, '[]', ?)",
                (_ts(1), _ts(1), j))
    con.commit(); con.close()

    monkeypatch.setattr(at, "NEWS_DB", str(ndb2))
    d2 = at.compute("600519", 30, 10)
    assert d2["sentiment"]["total"] == 1, "新库只有 1 条，说明缓存已按数据源失效"
    assert d1 is not d2


def test_cache_hit_same_source(env):
    d1 = at.compute("600519", 30, 10)
    d2 = at.compute("600519", 30, 10)
    assert d2 is d1  # 同源同参 60s 内复用


# ------------------------------------------------------------------ 参数与路由
def test_bad_code_and_params(env):
    with pytest.raises(ValueError):
        at.compute("abcdef", 30, 10)
    h = _H()
    at.handle_get(h, "/api/jev/target/600519", "days=999")
    assert h.status == 400


def test_handle_routes(env):
    h = _H()
    at.handle_get(h, "/api/jev/target/600519", "days=30&limit=5")
    assert h.status == 200 and h.body["schema"] == "jev_target.v2"
    assert set(h.body["block_status"]) == {"profile", "quotes", "sentiment", "judgments"}
    h2 = _H()
    at.handle_get(h2, "/api/jev/target/600519", "days=999")
    assert h2.status == 400
    h3 = _H()
    at.handle_get(h3, "/api/jev/target/", "")
    assert h3.status == 400
