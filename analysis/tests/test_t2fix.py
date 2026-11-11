# -*- coding: utf-8 -*-
"""T2FIX 回归测试：run_id 冲突幂等 + 盘后批启动迟到告警。

背景（2026-11-07 T2 盘后核对发现）：
1) 11-02 605258 补跑失败 `sqlite3.IntegrityError: UNIQUE constraint failed: runs.run_id`；
2) 11-03/11-06 盘后批非常规启动（迟到），但当日无任何告警。
"""
import sqlite3
import time

import pytest

from analysis import batch, config, db


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "analysis.db"))
    return tmp_path / "analysis.db"


def test_new_run_unique_under_same_second(monkeypatch, tmp_db):
    """同一秒内连续登记两条 run 不得撞主键（原缺陷）。"""
    monkeypatch.setattr(db.time, "strftime",
                        lambda fmt, *a: "20261102-154039" if fmt == "%H%M%S" else "2026-11-02 15:40:39")
    rid1 = db.new_run("605258.SH", "协和电子", "2026-11-02")
    rid2 = db.new_run("605258.SH", "协和电子", "2026-11-02")
    assert rid1 != rid2
    assert rid1.startswith("605258SH-20261102-")
    with sqlite3.connect(str(tmp_db)) as c:
        rows = c.execute("SELECT run_id FROM runs ORDER BY rowid").fetchall()
    assert sorted([rid1, rid2]) == sorted([r[0] for r in rows])


def test_new_run_normal_format(tmp_db):
    rid = db.new_run("600337.SH", "ST美克", "2026-11-06")
    assert rid.startswith("600337SH-20261106-")
    assert len(rid) >= len("600337SH-20261106-000000")


def _ts(y, mo, d, h, mi, s=0):
    return time.localtime(time.mktime((y, mo, d, h, mi, s, 0, 0, -1)))


def test_late_alert_check_trading_day_late():
    # 2026-11-06 周五 16:48：T2 实测的迟到场景
    payload = batch.late_alert_check(now=_ts(2026, 11, 6, 16, 48), threshold_min=8)
    assert payload is not None
    assert payload["lag_min"] == pytest.approx(68.0, abs=0.1)
    assert payload["expected"] == batch.EXPECTED_START_HHMM


def test_late_alert_check_within_threshold():
    # 2026-11-06 15:45：迟到 5 分钟 < 8，不告警
    assert batch.late_alert_check(now=_ts(2026, 11, 6, 15, 45), threshold_min=8) is None


def test_late_alert_check_weekend_silent():
    # 2026-11-07 周六：即使时间晚也不告警
    assert batch.late_alert_check(now=_ts(2026, 11, 7, 16, 30), threshold_min=8) is None


def test_late_alert_check_before_expected():
    assert batch.late_alert_check(now=_ts(2026, 11, 6, 15, 20), threshold_min=8) is None


def test_send_late_alert_dry_run():
    payload = batch.late_alert_check(now=_ts(2026, 11, 6, 16, 48), threshold_min=8)
    out = batch.send_late_alert(payload, dry_run=True)
    assert out["ok"] is True and out["dry_run"] is True
    assert "盘后批启动迟到" in out["text"] and "延迟" in out["text"]


def test_main_receipt_records_late_alert(monkeypatch, tmp_path):
    """main() 回执必须记录 started 与 late_alert（供 T2 事后核对）。"""
    monkeypatch.setattr(config, "WORK", str(tmp_path / "work"))
    monkeypatch.setattr(batch, "load_watchlist", lambda p=None: ["600337.SH"])
    monkeypatch.setattr(batch, "run_one", lambda *a, **k: {"ok": True, "rating": "观察", "elapsed_s": 0.1})
    monkeypatch.setattr(batch, "late_alert_check", lambda threshold_min=None: {
        "date": "2026-11-06", "expected": "15:40", "actual": "16:48:00",
        "lag_min": 68.0, "threshold_min": 8})
    monkeypatch.setattr(batch, "send_late_alert", lambda payload, dry_run=False: {"ok": True, "why": "dry"})
    real_strftime = time.strftime
    monkeypatch.setattr(batch.time, "strftime",
                        lambda fmt, *a: "2026-11-06 16:48:02" if fmt == "%F %T" else real_strftime(fmt, *a))
    argv = ["batch", "--codes", "600337.SH", "--late-alert"]
    monkeypatch.setattr(batch.sys, "argv", argv)
    rc = batch.main()
    assert rc == 0
    import json
    receipt = json.loads((tmp_path / "work" / "batch" / time.strftime("%Y-%m-%d") / "receipt.json").read_text("utf-8"))
    assert receipt["started"].startswith("2026-11-06")
    assert receipt["late_alert"]["ok"] is True
    assert receipt["late_alert"]["payload"]["lag_min"] == 68.0
