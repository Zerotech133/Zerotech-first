# -*- coding: utf-8 -*-
"""24h 观察收口测试（watch_24h_closeout / probe closed marker）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

import watch_24h_closeout as C  # noqa: E402

UTC = timezone.utc


def _write_log(path: Path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _rows(n=6, problems=None, start=None):
    start = start or datetime(2026, 11, 4, 15, 16, 0, tzinfo=UTC)
    fn = problems if callable(problems) else (lambda _i: problems)
    out = []
    for i in range(n):
        out.append({
            "at": (start + timedelta(minutes=10 * i)).isoformat(),
            "market_closed": True,
            "watch_8789": True,
            "engine": {"exists": True, "engine_state": "running"},
            "problems": list(fn(i) or []),
            "alert_sent": False,
        })
    return out


def test_analyze_healthy_and_span():
    data = C.analyze(_rows(6), 0, log_path="x.jsonl")
    assert data["records"] == 6
    assert data["active_problems"] == []
    assert data["historical_problem_records"] == 0
    assert data["verdict"] == "healthy"
    assert data["gaps_over_15m"] == 0
    assert data["log"] == "x.jsonl"


def test_analyze_historical_false_alert_is_healthy_at_end():
    rows = _rows(6, problems=lambda i: ["WatchEngineLoop_task_not_running"] if i == 0 else None)
    data = C.analyze(rows)
    assert data["problems_records"] == 1
    assert data["historical_problem_records"] == 1
    assert data["active_problems"] == []
    assert data["verdict"] == "healthy"
    assert data["alert_sent_records"] == 0


def test_analyze_active_problem_is_degraded():
    rows = _rows(6, problems=lambda i: ["8789_watch_admin_down"] if i == 5 else None)
    data = C.analyze(rows)
    assert data["active_problems"] == ["8789_watch_admin_down"]
    assert data["historical_problem_records"] == 0
    assert data["verdict"] == "degraded"


def test_analyze_gap_over_15m():
    rows = _rows(3)
    rows[2]["at"] = (datetime(2026, 11, 4, 16, 10, tzinfo=UTC)).isoformat()
    data = C.analyze(rows)
    assert data["gaps_over_15m"] >= 1
    assert data["max_gap_minutes"] > 15
    assert data["verdict"] == "healthy"  # 断点不改变末条健康判定，但会在复盘中标出


def test_main_dry_run_writes_nothing(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    _write_log(log, _rows(6))
    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(tmp_path / "r.json"),
                 "--out-md", str(tmp_path / "r.md"), "--dry-run", "--json"])
    assert rc == 0
    assert not marker.exists()
    assert not (tmp_path / "r.json").exists()
    out = json.loads(capsys.readouterr().out)
    assert out["records"] == 6 and out["verdict"] == "healthy"


def test_main_writes_reports_marker_and_is_idempotent(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    _write_log(log, _rows(6))
    args = ["--log", str(log), "--marker", str(marker), "--out-json", str(out_json),
            "--out-md", str(out_md), "--json"]
    assert C.main(args) == 0
    assert marker.exists() and out_json.exists() and out_md.exists()
    assert "Watch 24h" in out_md.read_text(encoding="utf-8")
    assert json.loads(out_json.read_text(encoding="utf-8"))["records"] == 6
    capsys.readouterr()
    assert C.main(args) == 0
    assert "already closed" in capsys.readouterr().out


def test_empty_log_fails_without_marker(tmp_path):
    log = tmp_path / "log.jsonl"
    log.write_text("", encoding="utf-8")
    marker = tmp_path / "closed.json"
    assert C.main(["--log", str(log), "--marker", str(marker),
                   "--out-json", str(tmp_path / "r.json"),
                   "--out-md", str(tmp_path / "r.md")]) == 1
    assert not marker.exists()
