# -*- coding: utf-8 -*-
"""BATCH1 回归：posterior 导出契约、T+1 信号日口径、覆盖率/门控字段。"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import jev_audit as ja  # noqa: E402


def _mem_conn():
    conn = sqlite3.connect(":memory:")
    conn.executescript(ja.SCHEMA)
    return conn


def _row(conn, news_id, bias="bull", trade_day="20261027", excess=1.0,
         hit=1, posterior_at="2026-10-27T07:10:00", companies='["600000.SH"]'):
    conn.execute(
        "INSERT INTO dec_audit(news_id, ts, created_at, trade_day, source, bias, conf,"
        " event_type, companies, model, prompt_version, ret, bench, excess, hit,"
        " posterior_at, updated_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (news_id, "2026-10-26 20:00", "2026-10-26 20:01", trade_day, "sina",
         bias, 0.9, "other", companies, "m", "v", 1.0, 0.0, excess, hit,
         posterior_at, posterior_at))
    conn.commit()


def test_upsert_signal_day_uses_created_at_when_earlier(tmp_path, monkeypatch):
    """created_at 日期早于 ts（公告占位 00:00）→ 用更早的信息可得日做基准。"""
    opens = ["20260925", "20260928", "20260929"]
    conn = _mem_conn()
    row = {"news_id": "n1", "ts": "2026-09-28 00:00", "created_at": "2026-09-27 15:45",
           "source": "sina", "bias": "bull", "conf": 0.9, "event_type": "other",
           "codes": ["600000.SH"], "model": "m", "prompt_version": "v"}
    ja.upsert_judged(conn, [row], opens)
    trade_day = conn.execute("SELECT trade_day FROM dec_audit WHERE news_id='n1'").fetchone()[0]
    conn.close()
    # 旧口径：ts 日 09-28 的“下一个交易日”= 09-29（错）；新口径：09-27 之后首个交易日 = 09-28
    assert trade_day == "20260928"


def test_upsert_resets_posterior_when_judgment_changes():
    conn = _mem_conn()
    _row(conn, "n1", bias="bull")
    row = {"news_id": "n1", "ts": "2026-10-26 20:00", "created_at": "2026-10-26 20:01",
           "source": "sina", "bias": "bear", "conf": 0.9, "event_type": "other",
           "codes": ["600000.SH"], "model": "m", "prompt_version": "v"}
    ja.upsert_judged(conn, [row], ["20261027"])
    got = conn.execute(
        "SELECT bias, posterior_at, hit FROM dec_audit WHERE news_id='n1'").fetchone()
    conn.close()
    assert got[0] == "bear"
    assert got[1] is None and got[2] is None  # 判定变化后旧后验被清空


def test_export_posterior_local_day_and_repair(tmp_path, monkeypatch):
    monkeypatch.setattr(ja, "AUDIT_DIR", tmp_path)
    conn = _mem_conn()
    _row(conn, "n1", posterior_at="2026-10-26T07:10:00")
    _row(conn, "n2", posterior_at="2026-10-27T07:10:00")
    out = ja.export_posterior_jsonl(conn, day="20261026")
    lines = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines()]
    assert [x["news_id"] for x in lines] == ["n1"]
    idx = ja.repair_posterior_exports(conn)
    conn.close()
    assert idx["total_db_posterior"] == 2
    assert idx["missing"] == 0
    assert set(idx["by_day"]) == {"2026-10-26", "2026-10-27"}
    assert (tmp_path / "posterior_20261027.jsonl").exists()


def test_build_report_coverage_and_trade_days(monkeypatch):
    conn = _mem_conn()
    _row(conn, "n1", bias="bull", trade_day="20261026", excess=1.0)
    _row(conn, "n2", bias="bear", trade_day="20261027", excess=-1.0)
    _row(conn, "n3", bias="neutral", trade_day="20261027", excess=0.2)
    monkeypatch.setattr(ja, "load_open_days", lambda: ["20261026", "20261027", "20261029"])
    report = ja.build_report(conn, 2.5)
    conn.close()
    assert report["judged_total"] == 3 and report["priced_total"] == 3
    assert report["linked_total"] == 3
    assert report["data_through"] == "20261027"
    assert report["latest_trade_day"] == "20261029"
    assert report["stale_trade_days"] == 1
    assert report["n_trade_days"] == 2
    assert report["by_trade_day"][0]["trade_day"] == "20261026"
    assert report["assessment"]["directional"]["n_days"] == 2
    assert report["assessment"]["directional"]["gate_pass"] is False


def test_assess_gate_requires_20_days_and_wilson():
    report = {
        "directional": {
            "n": 3690, "rate": 0.60, "wilson95": [0.55, 0.65], "hits": 2214,
            "baseline_all_bull": {"n": 3690, "rate": 0.54, "hits": 1993},
        },
        "by_bias": {"bull": {"n": 2000, "accuracy": 0.62},
                    "bear": {"n": 500, "accuracy": 0.55}},
        "neutral": {"n": 1000, "band_hit_rate": 0.70, "highvol_share": 0.30},
        "n_trade_days": 20,
        "by_event_type": {},
    }
    a = ja.assess_report(report)
    assert a["directional"]["gate_pass"] is True
    report["n_trade_days"] = 5
    assert ja.assess_report(report)["directional"]["gate_pass"] is False
