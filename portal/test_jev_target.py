# -*- coding: utf-8 -*-
"""api_jev_target 单测：归一/四块聚合/降级/缓存/参数校验。"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import api_jev_target as at


@pytest.fixture()
def env(tmp_path, monkeypatch):
    # news.db 假库：两条相关判定（600519）+ 一条无关
    ndb = tmp_path / "news.db"
    con = sqlite3.connect(str(ndb))
    con.execute(
        "CREATE TABLE news(id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER,"
        " created_at TEXT, stocks TEXT, jev TEXT)")
    j1 = json.dumps({"bias": "bull", "conf": 0.8, "event_type": "policy",
                     "stocks": ["600519.SH"], "companies": ["600519"]},
                    ensure_ascii=False)
    j2 = json.dumps({"bias": "bear", "conf": 0.4, "event_type": "other",
                     "stocks": [], "companies": ["600519"],
                     "pref": {"reason": "x"}}, ensure_ascii=False)
    con.execute("INSERT INTO news VALUES('a','sina','2026-11-06 10:00','利好茅台','c',0,0,0,'2026-11-06 10:00','[]',?)", (j1,))
    con.execute("INSERT INTO news VALUES('b','sina','2026-11-05 10:00','利空茅台','c',0,0,0,'2026-11-05 10:00','[]',?)", (j2,))
    con.execute("INSERT INTO news VALUES('c','sina','2026-11-06 11:00','无关','c',0,0,0,'2026-11-06 11:00','[]','{\"bias\":\"bull\",\"companies\":[\"000001\"]}')")
    con.commit(); con.close()

    # audit 假库
    adb = tmp_path / "jev_audit.db"
    con = sqlite3.connect(str(adb))
    con.execute("CREATE TABLE dec_audit(seq INTEGER, news_id TEXT, companies TEXT, ret REAL, hit INTEGER)")
    con.execute("INSERT INTO dec_audit VALUES(1,'a','[\"600519\"]',2.5,1)")
    con.execute("INSERT INTO dec_audit VALUES(2,'b','[\"600519\"]',-1.0,0)")
    con.commit(); con.close()

    monkeypatch.setattr(at, "NEWS_DB", str(ndb))
    monkeypatch.setattr(at, "AUDIT_DB", str(adb))
    # datamart 面换假件（不触真库）
    import types
    fake_dm = types.ModuleType("datamart")
    import pandas as pd
    def _read(name, codes=None, start=None, end=None, limit=None, **k):
        if name == "stock_basic":
            return pd.DataFrame([{"symbol": "600519", "name": "X酒",
                                  "industry": "白酒", "area": "贵州",
                                  "list_date": "20010827", "list_status": "L"}])
        if name == "daily":
            return pd.DataFrame([
                {"trade_date": 20261105, "close": 10.0, "pct_chg": 1.0},
                {"trade_date": 20261106, "close": 11.0, "pct_chg": 10.0},
            ])
        return pd.DataFrame()
    fake_dm.read = _read
    monkeypatch.setitem(sys.modules, "datamart", fake_dm)
    yield


def test_norm_code(env):
    assert at._norm_code("600519") == ("600519.SH", "600519")
    assert at._norm_code("000001.SZ") == ("000001.SZ", "000001")
    assert at._norm_code("830001") == ("830001.BJ", "830001")
    assert at._norm_code("abc") is None


def test_compute_aggregates_four_blocks(env):
    d = at.compute("600519", 30, 10)
    assert d["schema"] == "jev_target.v1" and d["ts_code"] == "600519.SH"
    assert d["profile"]["available"] and d["profile"]["name"] == "X酒"
    assert d["quotes"]["available"] and d["quotes"]["last_close"] == 11.0
    j = d["judgments"]
    assert j["total"] == 2 and j["dist"] == {"bull": 1, "bear": 1, "neutral": 0}
    assert j["net_bias"] == 0.0
    assert j["posterior"]["judged_total"] == 2 and j["posterior"]["verified"] == 2
    assert j["posterior"]["hit_rate"] == 0.5
    ids = [it["id"] for it in j["items"]]
    assert "c" not in ids  # 无关行被精确过滤


def test_prefilter_src_labeled(env):
    d = at.compute("600519", 30, 10)
    by_id = {it["id"]: it for it in d["judgments"]["items"]}
    assert by_id["a"]["src"] == "model"
    assert by_id["b"]["src"] == "prefilter"


def test_bad_code_and_params(env):
    with pytest.raises(ValueError):
        at.compute("abcdef", 30, 10)  # compute 层校验 code
    # days/limit 校验在 handle 层（HTTP 400）
    h = _H()
    at.handle_get(h, "/api/jev/target/600519", "days=999")
    assert h.status == 400


def test_cache_hit(env):
    d1 = at.compute("600519", 30, 10)
    d2 = at.compute("600519", 30, 10)
    assert d2 is d1  # 60s 内同对象


class _H:
    def __init__(self):
        self.status = None
        self.body = None

    def _json(self, obj, status=200):
        self.status, self.body = status, obj


def test_handle_routes(env):
    h = _H()
    at.handle_get(h, "/api/jev/target/600519", "days=30&limit=5")
    assert h.status == 200 and h.body["schema"] == "jev_target.v1"
    h2 = _H()
    at.handle_get(h2, "/api/jev/target/600519", "days=999")
    assert h2.status == 400
    h3 = _H()
    at.handle_get(h3, "/api/jev/target/", "")
    assert h3.status == 400
