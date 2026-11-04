# -*- coding: utf-8 -*-
"""todays_picks 18:20 首跑验收器契约测试（纯函数，不触发任务/不写生产数据）。"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

VERIFY = Path(__file__).resolve().parents[1] / "scheduled" / "verify_todays_picks.py"


def _load():
    spec = importlib.util.spec_from_file_location("verify_todays_picks_under_test", VERIFY)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


v = _load()
DAY = "2026-11-05"


def make_picks(gen="2026-11-05 18:20:41", basis=f"{DAY} 收盘（行情湖 ts_daily 最新可得交易日）",
               total=731):
    return {"generated_at": gen, "trade_basis": basis,
            "counts": {"total": total, "T1_resonance": 3, "T2_high_value": 44, "T3_single": 684}}


def make_archive(gen="2026-11-05 18:20:41", basis=f"{DAY} 收盘（行情湖 ts_daily 最新可得交易日）",
                 total=731):
    return {"generated_at": gen, "trade_basis": basis,
            "counts": {"total": total}}


def make_task(last_run="2026-11-05 18:20:31", last_result=0, state="Ready"):
    return {"last_run": last_run, "last_result": last_result, "state": state,
            "next_run": "2026-11-06 18:20:00"}


def test_pass(tmp_path):
    ap = tmp_path / "todays_20261105.json"
    ap.write_text("{}", encoding="utf-8")
    r = v.evaluate(make_picks(), make_archive(), make_task(), DAY, ap)
    assert r["status"] == "PASS" and r["ok"] is True
    assert r["generated_at"] == "2026-11-05 18:20:41"
    assert r["archive"]["exists"] is True


def test_fail_wrong_day(tmp_path):
    r = v.evaluate(make_picks(gen="2026-11-04 20:27:39"), make_archive(), make_task(), DAY, tmp_path / "x.json")
    assert r["status"] == "FAIL" and any("日期" in e for e in r["errors"])


def test_fail_too_early(tmp_path):
    r = v.evaluate(make_picks(gen="2026-11-05 18:00:00"), make_archive(), make_task(), DAY, tmp_path / "x.json")
    assert r["status"] == "FAIL" and any("早于" in e for e in r["errors"])


def test_fail_basis_mismatch(tmp_path):
    r = v.evaluate(make_picks(basis="2026-11-04 收盘"), make_archive(), make_task(), DAY, tmp_path / "x.json")
    assert r["status"] == "FAIL" and any("trade_basis" in e for e in r["errors"])


def test_fail_archive_missing(tmp_path):
    r = v.evaluate(make_picks(), None, make_task(), DAY, tmp_path / "missing.json")
    assert r["status"] == "FAIL" and any("归档缺失" in e for e in r["errors"])


def test_fail_archive_too_early(tmp_path):
    r = v.evaluate(make_picks(), make_archive(gen="2026-11-05 17:15:01"), make_task(), DAY,
                   tmp_path / "x.json")
    assert r["status"] == "FAIL" and any("旧运行首写" in e for e in r["errors"])


def test_pending_when_task_not_run_today(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(last_run="2026-11-04 18:20:31"), DAY,
                   tmp_path / "x.json")
    assert r["status"] == "PENDING" and r["ok"] is False


def test_fail_task_nonzero_result(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(last_result=4), DAY, tmp_path / "x.json")
    assert r["status"] == "FAIL" and any("LastTaskResult" in e for e in r["errors"])


def test_cli_exit_code_pending(tmp_path, monkeypatch, capsys):
    import subprocess
    monkeypatch.setattr(v, "task_snapshot", lambda: make_task(last_run="2026-11-04 18:20:31"))
    picks = tmp_path / "picks.json"
    picks.write_text(__import__("json").dumps(make_picks()), encoding="utf-8")
    archive = tmp_path / "todays_20261105.json"
    archive.write_text(__import__("json").dumps(make_archive()), encoding="utf-8")
    out = tmp_path / "report.json"
    rc = v.main(["--date", DAY, "--picks", str(picks), "--history-dir", str(tmp_path),
                 "--json-out", str(out)])
    assert rc == 3
    assert out.exists()
    assert "status=PENDING" in capsys.readouterr().out
