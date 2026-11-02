# -*- coding: utf-8 -*-
"""tdx_sync_probe 看门狗单测（全 mock/临时目录，不碰真计划任务/真数据根/真 C:\\Temp）。

覆盖（≥4 用例，实际 8 个）：
1. 全正常（18:30 后 / 交易日 / rows 有增量 / part mtime 今日 / 无锁）→ rc=0；
2. 零行假绿（rows 无增量 + part 全旧）→ rc=1，zero_row_all_key_tables + no_part_updated_since_window；
3. 锁残留（>2h 判失败；<2h 仅 WARN）；
4. 任务失败（LastTaskResult 非 0）/ 任务未跑（267011）→ rc=1；
5. 首夜前（18:30 前、267011 尚未运行）不判失败且首跑只建立基线，第二次起判零行；
6. manifest 缺失 → rc=1 fail-visible（不假绿）；
7. 非交易日（周六）rows 无增量不判失败（任务仍须今日跑过）；
8. 日志 JSONL + 基线状态落盘（内容/结构断言）。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time as dtime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "tools" / "datawarehouse"))
import tdx_sync_probe as probe  # noqa: E402

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 11, 2, 19, 5, tzinfo=TZ)  # 周一，四批窗口之后
BEFORE_WINDOW = datetime(2026, 11, 2, 11, 0, tzinfo=TZ)  # 首夜 17:35 之前
PART_MTIME = datetime(2026, 11, 2, 17, 45, tzinfo=TZ)
WINDOW_START = datetime(2026, 11, 2, 17, 30, tzinfo=TZ)


# ---- fixtures -------------------------------------------------------------------
def _write_manifest(data_root: Path, rows_map: dict, part_mtime: datetime) -> None:
    tdx = data_root / "tdx"
    tdx.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for table, rows in rows_map.items():
        folder = tdx / table
        folder.mkdir(parents=True, exist_ok=True)
        part = folder / "part_0002.parquet"
        part.write_bytes(b"x")
        ts = part_mtime.timestamp()
        os.utime(part, (ts, ts))
        manifest[table] = {"rows": rows, "part": 2, "done": True}
    (tdx / "_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def _write_baseline(path: Path, rows_map: dict, when: datetime = PART_MTIME,
                    day: str | None = None) -> None:
    """写「当日窗口前基线」状态（与 tdx_sync_probe.collect 的 state 结构一致）。"""
    payload = {
        "updated_at": when.isoformat(timespec="seconds"),
        "day_baseline": {"day": day or when.date().isoformat(),
                         "captured_at": when.isoformat(timespec="seconds"),
                         "rows": dict(rows_map)},
        "tables": {t: {"rows": rows} for t, rows in rows_map.items()},
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _tasks(last_result: int = 0, last_run: datetime | None = PART_MTIME) -> list:
    return [{
        "name": name,
        "found": True,
        "state": "Ready",
        "last_run": last_run.isoformat(timespec="seconds") if last_run else "",
        "next_run": "",
        "last_result": last_result,
    } for name in probe.TASK_NAMES]


def _baseline_rows(rows: int = 1000) -> dict:
    return {table: rows for table in probe.KEY_TABLES}


def _collect(tmp_path: Path, *, now: datetime = NOW, tasks: list | None = None,
             rows_map: dict | None = None, part_mtime: datetime = PART_MTIME,
             baseline: dict | None = None, data_root: Path | None = None,
             locks: list | None = None, trading_day: str = "auto",
             baseline_when: datetime = PART_MTIME) -> dict:
    data_root = data_root or (tmp_path / "data")
    if rows_map is not None:
        _write_manifest(data_root, rows_map, part_mtime)
    state_path = tmp_path / "state.json"
    if baseline is not None:
        _write_baseline(state_path, baseline, when=baseline_when)
    return probe.collect(
        now=now,
        tasks=tasks if tasks is not None else _tasks(),
        data_root=data_root,
        lock_paths=locks if locks is not None else [],
        state_path=state_path,
        log_path=tmp_path / "probe.log",
        trading_day=trading_day,
    )


def _codes(report: dict, severity: str = "fail") -> set:
    return {issue["code"] for issue in report["issues"] if issue["severity"] == severity}


# ---- 1. 全正常 -------------------------------------------------------------------
def test_all_normal_after_window_ok(tmp_path):
    report = _collect(
        tmp_path,
        rows_map={t: 1100 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
    )
    assert report["code"] == probe.EXIT_OK, report["issues"]
    assert not _codes(report), report["issues"]
    assert report["window"]["after_ready"] is True
    assert report["window"]["trading_day"] is True
    assert report["summary"]["rows_delta_total"] == 100 * len(probe.KEY_TABLES)
    assert report["baseline"]["updated"] is True
    assert all(t["verdict"] == "ok" for t in report["tables"]), report["tables"]
    # 基线状态落盘：当日窗口前基线不得被运行后值覆盖
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["tables"]["ts_daily"]["rows"] == 1100
    assert state["day_baseline"]["rows"]["ts_daily"] == 1000
    # 日志 JSONL
    lines = (tmp_path / "probe.log").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["code"] == probe.EXIT_OK and record["rows_delta_total"] == 700


# ---- 2. 零行假绿 -----------------------------------------------------------------
def test_zero_row_all_key_tables_fails(tmp_path):
    report = _collect(
        tmp_path,
        rows_map=_baseline_rows(1000),  # rows 与基线一致 = 零增量
        part_mtime=NOW - timedelta(days=1),  # part 也没更新
        baseline=_baseline_rows(1000),
    )
    assert report["code"] == probe.EXIT_FAIL
    fail_codes = _codes(report)
    assert "zero_row_all_key_tables" in fail_codes, fail_codes
    assert "no_part_updated_since_window" in fail_codes, fail_codes
    assert report["summary"]["rows_delta_total"] == 0


# ---- 3. 锁残留 -------------------------------------------------------------------
def test_lock_residue_stale_fails_fresh_only_warns(tmp_path):
    stale = tmp_path / "tdx_sync_core.lock"
    stale.write_text("12345 1793000000\n", encoding="ascii")
    stale_ts = (NOW - timedelta(hours=3)).timestamp()
    os.utime(stale, (stale_ts, stale_ts))
    report = _collect(
        tmp_path,
        rows_map={t: 1100 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
        locks=[stale],
    )
    assert report["code"] == probe.EXIT_FAIL
    assert "stale_lock_residue" in _codes(report)
    assert report["lock"][0]["stale"] is True
    assert report["lock"][0]["age_minutes"] >= 179

    fresh = tmp_path / "fresh.lock"
    fresh.write_text("999 1793000000\n", encoding="ascii")
    fresh_ts = (NOW - timedelta(minutes=5)).timestamp()
    os.utime(fresh, (fresh_ts, fresh_ts))
    report_ok = _collect(
        tmp_path,
        rows_map={t: 1100 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
        locks=[fresh],
    )
    assert report_ok["code"] == probe.EXIT_OK, report_ok["issues"]
    assert "lock_recent" in _codes(report_ok, "warn")


# ---- 4. 任务失败 / 未跑 ----------------------------------------------------------
def test_task_failed_after_window_fails(tmp_path):
    tasks = _tasks(last_result=1)
    report = _collect(
        tmp_path,
        tasks=tasks,
        rows_map={t: 1100 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
    )
    assert report["code"] == probe.EXIT_FAIL
    assert "task_failed" in _codes(report)
    assert report["summary"]["tasks_checked"] == len(probe.TASK_NAMES)


def test_task_never_run_after_window_fails(tmp_path):
    report = _collect(
        tmp_path,
        tasks=_tasks(last_result=probe.NEVER_RUN_RESULT, last_run=None),
        rows_map={t: 1100 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
    )
    assert report["code"] == probe.EXIT_FAIL
    assert "task_never_run" in _codes(report)


def test_task_not_run_today_after_window_fails(tmp_path):
    report = _collect(
        tmp_path,
        tasks=_tasks(last_run=datetime(2026, 11, 1, 17, 45, tzinfo=TZ)),
        rows_map={t: 1100 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
    )
    assert report["code"] == probe.EXIT_FAIL
    assert "task_not_run_today" in _codes(report)


# ---- 5. 首夜前：不误报 + 基线生命周期 --------------------------------------------
def test_before_window_never_run_ok_then_baseline_enforced(tmp_path):
    tasks = _tasks(last_result=probe.NEVER_RUN_RESULT, last_run=None)
    first = _collect(
        tmp_path,
        now=BEFORE_WINDOW,
        tasks=tasks,
        rows_map=_baseline_rows(1000),
        part_mtime=BEFORE_WINDOW - timedelta(days=1),
        baseline=None,  # 首次运行无基线
    )
    assert first["code"] == probe.EXIT_OK, first["issues"]  # 17:35 前不判失败
    assert first["window"]["after_ready"] is False
    assert first["baseline"]["established_before_run"] is False
    assert first["baseline"]["updated"] is True
    assert all(t["verdict"] == "awaiting_first_run" for t in first["tasks"])

    # 首跑建立基线后，盘中仍不判零行（未到窗口）；次日窗口后同样 rows → 零行假绿
    second = _collect(
        tmp_path,
        now=NOW,
        tasks=_tasks(),  # 今日四批均 0
        rows_map=_baseline_rows(1000),
        part_mtime=BEFORE_WINDOW - timedelta(days=1),
        baseline=None,  # 不重写基线 → 复用第一次落盘的状态
    )
    assert second["baseline"]["established_before_run"] is True
    assert second["code"] == probe.EXIT_FAIL
    assert "zero_row_all_key_tables" in _codes(second)


# ---- 6. manifest 缺失 fail-visible ----------------------------------------------
def test_manifest_missing_fail_visible(tmp_path):
    report = _collect(
        tmp_path,
        rows_map=None,  # 不生成 data/tdx/_manifest.json
        data_root=tmp_path / "empty-data",
        baseline=_baseline_rows(1000),
    )
    assert report["code"] == probe.EXIT_FAIL
    assert "manifest_unreadable" in _codes(report)
    assert report["diagnostics"], "异常必须有 diagnostics 证据"


# ---- 7. 非交易日不判零行 ---------------------------------------------------------
def test_weekend_after_window_no_row_failure(tmp_path):
    saturday = datetime(2026, 11, 7, 19, 5, tzinfo=TZ)
    report = _collect(
        tmp_path,
        now=saturday,
        tasks=_tasks(last_run=datetime(2026, 11, 7, 17, 45, tzinfo=TZ)),
        rows_map={t: 1000 for t in probe.KEY_TABLES},
        baseline=_baseline_rows(1000),
        part_mtime=datetime(2026, 11, 6, 17, 45, tzinfo=TZ),
        baseline_when=datetime(2026, 11, 7, 11, 0, tzinfo=TZ),
    )
    assert report["code"] == probe.EXIT_OK, report["issues"]
    assert report["window"]["trading_day"] is False
    assert all(t["verdict"] == "non_trading_day" for t in report["tables"])


# ---- 8. 每小时运行不因「与上一小时零增量」误报（基线=当日窗口前）----------------
def test_hourly_after_sync_no_false_alarm(tmp_path):
    rows = {t: 1100 for t in probe.KEY_TABLES}
    first = _collect(tmp_path, now=NOW, rows_map=rows, baseline=_baseline_rows(1000))
    assert first["code"] == probe.EXIT_OK, first["issues"]
    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state["day_baseline"]["rows"]["ts_daily"] == 1000, "运行后基线被覆盖"

    # 20:05 再跑：rows 相对上一小时零增量，但相对当日窗口前基线仍有增量 → 不得误报
    second = probe.collect(
        now=NOW + timedelta(hours=1),
        tasks=_tasks(last_run=NOW + timedelta(hours=1)),
        data_root=tmp_path / "data",
        lock_paths=[],
        state_path=tmp_path / "state.json",
        log_path=tmp_path / "probe.log",
    )
    assert second["code"] == probe.EXIT_OK, second["issues"]
    assert not _codes(second), second["issues"]
    state2 = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert state2["day_baseline"]["rows"]["ts_daily"] == 1000


# ---- 9. CLI JSON 输出契约（--now 演练，不写状态）--------------------------------
def test_cli_json_dry_run(tmp_path, capsys):
    data_root = tmp_path / "data"
    _write_manifest(data_root, {t: 1000 for t in probe.KEY_TABLES},
                    datetime(2026, 11, 6, 17, 45, tzinfo=TZ))
    tasks_json = tmp_path / "tasks.json"
    tasks_json.write_text(json.dumps(
        _tasks(last_run=datetime(2026, 11, 7, 17, 45, tzinfo=TZ)), ensure_ascii=False),
        encoding="utf-8")
    rc = probe.main([
        "--json", "--now", "2026-11-07T19:05:00+08:00",
        "--data-root", str(data_root),
        "--tasks-json", str(tasks_json),
        "--state", str(tmp_path / "state.json"),
        "--log", str(tmp_path / "probe.log"),
        "--no-state",
    ])
    assert rc == probe.EXIT_OK
    report = json.loads(capsys.readouterr().out)
    assert report["tool"] == "tdx_sync_probe"
    assert report["window"]["trading_day"] is False
    assert not (tmp_path / "state.json").exists(), "--no-state 不得写状态文件"
