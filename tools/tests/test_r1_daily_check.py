# -*- coding: utf-8 -*-
"""r1_daily_check：R1 恢复只读检查器测试（全离线 mock，不启动 R1/不改计划任务）。

覆盖：正常 pass、LastTaskResult 非 0、缺任务、查询不可用、任务禁用、动作口径不符、
日志 rc 非 0 / TIMEOUT、日志/台账过期、台账缺失、对账 FAIL、NextRunTime 过期/N/A/时刻不符、
中英文表头解析、时区无关的时间解析、真实路径定位、markdown/json 落盘、CLI 退出码 0/1/2。
"""
import datetime as dt
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "r1_daily_check.py"
REPO = TOOL.parents[1]
TASK_ACTION = ('C:\\项目\\py312\\python.exe -X utf8 '
               '"C:\\aios\\tools\\hk_r1_daily.py" --append --json')


def _load():
    spec = importlib.util.spec_from_file_location("r1_daily_check_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _set_mtime(path: Path, moment: dt.datetime) -> None:
    ts = moment.timestamp()
    os.utime(path, (ts, ts))


def _csv_text(last_run: str, last_result: str, next_run: str, *, status: str = "Ready",
              scheduled: str = "Enabled", action: str = TASK_ACTION,
              task_name: str = "\\HK_R1_Daily") -> str:
    header = ["HostName", "TaskName", "Next Run Time", "Status", "Logon Mode",
              "Last Run Time", "Last Result", "Author", "Task To Run", "Start In",
              "Comment", "Scheduled Task State", "Run As User"]
    row = ["DESKTOP-ARF447U", task_name, next_run, status, "Interactive only",
           last_run, last_result, "N/A", action, "C:\\aios", "N/A", scheduled, "t"]
    buffer = io.StringIO()
    writer = csv_writer(buffer)
    writer.writerow(header)
    writer.writerow(row)
    return buffer.getvalue()


def csv_writer(buffer):
    import csv
    return csv.writer(buffer, lineterminator="\n")


def _log_text(rcs=(0, 0, 0, 0), reconcile: str = "OK", timeout: bool = False) -> str:
    commands = ["quant_backtest/export_us_hk.py --market HK",
                "k_stock/paper/hk_market.py calendar",
                "k_stock/paper/hk_market.py run",
                "k_stock/paper/hk_market.py status"]
    lines = []
    for command, rc in zip(commands, rcs):
        lines.append("$ C:\\项目\\py312\\python.exe -X utf8 %s" % command)
        lines.append("[rc=%s]" % rc)
        lines.append("输出摘要 ...")
    if timeout:
        lines.append("[TIMEOUT >600s]")
    if reconcile:
        lines.append("现金 635,351.27 HKD | 持仓市值 358,838.35 | 总资产 994,189.62 "
                     "| 仓位 36.1%% | 对账 %s" % reconcile)
    return "\n".join(lines) + "\n"


def _ledger_text(rows=(("2026-10-30", "20261029", "OK"),)) -> str:
    lines = [
        "# HK R1 影子账本 · 两周观察台账 v1",
        "",
        "| 日期 | 湖最新 | 成交笔数 | 费用合计 | 现金 | 持仓市值 | 总资产 | 仓位 | 对账 | 备注/异常 |",
        "|---|---|---:|---:|---:|---:|---:|---|---|---|",
    ]
    for date, lake, reconcile in rows:
        lines.append("| %s | %s | 0 | 0.00 | 635,351.27 | 358,838.35 | 994,189.62 | 36.1%% | %s | 自动跑批 |"
                     % (date, lake, reconcile))
    return "\n".join(lines) + "\n"


def _make_env(tmp_path: Path, *, now: dt.datetime | None = None,
              last_result: str = "0", last_run_dt: dt.datetime | None = None,
              next_run_dt: dt.datetime | None = None, last_run_raw: str | None = None,
              next_run_raw: str | None = None, log_stamp: str | None = None,
              ledger_date: str | None = None, ledger_reconcile: str = "OK",
              rcs=(0, 0, 0, 0), timeout: bool = False, scheduled: str = "Enabled",
              action: str = TASK_ACTION, status: str = "Ready",
              log_age_hours: float = 0.1, ledger_age_hours: float = 0.1) -> dict:
    """构造一套 R1 只读输入（时间锚定 18:30，时区无关）。"""
    now = (now or dt.datetime.now().astimezone()).replace(microsecond=0)
    anchor = now.replace(hour=18, minute=30, second=0, microsecond=0)
    if anchor <= now:
        anchor += dt.timedelta(days=1)
    last_run = last_run_dt if last_run_dt is not None else anchor - dt.timedelta(days=1)
    next_run = next_run_dt if next_run_dt is not None else anchor
    last_run_text = last_run_raw if last_run_raw is not None else last_run.strftime("%Y/%m/%d %H:%M:%S")
    next_run_text = next_run_raw if next_run_raw is not None else next_run.strftime("%Y/%m/%d %H:%M:%S")
    log_stamp = log_stamp or last_run.strftime("%Y%m%d")
    ledger_date = ledger_date or last_run.strftime("%Y-%m-%d")
    base = tmp_path / ("r1_%d" % (abs(hash((str(now), last_result, last_run_text))) % 1000000))
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = _write(logs / ("hk_r1_%s_%s.log" % (log_stamp, last_run.strftime("%H%M%S"))),
                      _log_text(rcs=rcs, reconcile=ledger_reconcile, timeout=timeout))
    _set_mtime(log_path, now - dt.timedelta(hours=log_age_hours))
    ledger_path = _write(base / "hk_r1_observation_v1.md",
                         _ledger_text(rows=((ledger_date, "20261029", ledger_reconcile),)))
    _set_mtime(ledger_path, now - dt.timedelta(hours=ledger_age_hours))
    task_csv = _write(base / "task.csv",
                      _csv_text(last_run_text, last_result, next_run_text,
                                status=status, scheduled=scheduled, action=action))
    return {"base": base, "logs": logs, "log": log_path, "ledger": ledger_path,
            "task_csv": task_csv, "now": now, "anchor": anchor, "last_run": last_run}


@pytest.fixture()
def env(tmp_path):
    """全绿基线：LastTaskResult=0、日志 rc=0、台账对账 OK、NextRunTime=18:30 将来。"""
    return _make_env(tmp_path)


def _run(mod, env, **over):
    kwargs = {"task_query": mod.query_task(csv_path=env["task_csv"]), "now": env["now"],
              "ledger": env["ledger"], "logs_dir": env["logs"],
              "max_age_hours": 24.0, "expect_time": "18:30"}
    kwargs.update(over)
    return mod.check_r1(**kwargs)


def _rows(report):
    return {item["id"]: item for item in report["criteria"]}


# ---------------------------------------------------------------------------
# 正常 pass
# ---------------------------------------------------------------------------
def test_all_pass(env):
    mod = _load()
    report = _run(mod, env)
    assert report["verdict"] == "pass" and report["passed"] is True
    assert report["gaps"] == []
    rows = _rows(report)
    for cid in ("task_exists", "task_enabled", "action_matches", "last_result",
                "last_run_freshness", "next_run", "next_run_expect_time",
                "log_present", "log_rc", "log_freshness", "log_matches_last_run",
                "log_reconcile", "ledger_present", "ledger_freshness",
                "ledger_reconcile", "ledger_row_for_last_run"):
        assert cid in rows, "缺判据 %s" % cid
        assert rows[cid]["status"] == "pass", (cid, rows[cid]["detail"])
    assert report["task"]["last_result"] == 0
    assert "成功" in report["task"]["last_result_note"]
    assert report["log"]["latest"]["rcs"] == [0, 0, 0, 0]
    assert report["ledger"]["latest"]["reconcile"] == "OK"
    assert report["counts"]["fail"] == 0 and report["counts"]["unknown"] == 0


def test_last_result_nonzero_only_gap(env):
    """LastResult=1（R1 上一轮失败）→ 唯一 fail 判据；其余证据仍 pass。"""
    mod = _load()
    modified = _make_env(Path(env["base"]).parent, last_result="1")
    report = _run(mod, modified)
    assert report["verdict"] == "fail"
    rows = _rows(report)
    assert rows["last_result"]["status"] == "fail"
    assert "LastTaskResult=1" in rows["last_result"]["detail"]
    assert [gap["id"] for gap in report["gaps"]] == ["last_result"]


def test_missing_task_fail():
    mod = _load()
    query = mod.parse_task_query(1, "", "ERROR: The system cannot find the file specified.")
    assert query["ok"] is True and query["found"] is False
    report = mod.check_r1(task_query=query, now=dt.datetime.now().astimezone())
    assert report["verdict"] == "fail"
    assert _rows(report)["task_exists"]["status"] == "fail"
    assert "不存在" in _rows(report)["task_exists"]["detail"]


def test_query_unavailable_unknown():
    mod = _load()
    query = {"ok": False, "found": False, "task": {}, "rc": None,
             "error": "非 Windows 环境：schtasks 不可用", "mode": "schtasks"}
    report = mod.check_r1(task_query=query, now=dt.datetime.now().astimezone(),
                          ledger="__missing__", logs_dir="__missing__")
    assert report["verdict"] == "unknown"
    assert _rows(report)["task_exists"]["status"] == "unknown"


# ---------------------------------------------------------------------------
# 任务属性异常
# ---------------------------------------------------------------------------
def test_task_disabled_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, scheduled="Disabled"))
    assert report["verdict"] == "fail"
    assert _rows(report)["task_enabled"]["status"] == "fail"


def test_action_mismatch_fail(tmp_path):
    mod = _load()
    bad_action = TASK_ACTION.replace(" --append", "")  # 缺少 --append → 不会写台账
    report = _run(mod, _make_env(tmp_path, action=bad_action))
    rows = _rows(report)
    assert rows["action_matches"]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_last_run_stale_fail(tmp_path):
    mod = _load()
    now = dt.datetime.now().astimezone().replace(microsecond=0)
    report = _run(mod, _make_env(tmp_path, now=now,
                                 last_run_dt=now - dt.timedelta(hours=48),
                                 log_stamp=(now - dt.timedelta(hours=48)).strftime("%Y%m%d"),
                                 ledger_date=(now - dt.timedelta(hours=48)).strftime("%Y-%m-%d")))
    rows = _rows(report)
    assert rows["last_run_freshness"]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_next_run_past_fail(tmp_path):
    mod = _load()
    now = dt.datetime.now().astimezone().replace(microsecond=0)
    report = _run(mod, _make_env(tmp_path, now=now,
                                 next_run_dt=now - dt.timedelta(minutes=5)))
    assert _rows(report)["next_run"]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_next_run_na_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, next_run_raw="N/A"))
    assert _rows(report)["next_run"]["status"] == "fail"
    assert "无后续运行计划" in _rows(report)["next_run"]["detail"]


def test_next_run_wrong_time_fail(tmp_path):
    mod = _load()
    now = dt.datetime.now().astimezone().replace(microsecond=0)
    anchor = now.replace(hour=18, minute=30, second=0, microsecond=0)
    if anchor <= now:
        anchor += dt.timedelta(days=1)
    report = _run(mod, _make_env(tmp_path, now=now,
                                 next_run_dt=anchor + dt.timedelta(hours=1)))
    assert _rows(report)["next_run_expect_time"]["status"] == "fail"
    assert report["verdict"] == "fail"


# ---------------------------------------------------------------------------
# 日志异常
# ---------------------------------------------------------------------------
def test_log_rc_nonzero_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, rcs=(0, 1, 0, 0)))
    rows = _rows(report)
    assert rows["log_rc"]["status"] == "fail"
    assert "[1]" in rows["log_rc"]["detail"]
    assert report["verdict"] == "fail"


def test_log_timeout_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, timeout=True))
    rows = _rows(report)
    assert rows["log_rc"]["status"] == "fail"
    assert "TIMEOUT" in rows["log_rc"]["detail"]


def test_log_stale_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, log_age_hours=30))
    assert _rows(report)["log_freshness"]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_logs_missing_unknown(tmp_path, env):
    mod = _load()
    report = _run(mod, env, logs_dir=tmp_path / "no_such_logs")
    rows = _rows(report)
    assert rows["log_present"]["status"] == "unknown"
    assert rows["log_rc"]["status"] == "unknown"
    assert report["verdict"] == "unknown"
    # 其它必需判据仍 pass（不因单点缺日志伪造 fail）
    assert rows["task_exists"]["status"] == "pass"
    assert rows["last_result"]["status"] == "pass"


def test_log_nonzero_rc_parse():
    mod = _load()
    parsed = mod.parse_log_text(_log_text(rcs=(0, 0, 1, 0), reconcile="FAIL"))
    assert parsed["rcs"] == [0, 0, 1, 0]
    assert parsed["rc_nonzero"] == [1]
    assert parsed["reconcile"] == "FAIL"
    assert mod.parse_log_text("[TIMEOUT >600s]")["timeouts"] == 1


# ---------------------------------------------------------------------------
# 台账异常
# ---------------------------------------------------------------------------
def test_ledger_missing_fail(tmp_path, env):
    mod = _load()
    report = _run(mod, env, ledger=tmp_path / "no_such_ledger.md")
    rows = _rows(report)
    assert rows["ledger_present"]["status"] == "fail"
    assert rows["ledger_freshness"]["status"] == "unknown"
    assert report["verdict"] == "fail"


def test_ledger_stale_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, ledger_age_hours=30))
    assert _rows(report)["ledger_freshness"]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_ledger_reconcile_fail(tmp_path):
    mod = _load()
    report = _run(mod, _make_env(tmp_path, ledger_reconcile="FAIL"))
    rows = _rows(report)
    assert rows["ledger_reconcile"]["status"] == "fail"
    assert report["verdict"] == "fail"


def test_ledger_parse_rows(tmp_path, env):
    mod = _load()
    parsed = mod.parse_ledger(env["ledger"])
    assert parsed["exists"] is True and parsed["count"] == 1
    row = parsed["latest"]
    assert row["reconcile"] == "OK"
    assert row["date"] == env["last_run"].strftime("%Y-%m-%d")
    assert row["total"] == "994,189.62"


# ---------------------------------------------------------------------------
# 解析器（中英文表头 / 时间格式 / Last Result 代码）
# ---------------------------------------------------------------------------
def test_parse_chinese_headers():
    mod = _load()
    text = ("主机名,任务名,下次运行时间,状态,登录模式,上次运行时间,上次结果,作者,"
            "要运行的任务,起始于,注释,计划任务状态,运行方式用户\n"
            "DESKTOP-X,\\HK_R1_Daily,2026/11/01 18:30:00,Ready,Interactive only,"
            "2026/10/31 18:30:02,0,N/A,\"%s\",C:\\aios,N/A,Enabled,t\n"
            % TASK_ACTION.replace('"', "'"))
    task, err = mod.parse_task_csv_text(text)
    assert err == ""
    assert task["task_name"] == "\\HK_R1_Daily"
    assert task["last_result"] == "0"
    assert task["scheduled_state"] == "Enabled"
    assert "--append" in task["task_to_run"]


def test_parse_dt_and_last_result():
    mod = _load()
    parsed = mod.parse_dt("2026/10/30 18:30:01")
    assert parsed is not None and parsed.strftime("%Y-%m-%d %H:%M:%S") == "2026-10-30 18:30:01"
    us = mod.parse_dt("10/30/2026 6:30:01 PM")
    assert us is not None and us.strftime("%H:%M:%S") == "18:30:01"
    assert mod.parse_dt("N/A") is None
    assert mod.parse_dt("") is None
    assert mod.parse_last_result("0") == 0
    assert mod.parse_last_result("1") == 1
    assert mod.parse_last_result("0x41301") == 267009
    assert mod.parse_last_result("N/A") is None
    assert "正在运行" in mod.task_result_note(267009)
    assert "0x41306" in mod.task_result_note(267014)


def test_parse_task_csv_missing_columns():
    mod = _load()
    task, err = mod.parse_task_csv_text("a,b,c\n1,2,3\n")
    assert task == {} and "关键列" in err


# ---------------------------------------------------------------------------
# 路径定位
# ---------------------------------------------------------------------------
def test_locate_r1_paths_from_tool_constants():
    mod = _load()
    located = mod.locate_r1_paths(REPO)
    assert located["source"] == "hk_r1_daily.py"
    assert Path(located["ledger"]).name == "hk_r1_observation_v1.md"
    assert Path(located["ledger"]).parent.name == "_reviews_20261026"
    assert Path(located["logs"]).name == "hk_r1_logs"


# ---------------------------------------------------------------------------
# 渲染 / CLI
# ---------------------------------------------------------------------------
def test_render_markdown_sections(env):
    mod = _load()
    report = _run(mod, env)
    text = mod.render_markdown(report)
    assert "# R1 恢复只读检查" in text
    assert "## 判据表" in text
    assert "## R1 产物（只读）" in text
    assert "**判定：PASS**" in text
    assert "不启动" in text


def _cli(*args, timeout=120):
    return subprocess.run([sys.executable, "-X", "utf8", str(TOOL), *args],
                          cwd=str(REPO), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def _cli_env_args(env):
    return ["--task-csv", str(env["task_csv"]), "--ledger", str(env["ledger"]),
            "--logs-dir", str(env["logs"]), "--now", env["now"].isoformat()]


def test_cli_json_pass(tmp_path, env):
    proc = _cli("--json", "--no-write", *_cli_env_args(env))
    assert proc.returncode == 0, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["verdict"] == "pass"
    assert report["schema"] == "r1-daily-check/v1"


def test_cli_fail_exit_1(tmp_path):
    env = _make_env(tmp_path, last_result="1")
    proc = _cli("--json", "--no-write", *_cli_env_args(env))
    assert proc.returncode == 1, proc.stderr[-800:]
    assert json.loads(proc.stdout)["verdict"] == "fail"


def test_cli_unknown_exit_2(tmp_path, env):
    args = _cli_env_args(env)
    index = args.index("--task-csv")
    args[index + 1] = str(tmp_path / "no_such_task.csv")
    proc = _cli("--json", *args)
    assert proc.returncode == 2, proc.stderr[-800:]
    report = json.loads(proc.stdout)
    assert report["verdict"] == "unknown"
    assert "不可读" in report["task"]["query_error"]


def test_cli_markdown_out_writes(tmp_path, env):
    out = tmp_path / "out"
    proc = _cli("--markdown", "--out", str(out), *_cli_env_args(env))
    assert proc.returncode == 0, proc.stderr[-800:]
    assert "# R1 恢复只读检查" in proc.stdout
    files = sorted(out.glob("r1_daily_check_*"))
    assert len(files) == 2
    assert {item.suffix for item in files} == {".md", ".json"}
    assert files[0].parent == out


def test_cli_no_write_skips_files(tmp_path, env):
    out = tmp_path / "out_nowrite"
    proc = _cli("--json", "--out", str(out), "--no-write", *_cli_env_args(env))
    assert proc.returncode == 0
    assert not out.exists()


def test_cli_bad_now_returns_2():
    proc = _cli("--json", "--now", "not-a-time")
    assert proc.returncode == 2
    assert "参数错误" in proc.stderr
