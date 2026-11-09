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
import watch_24h_probe as P  # noqa: E402

UTC = timezone.utc


def _write_log(path: Path, rows):
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
                    encoding="utf-8")


def _rows(n=6, problems=None, start=None, step_minutes=10):
    start = start or datetime(2026, 11, 4, 15, 16, 0, tzinfo=UTC)
    fn = problems if callable(problems) else (lambda _i: problems)
    out = []
    for i in range(n):
        out.append({
            "at": (start + timedelta(minutes=step_minutes * i)).isoformat(),
            "market_closed": True,
            "watch_8789": True,
            "engine": {"exists": True, "engine_state": "running"},
            "problems": list(fn(i) or []),
            "alert_sent": False,
        })
    return out


def _full_rows(problems=None):
    """144 条记录、首末覆盖恰好 24h（10min 名义采样的真实抖动由 1440/143 表达）。"""
    return _rows(144, problems=problems, step_minutes=1440.0 / 143.0)


def test_analyze_six_rows_incomplete_not_healthy():
    data = C.analyze(_rows(6), 0, log_path="x.jsonl")
    assert data["records"] == 6
    assert data["window_complete"] is False
    assert data["verdict"] == "incomplete"
    assert data["log"] == "x.jsonl"
    assert data["completeness"]["span_ok"] is False
    assert data["completeness"]["records_ok"] is False


def test_analyze_full_24h_window_healthy():
    data = C.analyze(_full_rows())
    assert data["records"] == 144
    assert data["span_hours"] >= C.MIN_SPAN_HOURS
    assert data["window_complete"] is True
    assert data["completeness"]["records_ok"] is True
    assert data["completeness"]["span_ok"] is True
    assert data["verdict"] == "healthy"
    assert data["active_problems"] == []


def test_analyze_120_records_short_span_incomplete():
    data = C.analyze(_rows(120, step_minutes=10))
    assert data["records"] == 120
    assert data["span_hours"] < 21.6
    assert data["completeness"]["span_ok"] is False
    assert data["verdict"] == "incomplete"


def test_analyze_120_records_long_span_still_incomplete_by_records():
    """覆盖够长但记录数不足（<130）也不能判完整。"""
    data = C.analyze(_rows(120, step_minutes=12))
    assert data["span_hours"] >= 21.6
    assert data["completeness"]["span_ok"] is True
    assert data["completeness"]["records_ok"] is False
    assert data["window_complete"] is False
    assert data["verdict"] == "incomplete"


def test_analyze_historical_false_alert_is_healthy_at_end():
    rows = _full_rows(problems=lambda i: ["WatchEngineLoop_task_not_running"] if i == 0 else None)
    data = C.analyze(rows)
    assert data["problems_records"] == 1
    assert data["historical_problem_records"] == 1
    assert data["active_problems"] == []
    assert data["verdict"] == "healthy"
    assert data["alert_sent_records"] == 0


def test_analyze_active_problem_is_degraded():
    rows = _full_rows(problems=lambda i: ["8789_watch_admin_down"] if i == 143 else None)
    data = C.analyze(rows)
    assert data["window_complete"] is True
    assert data["active_problems"] == ["8789_watch_admin_down"]
    assert data["historical_problem_records"] == 0
    assert data["verdict"] == "degraded"


def test_analyze_gap_over_15m_stays_informational():
    rows = _full_rows()
    for i in range(100, len(rows)):
        rows[i]["at"] = (datetime.fromisoformat(rows[i]["at"]) + timedelta(minutes=20)).isoformat()
    data = C.analyze(rows)
    assert data["gaps_over_15m"] >= 1
    assert data["max_gap_minutes"] > 15
    # gaps 只作复盘信息，不影响健康判定
    assert data["window_complete"] is True
    assert data["verdict"] == "healthy"


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
    assert not (tmp_path / "r.md").exists()
    out = json.loads(capsys.readouterr().out)
    assert out["records"] == 6
    assert out["verdict"] == "incomplete"
    assert out["closable"] is False
    assert out["marker_written"] is False


def test_main_incomplete_does_not_write_marker(tmp_path):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    out_json = tmp_path / "r.json"
    _write_log(log, _rows(6))
    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(out_json), "--out-md", str(tmp_path / "r.md")])
    assert rc == 2
    assert not marker.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["verdict"] == "incomplete"
    assert data["window_complete"] is False
    assert data["marker_written"] is False


def test_main_force_without_allow_early_does_not_close(tmp_path):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    _write_log(log, _rows(6))
    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(tmp_path / "r.json"),
                 "--out-md", str(tmp_path / "r.md"), "--force"])
    assert rc == 2
    assert not marker.exists()


def test_main_allow_early_without_force_does_not_close(tmp_path):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    _write_log(log, _rows(6))
    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(tmp_path / "r.json"),
                 "--out-md", str(tmp_path / "r.md"), "--allow-early"])
    assert rc == 2
    assert not marker.exists()


def test_main_force_allow_early_closes_explicitly(tmp_path):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    out_md = tmp_path / "r.md"
    _write_log(log, _rows(6))
    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(tmp_path / "r.json"), "--out-md", str(out_md),
                 "--force", "--allow-early"])
    assert rc == 0
    assert marker.exists()
    data = json.loads(marker.read_text(encoding="utf-8"))
    assert data["early_close"] is True
    assert data["marker_written"] is True
    assert data["verdict"] == "incomplete"
    assert "人工提前关闭" in out_md.read_text(encoding="utf-8")


def test_main_degraded_complete_does_not_close(tmp_path):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    out_json = tmp_path / "r.json"
    _write_log(log, _full_rows(problems=lambda i: ["8789_watch_admin_down"] if i == 143 else None))
    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(out_json), "--out-md", str(tmp_path / "r.md")])
    assert rc == 2
    assert not marker.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["verdict"] == "degraded"
    assert data["window_complete"] is True
    assert data["marker_written"] is False


def test_main_healthy_full_window_writes_marker_and_idempotent(tmp_path, capsys):
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    out_json, out_md = tmp_path / "r.json", tmp_path / "r.md"
    _write_log(log, _full_rows())
    args = ["--log", str(log), "--marker", str(marker), "--out-json", str(out_json),
            "--out-md", str(out_md), "--json"]
    assert C.main(args) == 0
    assert marker.exists() and out_json.exists() and out_md.exists()
    assert "Watch 24h" in out_md.read_text(encoding="utf-8")
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert data["records"] == 144
    assert data["verdict"] == "healthy"
    assert data["marker_written"] is True
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


def test_incomplete_closeout_probe_keeps_recording_then_marker_stops(tmp_path, monkeypatch):
    """AUD-01 联动：窗口不完整 -> closeout 不写 marker -> probe 继续采集。"""
    log = tmp_path / "log.jsonl"
    marker = tmp_path / "closed.json"
    _write_log(log, _rows(6))

    rc = C.main(["--log", str(log), "--marker", str(marker),
                 "--out-json", str(tmp_path / "r.json"),
                 "--out-md", str(tmp_path / "r.md")])
    assert rc == 2
    assert not marker.exists()

    monkeypatch.setattr(P, "CLOSED", marker)
    monkeypatch.setattr(P, "LOG", log)
    monkeypatch.setattr(P, "ALERT_STATE", tmp_path / "alert.json")
    monkeypatch.setattr(P, "_http_ok", lambda url: True)
    monkeypatch.setattr(P, "_loop_process_running", lambda: True)
    monkeypatch.setattr(P, "_engine", lambda: {"exists": True, "engine_state": "running", "age_s": 0})
    monkeypatch.setattr(P, "_market_closed", lambda now=None: True)

    before = len(log.read_text(encoding="utf-8").splitlines())
    assert P.main(["--no-alert", "--json"]) == 0
    after = len(log.read_text(encoding="utf-8").splitlines())
    assert after == before + 1  # 不完整关闭后 probe 仍记录

    marker.write_text("{}", encoding="utf-8")  # 只有显式关闭后才出现 marker
    assert P.main(["--no-alert", "--json"]) == 0
    assert len(log.read_text(encoding="utf-8").splitlines()) == after  # marker 后 no-op
