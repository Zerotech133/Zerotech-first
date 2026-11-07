# -*- coding: utf-8 -*-
"""api_jev_intraday 单测：5 分钟聚合/事件对齐/反应计算/降级。"""
import json
import sqlite3
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_intraday as ai


@pytest.fixture()
def env(tmp_path, monkeypatch):
    ndb = tmp_path / "news.db"
    con = sqlite3.connect(str(ndb))
    con.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INT, pushed INT, digested INT, created_at TEXT,"
        " stocks TEXT, jev TEXT)")
    evs = [
        ("a", "sina", "2026-11-06 09:40:00", "盘中利好", "bull"),   # 盘中
        ("b", "sina", "2026-11-06 22:00:00", "盘后利好", "bull"),   # 盘后
        ("c", "sina", "2026-11-06 10:00:00", "无关标的", "bear"),   # 不匹配 → 滤
    ]
    for nid, src, ts, title, bias in evs:
        comps = ["300001"] if nid != "c" else ["000002"]
        jev = json.dumps({"bias": bias, "conf": 0.8, "companies": comps})
        con.execute(
            "INSERT INTO news(id,source,ts,title,content,hit,pushed,digested,"
            "created_at,stocks,jev) VALUES(?,?,?,?,'',0,0,0,?,'[]',?)",
            (nid, src, ts, title, ts, jev))
    con.commit(); con.close()
    monkeypatch.setattr(ai, "NEWS_DB", str(ndb))

    # datamart 假件：1 分钟线（09:30-10:00 每 2 分钟一根，价格线性涨）
    rows = []
    px = 10.0
    for mins in range(0, 30, 2):
        hh = 9 + (30 + mins) // 60
        mm = (30 + mins) % 60
        px += 0.1
        rows.append({"trade_time": "2026-11-06 %02d:%02d:00" % (hh, mm),
                     "close": round(px, 2), "open": px, "high": px, "low": px,
                     "vol": 1000.0, "amount": 10000.0})
    fake = types.ModuleType("datamart")

    def _read(name, codes=None, limit=None, **k):
        if name == "stk_mins":
            return pd.DataFrame(rows)
        if name == "daily":
            return pd.DataFrame([
                {"trade_date": 20261105, "close": 10.0},
                {"trade_date": 20261106, "close": 12.0},
            ])
        return pd.DataFrame()
    fake.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake)
    yield


def test_five_min_bars(env):
    d = ai.compute("300001", "20261106")
    assert d["available"] and d["bars"]
    # 09:30-09:59 每 2 分钟一根 → 桶 09:30/35/40/45/50/55 = 6 棒
    assert d["bars"][0]["t"] == "09:30"
    assert len(d["bars"]) == 6
    # 基准=昨收 10.0；首棒 close≈10.1 → cum_pct≈1.0
    assert d["base"]["kind"] == "prev_close"


def test_events_filtered_by_target(env):
    d = ai.compute("300001", "20261106")
    hhmm = [e["hhmm"] for e in d["events"]]
    assert "09:40" in hhmm          # 盘中事件
    assert "22:00" in hhmm          # 盘后事件也在（反应映射到尾棒）
    assert all(e["title"] != "无关标的" for e in d["events"])


def test_reaction_windows(env):
    d = ai.compute("300001", "20261106")
    rx = {r["hhmm"]: r for r in d["reactions"]}
    assert "09:40" in rx
    r = rx["09:40"]
    assert set(r["chg"].keys()) == {"m5", "m15", "m30", "m60"}
    # 价格线性上涨，利多事件 → 各窗口为正
    assert r["chg"]["m5"] > 0


def test_unavailable_day(env):
    d = ai.compute("300001", "20261101")  # 假库无此日
    assert d["available"] is False


def test_bad_code(env):
    with pytest.raises(ValueError):
        ai.compute("abc", "20261106")
