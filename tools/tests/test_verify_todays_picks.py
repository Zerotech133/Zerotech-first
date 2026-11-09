# -*- coding: utf-8 -*-
"""todays_picks 18:20 首跑验收器契约测试（纯函数，不触发任务/不写生产数据）。"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
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
NOW_AFTER = datetime(2026, 11, 5, 19, 0, 0)
NOW_BEFORE = datetime(2026, 11, 5, 18, 24, 0)


def make_picks(gen="2026-11-05 18:20:41", basis=f"{DAY} 收盘（行情湖 ts_daily 最新可得交易日）",
               total=731, t1=3, t2=44, t3=684):
    return {"generated_at": gen, "trade_basis": basis,
            "counts": {"total": total, "T1_resonance": t1,
                       "T2_high_value": t2, "T3_single": t3}}


def make_archive(gen="2026-11-05 18:20:41", basis=f"{DAY} 收盘（行情湖 ts_daily 最新可得交易日）",
                 total=731, t1=3, t2=44, t3=684):
    return {"generated_at": gen, "trade_basis": basis,
            "counts": {"total": total, "T1_resonance": t1,
                       "T2_high_value": t2, "T3_single": t3}}


def make_task(last_run="2026-11-05 18:20:31", last_result=0, state="Ready"):
    return {"last_run": last_run, "last_result": last_result, "state": state,
            "next_run": "2026-11-06 18:20:00"}


def test_pass(tmp_path):
    ap = tmp_path / "todays_20261105.json"
    ap.write_text("{}", encoding="utf-8")
    r = v.evaluate(make_picks(), make_archive(), make_task(), DAY, ap, now=NOW_AFTER)
    assert r["status"] == "PASS" and r["ok"] is True
    assert r["generated_at"] == "2026-11-05 18:20:41"
    assert r["archive"]["exists"] is True


def test_fail_wrong_day(tmp_path):
    r = v.evaluate(make_picks(gen="2026-11-04 20:27:39"), make_archive(), make_task(), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL" and any("日期" in e for e in r["errors"])


def test_fail_too_early(tmp_path):
    r = v.evaluate(make_picks(gen="2026-11-05 18:00:00"), make_archive(), make_task(), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL" and any("早于" in e for e in r["errors"])


def test_fail_basis_mismatch(tmp_path):
    r = v.evaluate(make_picks(basis="2026-11-04 收盘"), make_archive(), make_task(), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL" and any("trade_basis" in e for e in r["errors"])


def test_fail_archive_missing(tmp_path):
    r = v.evaluate(make_picks(), None, make_task(), DAY, tmp_path / "missing.json", now=NOW_AFTER)
    assert r["status"] == "FAIL" and any("归档缺失" in e for e in r["errors"])


def test_archive_early_consistent_pass_with_warning(tmp_path):
    # 11-04 现实场景：17:15 首写，20:27 主文件刷新但归档首写锁定；
    # 归档时间窗早，但内容与主文件一致 → 接受 + warning，不再永久 FAIL。
    r = v.evaluate(make_picks(), make_archive(gen="2026-11-05 17:15:01"), make_task(), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "PASS" and r["ok"] is True
    assert any("窗口前首写" in w for w in r["warnings"])


def test_fail_archive_early_inconsistent(tmp_path):
    r = v.evaluate(make_picks(), make_archive(gen="2026-11-05 17:15:01", total=0),
                   make_task(), DAY, tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL"
    assert any("counts" in e for e in r["errors"])


def test_fail_archive_counts_mismatch(tmp_path):
    r = v.evaluate(make_picks(), make_archive(t1=0), make_task(), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL"
    assert any("T1_resonance" in e for e in r["errors"])


def test_pass_with_t1_suspended_rc3(tmp_path):
    # builder 契约：rc=3 = 已落盘但 T1 停挂；验收应 PASS 并给出警告，而不是 FAIL。
    r = v.evaluate(make_picks(), make_archive(), make_task(last_result=3), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "PASS" and r["ok"] is True
    assert any("T1 停挂" in w for w in r["warnings"])


def test_fail_when_task_snapshot_error(tmp_path):
    # 任务被删/查询失败不是「尚未运行」，必须 FAIL，不能 PENDING 掩盖故障。
    r = v.evaluate(make_picks(), make_archive(), {"error": "Get-ScheduledTask: not found"}, DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL" and r["ok"] is False


def test_pending_when_task_not_run_today_before_deadline(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(last_run="2026-11-04 18:20:31"), DAY,
                   tmp_path / "x.json", now=NOW_BEFORE)
    assert r["status"] == "PENDING" and r["ok"] is False


def test_fail_when_task_never_ran_after_deadline(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(last_run="2026-11-04 18:20:31"), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL"
    assert any("当日未运行" in e for e in r["errors"])


def test_pending_when_task_running(tmp_path):
    r = v.evaluate(make_picks(), make_archive(),
                   make_task(last_run="2026-11-05 18:20:31", last_result=267009, state="Running"),
                   DAY, tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "PENDING"
    assert any("仍在运行" in w for w in r["warnings"])


def test_fail_when_task_disabled(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(state="Disabled"), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL"
    assert any("禁用" in e for e in r["errors"])


def test_fail_when_last_run_unparseable_after_deadline(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(last_run="garbage"), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL"
    assert any("不可解析" in e for e in r["errors"])


def test_fail_task_nonzero_result(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(last_result=4), DAY,
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "FAIL" and any("LastTaskResult" in e for e in r["errors"])


def test_weekend_skip(tmp_path):
    r = v.evaluate(make_picks(), make_archive(), make_task(), "2026-11-07",
                   tmp_path / "x.json", now=NOW_AFTER)
    assert r["status"] == "SKIP" and r["ok"] is True
    assert any("周末" in w for w in r["warnings"])


def test_cli_exit_code_pending(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "task_snapshot", lambda: make_task(last_run="2026-11-04 18:20:31"))
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps(make_picks()), encoding="utf-8")
    archive = tmp_path / "todays_20261105.json"
    archive.write_text(json.dumps(make_archive()), encoding="utf-8")
    out = tmp_path / "report.json"
    rc = v.main(["--date", DAY, "--picks", str(picks), "--history-dir", str(tmp_path),
                 "--json-out", str(out)],
                now=datetime(2026, 11, 5, 18, 24, 0))  # 修复：注入时钟（原依赖 datetime.now()，18:25 后跑必挂）
    assert rc == 3
    assert out.exists()
    assert "status=PENDING" in capsys.readouterr().out


def test_cli_exit_code_skip_weekend(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(v, "task_snapshot", lambda: make_task())
    picks = tmp_path / "picks.json"
    picks.write_text(json.dumps(make_picks()), encoding="utf-8")
    out = tmp_path / "report.json"
    rc = v.main(["--date", "2026-11-07", "--picks", str(picks),
                 "--history-dir", str(tmp_path), "--json-out", str(out)])
    assert rc == 0
    assert "status=SKIP" in capsys.readouterr().out
