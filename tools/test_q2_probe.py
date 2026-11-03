# -*- coding: utf-8 -*-
"""Q2 健康探针：成功/失败不假绿。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import q2_probe as Q  # noqa: E402


def test_probe_ok(monkeypatch):
    monkeypatch.setattr(Q, "_get", lambda url, timeout=5.0: {"ok": True, "status": 200, "latency_ms": 1, "evidence": "{}"})
    r = Q.probe("acad1-legion")
    assert r["ok"] and len(r["checks"]) == 2


def test_probe_failure_not_green(monkeypatch):
    monkeypatch.setattr(Q, "_get", lambda url, timeout=5.0: {"ok": False, "status": 0, "latency_ms": 1, "error": "down"})
    r = Q.probe("data-warehouse")
    assert r["ok"] is False
    assert all(not x["ok"] for x in r["checks"])


def test_unknown_composition():
    assert Q.main(["--composition", "not-exist"]) == 2
