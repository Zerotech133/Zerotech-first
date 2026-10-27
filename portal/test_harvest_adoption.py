"""test_harvest_adoption.py — 采纳率归集器测试（C1 · 服务器侧方案）。"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harvest_adoption as ha

_MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
           "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _log_ts(minutes_ago: int) -> str:
    t = datetime.now() - timedelta(minutes=minutes_ago)
    return "%02d/%s/%04d:%02d:%02d:%02d +0800" % (
        t.day, _MONTHS[t.month - 1], t.year, t.hour, t.minute, t.second)


def test_parse_and_dedup():
    ref = '" "http://127.0.0.1:8080/os-entry" "-'
    t20, t19, t10 = _log_ts(20), _log_ts(19), _log_ts(10)
    lines = [
        f'127.0.0.1 - - [{t20}] "GET /dev HTTP/1.1" 200 1{ref}',
        f'127.0.0.1 - - [{t19}] "GET /dev HTTP/1.1" 200 1{ref}',  # 窗口内去重
        f'127.0.0.1 - - [{t10}] "GET /dev HTTP/1.1" 200 1{ref}',  # 窗口外保留
        f'127.0.0.1 - - [{t20}] "GET /intel HTTP/1.1" 200 1{ref}',  # 不同域不去重
        f'127.0.0.1 - - [{t20}] "GET /health HTTP/1.1" 200 1 "-" "-"',  # 非建议路径
        f'127.0.0.1 - - [{t20}] "GET /dev HTTP/1.1" 404 1 "-" "-"',  # 非 200
    ]
    entries = ha.parse_entries(lines, since_hours=24)
    assert len(entries) == 4
    rows = ha.dedup_window(entries, minutes=5)
    domains = sorted(r["domain"] for r in rows)
    assert domains == ["情报", "研发", "研发"]  # 20 分钟前与 10 分钟前的 /dev 都留


def test_append_ledger_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(ha, "OUT", str(tmp_path / "a.jsonl"))
    rows = [
        {
            "at": "2026-10-23T04:50:00+08:00",
            "ip": "127.0.0.1",
            "domain": "研发",
            "path": "/dev",
        }
    ]
    assert ha.append_ledger(rows) == 1
    assert ha.append_ledger(rows) == 0  # 幂等


def test_stat_empty_honest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ha, "OUT", str(tmp_path / "none.jsonl"))
    assert ha.main(["--stat"]) == 0
    assert "不造样本" in capsys.readouterr().out


def test_direct_visit_not_adoption():
    """直访（无 os-entry referer）不算采纳。"""
    line = (
        '127.0.0.1 - - [23/Oct/2026:04:50:00 +0800] "GET /dev HTTP/1.1" 200 1 "-" "-"'
    )
    assert ha.parse_entries([line], since_hours=24) == []


def test_query_decision_id_parsed():
    ref = '" "http://127.0.0.1:8080/os-entry" "-'
    t = _log_ts(5)
    line = (
        f'127.0.0.1 - - [{t}] "GET /dev?decision_id=DR-20261027-01&card_id=ZQ-20261027-01 HTTP/1.1" '
        f'200 1{ref}'
    )
    rows = ha.parse_entries([line], since_hours=24)
    assert len(rows) == 1
    assert rows[0]["domain"] == "研发"
    assert rows[0]["decision_id"] == "DR-20261027-01"
    assert rows[0]["card_id"] == "ZQ-20261027-01"


def test_query_invalid_decision_id_ignored():
    ref = '" "http://127.0.0.1:8080/os-entry" "-'
    t = _log_ts(5)
    line = (
        f'127.0.0.1 - - [{t}] "GET /dev?decision_id=BAD-ID HTTP/1.1" 200 1{ref}'
    )
    rows = ha.parse_entries([line], since_hours=24)
    assert len(rows) == 1 and "decision_id" not in rows[0]


def test_dedup_keeps_distinct_decision_ids():
    t = _log_ts(5)
    entries = [
        {"at": t, "ip": "1.1.1.1", "domain": "研发", "decision_id": "DR-1"},
        {"at": t, "ip": "1.1.1.1", "domain": "研发", "decision_id": "DR-2"},
    ]
    assert len(ha.dedup_window(entries, minutes=5)) == 2
