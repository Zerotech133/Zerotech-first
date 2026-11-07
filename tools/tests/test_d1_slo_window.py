# -*- coding: utf-8 -*-
"""D1 P0-02 SLO 7d 窗口采样/报告测试（无网络、无生产写入）。"""
from __future__ import annotations

import json
import sys
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import d1_slo_window as W  # noqa: E402

REGISTRY = ROOT / "docs" / "standards" / "INTERFACE_REGISTRY_v1.yaml"
START = W.DEFAULT_WINDOW_START
END = W.DEFAULT_WINDOW_END


def _write_day(path: Path, rows):
    payload = {
        "schema": W.SCHEMA_SAMPLE,
        "window_start": START.isoformat(timespec="seconds"),
        "window_end": END.isoformat(timespec="seconds"),
        "day": "2026-11-08",
        "samples": rows,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _row(iid, ts, status=200, latency=20.0, probe="http_get", method="GET"):
    return {
        "interface_id": iid,
        "method": method,
        "path": "/x",
        "owner": "D1",
        "slo_class": "read_fast",
        "ts": ts.isoformat(timespec="seconds"),
        "probe": probe,
        "ok": probe == "http_get" and 200 <= status < 300,
        "status": status,
        "latency_ms": latency,
    }


def test_interfaces_cover_9_paths_and_10_methods():
    specs = W.resolve_interfaces(REGISTRY)
    assert len(specs) == 10
    assert {s["interface_id"] for s in specs} >= {
        "d1.research.get", "d1.decision.create", "d1.decision.list",
        "d1.meeting.get", "d1.meeting.create", "d1.handoff.create",
        "d1.handoff.list", "d1.watch_plans.list",
        "training.episode.get", "war_room.snapshot.get",
    }
    assert all(s["slo_class"] in W.load_slo_classes(REGISTRY) for s in specs)
    assert [s for s in specs if s["probe"] == "http_get"]  # 7 个只读探针
    assert len([s for s in specs if s["probe"] == "skipped_write"]) == 3


def test_sample_once_writes_raw_and_skips_write(tmp_path):
    specs = [s for s in W.resolve_interfaces(REGISTRY) if s["probe"] == "http_get"][:2]
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(W, "ACTIVE_INTERFACES", [
        (s["interface_id"], s["method"], s["path"]) for s in specs
    ])
    try:
        result = W.sample_once(tmp_path, force=True, http_get=lambda url: (200, None))
    finally:
        monkeypatch.undo()
    assert result["added"] == 2
    payload = json.loads((tmp_path / f"{W.dt.datetime.now(W.TZ).date().isoformat()}.json")
                         .read_text(encoding="utf-8"))
    assert len(payload["samples"]) == 2
    assert all(s["probe"] == "http_get" and s["status"] == 200 for s in payload["samples"])


def test_build_report_go_for_measured_reads(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "ACTIVE_INTERFACES", [
        ("d1.research.get", "GET", "/api/d1/research"),
        ("d1.decision.list", "GET", "/api/d1/decisions"),
    ])
    rows = []
    for i in range(25):
        ts = START + timedelta(minutes=5 * i)
        rows.append(_row("d1.research.get", ts, latency=100.0))
        rows.append(_row("d1.decision.list", ts, latency=20.0))
    _write_day(tmp_path / "2026-11-08.json", rows)
    report = W.build_report(tmp_path, registry_path=REGISTRY)
    assert report["verdict"] == "GO"
    assert report["coverage"] == {"measured": 2, "total": 2, "gaps": []}
    assert report["summary"]["pass"] == 2


def test_build_report_fail_on_threshold_and_gap(tmp_path, monkeypatch):
    monkeypatch.setattr(W, "ACTIVE_INTERFACES", [
        ("d1.research.get", "GET", "/api/d1/research"),
        ("d1.decision.list", "GET", "/api/d1/decisions"),
    ])
    rows = []
    for i in range(25):
        ts = START + timedelta(minutes=5 * i)
        rows.append(_row("d1.research.get", ts, latency=9999.0))
    _write_day(tmp_path / "2026-11-08.json", rows)
    report = W.build_report(tmp_path, registry_path=REGISTRY)
    assert report["verdict"] == "NO_GO"
    assert report["summary"]["fail"] == 1
    assert "d1.decision.list" in report["coverage"]["gaps"]


def test_write_interfaces_are_not_faked(tmp_path):
    specs = W.resolve_interfaces(REGISTRY)
    write_ids = [s["interface_id"] for s in specs if s["probe"] == "skipped_write"]
    rows = []
    for i in range(25):
        ts = START + timedelta(minutes=5 * i)
        for s in specs:
            if s["probe"] == "http_get":
                rows.append(_row(s["interface_id"], ts, latency=10.0))
    for iid in write_ids:
        rows.append(_row(iid, START, probe="skipped_write", status=None,
                         latency=None, method="POST"))
    _write_day(tmp_path / "2026-11-08.json", rows)
    report = W.build_report(tmp_path, registry_path=REGISTRY)
    assert report["verdict"] == "NO_GO"
    assert set(report["coverage"]["gaps"]) == set(write_ids)
    write_checks = [c for c in report["checks"] if c["interface_id"] in write_ids]
    assert all(c["verdict"] == "NOT_MEASURED" for c in write_checks)


def test_report_archive_contains_raw_and_report(tmp_path):
    _write_day(tmp_path / "2026-11-08.json", [])
    report = {
        "schema": W.SCHEMA_REPORT,
        "generated_at": "2026-11-14T00:10:00+08:00",
        "window_start": START.isoformat(timespec="seconds"),
        "window_end": END.isoformat(timespec="seconds"),
        "verdict": "NO_GO",
        "reasons": ["not measured"],
        "coverage": {"measured": 0, "total": 1, "gaps": ["x"]},
        "summary": {"pass": 0, "fail": 0, "not_measured": 1},
        "checks": [],
    }
    paths = W.write_report(report, tmp_path, archive=True)
    assert Path(paths["json"]).is_file()
    assert Path(paths["md"]).is_file()
    assert Path(paths["archive"]).is_file()
    import zipfile
    with zipfile.ZipFile(paths["archive"]) as zf:
        names = set(zf.namelist())
    assert "2026-11-08.json" in names
    assert "2026-11-14_slo_report.json" in names
    assert "2026-11-14_slo_report.md" in names


def test_sample_outside_window_requires_force(tmp_path):
    # 不依赖“当前时间是否已进入默认窗口”：显式给一个未来窗口，任何时刻都在窗口外。
    future_start = W.DEFAULT_WINDOW_END + timedelta(days=1)
    future_end = future_start + timedelta(days=1)
    with pytest.raises(RuntimeError):
        W.sample_once(
            tmp_path,
            force=False,
            window_start=future_start,
            window_end=future_end,
            http_get=lambda url: (200, None),
        )
