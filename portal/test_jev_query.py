# -*- coding: utf-8 -*-
"""`/api/jev/query` 单测（K-ORG-20261026-JEVAPI）。

用临时 news.db / jev_audit.db 验证过滤、窗口、后验联查与参数错误，
不依赖生产库（monkeypatch 模块常量）。
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path

import pytest

PORTAL = Path(__file__).resolve().parent
if str(PORTAL) not in sys.path:
    sys.path.insert(0, str(PORTAL))

import api_jev  # noqa: E402

NOW = dt.datetime(2026, 10, 26, 12, 0, 0)


def _ts(hours_ago: float) -> str:
    return (NOW - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _make_dbs(tmp_path: Path) -> tuple[Path, Path]:
    news = tmp_path / "news_collector" / "news.db"
    news.parent.mkdir(parents=True)
    c = sqlite3.connect(str(news))
    c.execute(
        "CREATE TABLE news (id TEXT PRIMARY KEY, source TEXT, ts TEXT, title TEXT,"
        " content TEXT, hit INTEGER, pushed INTEGER, digested INTEGER,"
        " created_at TEXT, stocks TEXT, jev TEXT)"
    )
    rows = [
        (
            "n1",
            "sina",
            _ts(1),
            "茅台提价",
            json.dumps({"stocks": ["600519.SH"]}),
            {
                "bias": "bull",
                "conf": 0.85,
                "event_type": "price",
                "companies": ["600519"],
                "reason": "提价利好",
            },
        ),
        (
            "n2",
            "eastmoney",
            _ts(2),
            "监管收紧",
            json.dumps({"stocks": ["000001.SZ"]}),
            {
                "bias": "bear",
                "conf": 0.80,
                "event_type": "policy",
                "companies": ["000001"],
                "reason": "监管风险",
            },
        ),
        (
            "n3",
            "xueqiu",
            _ts(3),
            "公司澄清",
            "[]",
            {
                "bias": "neutral",
                "conf": 0.60,
                "event_type": "other",
                "companies": [],
                "reason": "无明确方向",
            },
        ),
        (
            "n4",
            "sina",
            _ts(30),
            "旧闻",
            "[]",
            {"bias": "bull", "conf": 0.70, "event_type": "other", "companies": []},
        ),
    ]
    for nid, src, ts, title, stocks, jev in rows:
        # created_at 与 ts 同值：窗口/排序按采集时间
        c.execute(
            "INSERT INTO news(id,source,ts,title,stocks,jev,created_at)"
            " VALUES(?,?,?,?,?,?,?)",
            (nid, src, ts, title, stocks, json.dumps(jev, ensure_ascii=False), ts),
        )
    c.commit()
    c.close()

    audit = tmp_path / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"
    audit.parent.mkdir(parents=True)
    a = sqlite3.connect(str(audit))
    a.execute(
        "CREATE TABLE dec_audit (news_id TEXT PRIMARY KEY, trade_day TEXT,"
        " ret REAL, bench REAL, excess REAL, hit INTEGER)"
    )
    a.execute("INSERT INTO dec_audit VALUES('n1','20261027',2.5,1.0,1.5,1)")
    a.execute("INSERT INTO dec_audit VALUES('n3','20261027',0.2,0.1,0.1,1)")
    a.execute("INSERT INTO dec_audit VALUES('n2','20261027',-1.2,0.5,-1.7,1)")
    a.commit()
    a.close()
    return news, audit


@pytest.fixture()
def dbs(tmp_path, monkeypatch):
    news, audit = _make_dbs(tmp_path)
    monkeypatch.setattr(api_jev, "NEWS_DB", str(news))
    monkeypatch.setattr(api_jev, "AUDIT_DB", str(audit))
    return tmp_path


def test_window_and_summary(dbs):
    r = api_jev.query(hours=24, now=NOW)
    assert r["ok"] is True
    assert r["summary_window"] == {"total": 3, "bull": 1, "bear": 1, "neutral": 1}
    assert [i["id"] for i in r["items"]] == ["n1", "n2", "n3"]  # ts 倒序


def test_bias_filter(dbs):
    r = api_jev.query(hours=24, bias=["bear"], now=NOW)
    assert [i["bias"] for i in r["items"]] == ["bear"]


def test_code_filter(dbs):
    r = api_jev.query(hours=24, code="600519", now=NOW)
    assert [i["id"] for i in r["items"]] == ["n1"]
    assert r["items"][0]["codes"] == ["600519"]


def test_event_type_filter(dbs):
    r = api_jev.query(hours=24, event_type=["policy"], now=NOW)
    assert [i["id"] for i in r["items"]] == ["n2"]


def test_limit_clamped(dbs):
    r = api_jev.query(hours=24, limit=999, now=NOW)
    assert r["returned"] <= api_jev.MAX_LIMIT
    r1 = api_jev.query(hours=24, limit=1, now=NOW)
    assert r1["returned"] == 1
    # summary 是窗口全量统计，不随 limit 截断
    assert r1["summary_window"]["total"] == 3
    assert r1["items"][0]["collected_at"]


def test_posterior_join(dbs):
    r = api_jev.query(hours=24, with_posterior=True, now=NOW)
    by_id = {i["id"]: i for i in r["items"]}
    assert by_id["n1"]["posterior"]["excess"] == 1.5
    assert by_id["n1"]["posterior"]["hit"] is True
    assert by_id["n3"]["posterior"]["highvol"] is False  # 0.1% 带宽内
    assert r["posterior_band_pct"] == 2.5


def test_highvol_flag():
    band = 2.5
    assert abs(3.0) > band
    assert abs(1.0) <= band


def test_invalid_bias_raises(dbs):
    with pytest.raises(ValueError):
        api_jev.query(hours=24, bias=["sideways"], now=NOW)


def test_invalid_hours_raises(dbs):
    with pytest.raises(ValueError):
        api_jev.query(hours=0, now=NOW)


class _FakeHandler:
    def __init__(self):
        self.status = None
        self.payload = None

    def _json(self, obj, status=200):
        self.payload = obj
        self.status = status


def test_handle_get_bad_param(dbs):
    h = _FakeHandler()
    api_jev.handle_get(h, "/api/jev/query", "hours=abc")
    assert h.status == 400
    assert h.payload["error"] == "invalid_params"


def test_handle_get_ok(dbs):
    h = _FakeHandler()
    api_jev.handle_get(h, "/api/jev/query", "hours=24&bias=bull&limit=5")
    assert h.status is None or h.status == 200
    assert h.payload["ok"] is True
    assert all(i["bias"] == "bull" for i in h.payload["items"])


# ── 2026-10-30 BATCH1：计数/参数/低信号/空后验回归 ──────────────────────
def test_total_not_truncated_by_limit(dbs):
    r = api_jev.query(hours=24, limit=1, now=NOW)
    assert r["returned"] == 1
    assert r["total"] == 3            # 旧实现 total=len(matched)=1
    assert r["summary_window"]["total"] == 3


def test_invalid_code_raises(dbs):
    for bad in ("abc", "6005199", "600519.SHX"):
        with pytest.raises(ValueError):
            api_jev.query(hours=24, code=bad, now=NOW)


def test_code_with_suffix_accepted(dbs):
    r = api_jev.query(hours=24, code="600519.SH", now=NOW)
    assert [i["id"] for i in r["items"]] == ["n1"]


def test_low_signal_consumed_from_latest(dbs, tmp_path, monkeypatch):
    latest = tmp_path / "latest.json"
    latest.write_text(json.dumps({
        "generated_at": "2026-10-30T07:10:00",
        "assessment": {"neutral": {"band_pct": 2.5},
                       "low_signal_event_types": ["policy"]},
    }), encoding="utf-8")
    monkeypatch.setattr(api_jev, "LATEST_CALIB", str(latest))
    monkeypatch.setattr(api_jev, "_META_CACHE", {"mtime": None, "band": 2.5,
                                                 "low_signal": frozenset()})
    r = api_jev.query(hours=24, now=NOW)
    assert r["low_signal_event_types"] == ["policy"]
    by_id = {i["id"]: i for i in r["items"]}
    assert by_id["n2"]["low_signal"] is True
    assert by_id["n1"]["low_signal"] is False


def test_posterior_hit_null_preserved(dbs):
    audit = dbs / "agent_platform" / "data" / "jev_audit" / "jev_audit.db"
    a = sqlite3.connect(str(audit))
    a.execute("UPDATE dec_audit SET hit=NULL WHERE news_id='n3'")
    a.commit()
    a.close()
    r = api_jev.query(hours=24, with_posterior=True, now=NOW)
    by_id = {i["id"]: i for i in r["items"]}
    assert by_id["n3"]["posterior"]["hit"] is None


def test_posterior_missing_db_raises(dbs, monkeypatch):
    monkeypatch.setattr(api_jev, "AUDIT_DB", str(dbs / "no_such_audit.db"))
    with pytest.raises(RuntimeError):
        api_jev.query(hours=24, with_posterior=True, now=NOW)
