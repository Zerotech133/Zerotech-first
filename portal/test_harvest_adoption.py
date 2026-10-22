"""test_harvest_adoption.py — 采纳率归集器测试（C1 · 服务器侧方案）。"""
from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import harvest_adoption as ha


def test_parse_and_dedup():
    ref = '" "http://127.0.0.1:8080/os-entry" "-'
    lines = [
        f'127.0.0.1 - - [23/Oct/2026:04:50:00 +0800] "GET /dev HTTP/1.1" 200 1{ref}',
        f'127.0.0.1 - - [23/Oct/2026:04:51:00 +0800] "GET /dev HTTP/1.1" 200 1{ref}',   # 窗口内去重
        f'127.0.0.1 - - [23/Oct/2026:04:58:00 +0800] "GET /dev HTTP/1.1" 200 1{ref}',   # 窗口外保留
        f'127.0.0.1 - - [23/Oct/2026:04:50:00 +0800] "GET /intel HTTP/1.1" 200 1{ref}', # 不同域不去重
        '127.0.0.1 - - [23/Oct/2026:04:50:00 +0800] "GET /health HTTP/1.1" 200 1 "-" "-"',  # 非建议路径
        '127.0.0.1 - - [23/Oct/2026:04:50:00 +0800] "GET /dev HTTP/1.1" 404 1 "-" "-"',     # 非 200
    ]
    entries = ha.parse_entries(lines, since_hours=24)
    assert len(entries) == 4
    rows = ha.dedup_window(entries, minutes=5)
    domains = sorted(r["domain"] for r in rows)
    assert domains == ["情报", "研发", "研发"]  # 04:50 与 04:58 的 /dev 都留


def test_append_ledger_dedup(tmp_path, monkeypatch):
    monkeypatch.setattr(ha, "OUT", str(tmp_path / "a.jsonl"))
    rows = [{"at": "2026-10-23T04:50:00+08:00", "ip": "127.0.0.1", "domain": "研发", "path": "/dev"}]
    assert ha.append_ledger(rows) == 1
    assert ha.append_ledger(rows) == 0  # 幂等


def test_stat_empty_honest(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(ha, "OUT", str(tmp_path / "none.jsonl"))
    assert ha.main(["--stat"]) == 0
    assert "不造样本" in capsys.readouterr().out


def test_direct_visit_not_adoption():
    """直访（无 os-entry referer）不算采纳。"""
    line = '127.0.0.1 - - [23/Oct/2026:04:50:00 +0800] "GET /dev HTTP/1.1" 200 1 "-" "-"'
    assert ha.parse_entries([line], since_hours=24) == []
