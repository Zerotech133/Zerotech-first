# -*- coding: utf-8 -*-
"""Q2 SLO 告警：acad1 ok；data-warehouse 未达标必须 alert。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import q2_slo_alert as A  # noqa: E402


def test_acad1_ok(monkeypatch):
    monkeypatch.setattr(A, "_get", lambda url: {"freshness_days": 0, "stale": False, "data_date": "2026-11-03",
                                                "summary": {"pool_total": 12}})
    r = A.evaluate("acad1-legion")
    assert r["ok"] is True and not r["alerts"]


def test_dw_alert_not_green(monkeypatch):
    monkeypatch.setattr(A, "_get", lambda url: {"state": "ok", "total": 314, "registered": 265, "exposed": 72})
    r = A.evaluate("data-warehouse")
    assert r["ok"] is False
    assert any("registered_ratio" in x for x in r["alerts"])


def test_probe_error_alerts(monkeypatch):
    monkeypatch.setattr(A, "_get", lambda url: {"_error": "down"})
    r = A.evaluate("acad1-legion")
    assert r["ok"] is False and r["alerts"][0].startswith("probe_failed")
