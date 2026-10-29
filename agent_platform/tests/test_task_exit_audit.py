"""test_task_exit_audit.py — 告警闭环工具测试（决策书 D1）。

纪律：不依赖真实计划任务盘面（会演进的真实数据不写死——S43 判例）；
用 monkeypatch 自造前置状态；账本/摘要写入 tmp 目录隔离。
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(
    0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
)

import task_exit_audit as ta


@pytest.fixture()
def isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "DATA_DIR", str(tmp_path / "audit"))
    return str(tmp_path / "audit")


FAKE_TASKS = [
    {
        "name": "LegionMorningMeeting",
        "state": "Ready",
        "last_result": 0,
        "last_run": "2026-10-22 09:00:00",
        "next_run": "2026-10-23 09:00:00",
    },
    {
        "name": "QuantWatchdog",
        "state": "Running",
        "last_result": 2,
        "last_run": "2026-10-22 01:27:58",
        "next_run": "2026-10-22 01:28:57",
    },
    {
        "name": "AlertWatch",
        "state": "Ready",
        "last_result": 1,
        "last_run": "2026-10-22 01:15:01",
        "next_run": "2026-10-22 01:45:00",
    },
    {
        "name": "KG-QuantRetune",
        "state": "Disabled",
        "last_result": 267011,
        "last_run": "",
        "next_run": "",
    },
    {
        "name": "AgentMondayCheckpoint",
        "state": "Ready",
        "last_result": 3221225786,
        "last_run": "2026-10-19 17:25:00",
        "next_run": "2026-10-26 17:25:00",
    },
]


def test_scan_classifies_and_writes(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS))
    s = ta.scan(audit_family_only=False)

    assert s["total_scanned"] == 5
    assert s["ok"] == 1
    assert s["failed"] == 2  # exit 2 / 1（0xC000013A 已按 EXEC-R7 单列 cancelled）
    assert s["cancelled"] == 1  # 0xC000013A 被取消/强杀：不计 failed
    assert s["launcher_refused"] == 0
    assert s["disabled"] == 1  # Disabled 不计失败
    assert s["never_run"] == 0  # Disabled 的 267011 走 disabled 分支

    names = {f["name"] for f in s["failures"]}
    assert names == {"QuantWatchdog", "AlertWatch"}

    # 账本与摘要都落盘（全量扫描 → 独立 _all 文件，不污染 canonical）
    ledger = os.path.join(isolated_data_dir, ta.LEDGER_ALL_NAME)
    summary = os.path.join(isolated_data_dir, ta.SUMMARY_ALL_NAME)
    assert os.path.isfile(ledger) and os.path.isfile(summary)
    assert not os.path.exists(os.path.join(isolated_data_dir, ta.LEDGER_NAME))
    assert not os.path.exists(os.path.join(isolated_data_dir, ta.SUMMARY_NAME))
    lines = [json.loads(x) for x in open(ledger, encoding="utf-8") if x.strip()]
    assert len(lines) == 5  # append-only，含全部扫描记录
    # 0xC000013A 语义可读，且单列在 cancelled_tasks（不计 failed）
    cp = [f for f in s["cancelled_tasks"] if f["name"] == "AgentMondayCheckpoint"][0]
    assert "取消" in cp["exit_semantics"] and "0xC000013A" in cp["exit_semantics"]


def test_scan_empty_is_honest(isolated_data_dir, monkeypatch):
    """空态诚实：查不到任务不冒充健康。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: [])
    s = ta.scan(audit_family_only=False)
    assert s["total_scanned"] == 0 and s["failed"] == 0
    assert s["note"]


def test_family_filter(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(
        ta,
        "query_tasks_via_powershell",
        lambda: (
            list(FAKE_TASKS)
            + [
                {
                    "name": "GoogleUpdateTask",
                    "state": "Ready",
                    "last_result": 0,
                    "last_run": "2026-10-01 00:00:00",
                    "next_run": "",
                }
            ]
        ),
    )
    s = ta.scan(audit_family_only=True)
    assert s["total_scanned"] == 5  # 系统任务被滤除


def test_family_scan_writes_canonical_only(isolated_data_dir, monkeypatch):
    """canonical（军团系）扫描只写 canonical 文件，不动 _all 文件。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS))
    s = ta.scan(audit_family_only=True)
    assert s["canonical"] is True
    assert os.path.isfile(os.path.join(isolated_data_dir, ta.LEDGER_NAME))
    assert os.path.isfile(os.path.join(isolated_data_dir, ta.SUMMARY_NAME))
    assert not os.path.exists(os.path.join(isolated_data_dir, ta.LEDGER_ALL_NAME))
    assert not os.path.exists(os.path.join(isolated_data_dir, ta.SUMMARY_ALL_NAME))


def test_all_families_does_not_touch_canonical(isolated_data_dir, monkeypatch):
    """全量扫描不能覆盖 canonical 摘要/账本（健康卡防污染回归）。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS))
    ta.scan(audit_family_only=True)
    canonical_summary = os.path.join(isolated_data_dir, ta.SUMMARY_NAME)
    canonical_ledger = os.path.join(isolated_data_dir, ta.LEDGER_NAME)
    before_summary = open(canonical_summary, encoding="utf-8").read()
    before_ledger = open(canonical_ledger, encoding="utf-8").read()
    s = ta.scan(audit_family_only=False)
    assert s["canonical"] is False
    assert open(canonical_summary, encoding="utf-8").read() == before_summary
    assert open(canonical_ledger, encoding="utf-8").read() == before_ledger
    assert os.path.isfile(os.path.join(isolated_data_dir, ta.SUMMARY_ALL_NAME))


def test_never_run_not_failure(isolated_data_dir, monkeypatch):
    """267011（尚未运行）在非 Disabled 态也不算失败。"""
    monkeypatch.setattr(
        ta,
        "query_tasks_via_powershell",
        lambda: [
            {
                "name": "SpaceAgentTask",
                "state": "Ready",
                "last_result": 267011,
                "last_run": "",
                "next_run": "2026-10-23 00:00:00",
            }
        ],
    )
    s = ta.scan(audit_family_only=False)
    assert s["failed"] == 0 and s["never_run"] == 1


def test_launcher_refused_not_failure(isolated_data_dir, monkeypatch):
    """EXEC-R7：0x800710E0（启动器已在运行/启动请求被拒）不计失败，单列 launcher_refused。"""
    monkeypatch.setattr(
        ta,
        "query_tasks_via_powershell",
        lambda: [
            {
                "name": "AI工作站调优回测批次",
                "state": "Ready",
                "last_result": 2147946720,
                "last_run": "2026-10-24 20:23:40",
                "next_run": "",
            }
        ],
    )
    s = ta.scan(audit_family_only=False)
    assert s["failed"] == 0
    assert s["launcher_refused"] == 1
    assert [f["name"] for f in s["launcher_refused_tasks"]] == ["AI工作站调优回测批次"]
    assert "0x800710E0" in s["launcher_refused_tasks"][0]["exit_semantics"]


def test_summary_only_missing(isolated_data_dir, capsys):
    r = ta.summary_only()
    assert "error" in r


def test_ledger_append_only(isolated_data_dir, monkeypatch):
    """两次扫描账本累加，不覆盖。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS[:2]))
    ta.scan(audit_family_only=False)
    ta.scan(audit_family_only=False)
    ledger = os.path.join(isolated_data_dir, ta.LEDGER_ALL_NAME)
    lines = [x for x in open(ledger, encoding="utf-8") if x.strip()]
    assert len(lines) == 4  # 2 任务 × 2 轮


def _seed_ledger(isolated_data_dir, rounds):
    """自造多轮账本：rounds = [ {任务名: exit码}, ... ] 按时间顺序。"""
    import datetime as dt

    ledger = os.path.join(isolated_data_dir, ta.LEDGER_NAME)
    os.makedirs(isolated_data_dir, exist_ok=True)
    base = dt.datetime.now().astimezone() - dt.timedelta(hours=len(rounds) + 1)
    with open(ledger, "w", encoding="utf-8") as f:
        for i, snapshot in enumerate(rounds):
            ts = base + dt.timedelta(hours=i + 1)
            for name, code in snapshot.items():
                f.write(
                    json.dumps(
                        {
                            "at": ts.isoformat(timespec="seconds"),
                            "name": name,
                            "state": "Ready",
                            "last_result": code,
                            "exit_semantics": "",
                            "last_run": "",
                            "next_run": "",
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )


def test_window_diff_classifies(isolated_data_dir, monkeypatch):
    """窗口对比三分类：QuantWatchdog 全程失败（persisting）、心跳 2→0（fixed）、Nginx 0→1（new）。
    时序：旧状态两轮（cutoff 前）→ 新状态一轮（cutoff 后），保证转换发生在窗口内。"""
    _seed_ledger(
        isolated_data_dir,
        [
            {"QuantWatchdog": 2, "AgentLegionHeartbeat": 2, "AI工作站门户Nginx": 0},
            {"QuantWatchdog": 2, "AgentLegionHeartbeat": 2, "AI工作站门户Nginx": 0},
            {"QuantWatchdog": 2, "AgentLegionHeartbeat": 0, "AI工作站门户Nginx": 1},
        ],
    )
    r = ta.window_diff(hours=1.5)
    assert "error" not in r
    assert r["new_failures"] == ["AI工作站门户Nginx"]
    assert r["persisting_failures"] == ["QuantWatchdog"]
    assert r["fixed_in_window"] == ["AgentLegionHeartbeat"]


def test_window_diff_insufficient(isolated_data_dir):
    """账本不足两次扫描：如实报错，不硬算。"""
    _seed_ledger(isolated_data_dir, [{"A": 0}])
    r = ta.window_diff(hours=1.0)
    assert "error" in r


def test_window_diff_disabled_not_failure(isolated_data_dir):
    """Disabled 与 267011（未跑）不计失败。"""
    _seed_ledger(
        isolated_data_dir,
        [
            {"KG-QuantRetune": 267011, "SpaceTask": 1},
            {"KG-QuantRetune": 267011, "SpaceTask": 1},
        ],
    )
    # state 字段在 _seed_ledger 固定为 Ready，这里直接验证 267011 排除逻辑
    r = ta.window_diff(hours=1.5)
    assert "KG-QuantRetune" not in r.get("persisting_failures", [])


def test_window_diff_excludes_non_failure_rc(isolated_data_dir):
    """EXEC-R7：被取消(0xC000013A)/启动器已在跑(0x800710E0)/上游拒跑(3) 不进失败差集。"""
    _seed_ledger(
        isolated_data_dir,
        [
            {
                "CancelledTask": 3221225786,
                "LauncherTask": 2147946720,
                "UpstreamTask": 3,
                "RealFail": 1,
            },
            {
                "CancelledTask": 3221225786,
                "LauncherTask": 2147946720,
                "UpstreamTask": 3,
                "RealFail": 1,
            },
        ],
    )
    r = ta.window_diff(hours=1.5)
    assert "error" not in r
    assert r["persisting_failures"] == ["RealFail"]


def test_running_and_terminated_not_failure(isolated_data_dir, monkeypatch):
    """EXEC-2026-10-25：0x41001 跑批中 / 0x41306 被终止 均不计 failed。

    实测背景：2026-10-25 审计 8 个 failed 里，AI工作站门户API(267009)、
    XueqiuBackfillSprint(267009)、NoticeBackfill(267014) 三个是语义误计。
    """
    fake = [
        {
            "name": "AI工作站门户API",
            "state": "Ready",
            "last_result": 267009,
            "last_run": "2026-10-25 15:10:57",
            "next_run": "2026-10-25 15:15:00",
        },
        {
            "name": "NoticeBackfill",
            "state": "Ready",
            "last_result": 267014,
            "last_run": "2026-10-25 06:45:01",
            "next_run": "2026-10-26 06:45:00",
        },
    ]
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: fake)
    s = ta.scan(audit_family_only=False)

    assert s["failed"] == 0
    assert s["running"] == 1
    assert s["terminated"] == 1
    assert [f["name"] for f in s["running_tasks"]] == ["AI工作站门户API"]
    assert [f["name"] for f in s["terminated_tasks"]] == ["NoticeBackfill"]
    assert "0x41001" in s["running_tasks"][0]["exit_semantics"]
    assert "0x41306" in s["terminated_tasks"][0]["exit_semantics"]


# ---------------- P0-1 回归（WO-LEGION-P0FIX-20261026）----------------
# 旧实现 `if --scan / elif --window`：两参数同给时 --window 被**静默丢弃**，
# 用组合命令做收口却只落快照——正是“快照假绿”判例要防的假绿。


def _fake_scan(audit_family_only=True):
    return {
        "total_scanned": 1,
        "ok": 1,
        "failed": 0,
        "upstream_hold": 0,
        "launcher_refused": 0,
        "cancelled": 0,
        "running": 0,
        "terminated": 0,
        "disabled": 0,
        "never_run": 0,
        "failures": [],
    }


def _fake_window(hours=24.0):
    return {
        "window_hours": hours,
        "compared": {
            "prior_scan_at": "2026-10-25T00:00:00+08:00",
            "latest_scan_at": "2026-10-26T00:00:00+08:00",
        },
        "new_failures": ["A"],
        "persisting_failures": [],
        "fixed_in_window": [],
    }


def test_scan_and_window_combined_not_dropped(monkeypatch, capsys):
    calls = {}

    def fake_scan(audit_family_only=True):
        calls["scan"] = audit_family_only
        return _fake_scan()

    def fake_window(hours=24.0):
        calls["window"] = hours
        return _fake_window(hours)

    monkeypatch.setattr(ta, "scan", fake_scan)
    monkeypatch.setattr(ta, "window_diff", fake_window)
    rc = ta.main(["--scan", "--window", "6", "--json"])
    data = json.loads(capsys.readouterr().out)

    assert calls == {"scan": True, "window": 6.0}, "--window 被静默丢弃（P0-1 回归）"
    assert rc == 0
    assert data["scan"]["total_scanned"] == 1
    assert data["window"]["window_hours"] == 6.0


def test_scan_and_window_text_prints_both(monkeypatch, capsys):
    monkeypatch.setattr(ta, "scan", lambda audit_family_only=True: _fake_scan())
    monkeypatch.setattr(ta, "window_diff", lambda hours=24.0: _fake_window(hours))
    rc = ta.main(["--scan", "--window"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "扫描 1 任务" in out
    assert "窗口 24.0h 对比" in out


def test_scan_only_keeps_flat_output(monkeypatch, capsys):
    """单参数行为向后兼容：仍输出扁平扫描 JSON（不套 scan 键）。"""
    monkeypatch.setattr(ta, "scan", lambda audit_family_only=True: _fake_scan())
    rc = ta.main(["--scan", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert "scan" not in data and data["total_scanned"] == 1


def test_combined_window_error_returns_nonzero(monkeypatch, capsys):
    """组合模式下窗口不可用：如实报错（非零退出），但扫描结果照常输出。"""
    monkeypatch.setattr(ta, "scan", lambda audit_family_only=True: _fake_scan())
    monkeypatch.setattr(
        ta, "window_diff", lambda hours=24.0: {"error": "账本仅 1 条，不足两次扫描"}
    )
    rc = ta.main(["--scan", "--window", "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert data["scan"]["total_scanned"] == 1
    assert "不足两次扫描" in data["window"]["error"]


# ---------------------------------------------------------------------------
# 2026-10-27 AUDIT-09（审计 L-7）：任务级 by-design 语义映射单测
#   KgChainWatchdog rc=1 = 发现坏链 → alert_found（非任务失败）
#   PytestWeekly   rc=1 = k_stock 2 个既存失败（已知基线）→ known_baseline
# ---------------------------------------------------------------------------
def test_classify_task_by_design_mapping():
    assert ta.classify_task_by_design("KgChainWatchdog", 1) == (
        "alert_found",
        "发现坏链告警(by-design，非任务失败)",
    )
    assert ta.classify_task_by_design("PytestWeekly", 1) == (
        "known_baseline",
        "已知基线：历史收集炸窗已修(61f51fe9)，待下轮复验；原 k_stock 归因错误",
    )
    assert ta.classify_task_by_design("DeadlineWatch", 2)[0] == "known_baseline"
    assert ta.classify_task_by_design("x5-picks-publish-check", 2)[0] == "known_baseline"
    assert ta.classify_task_by_design("ObservePointReviewOnce", 1)[0] == "known_baseline"
    # 其它任务同码 / 同任务其它码 → 不映射（保持既有口径）
    assert ta.classify_task_by_design("SomeOtherTask", 1) is None
    assert ta.classify_task_by_design("KgChainWatchdog", 2) is None
    assert ta.classify_task_by_design("PytestWeekly", 0) is None


def test_scan_task_semantics_not_counted_as_failure(isolated_data_dir, monkeypatch):
    """KgChainWatchdog/PytestWeekly 的 rc=1 单列 alert_found/known_baseline，不计 failed。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: [
        {"name": "KgChainWatchdog", "state": "Ready", "last_result": 1,
         "last_run": "2026-10-27 08:00:10", "next_run": "2026-10-27 12:30:00"},
        {"name": "PytestWeekly", "state": "Ready", "last_result": 1,
         "last_run": "2026-10-24 09:00:01", "next_run": "2026-10-31 09:00:00"},
        {"name": "SomeOtherTask", "state": "Ready", "last_result": 1,
         "last_run": "2026-10-27 07:00:00", "next_run": ""},
    ])
    s = ta.scan(audit_family_only=False)
    assert s["failed"] == 1, s["failures"]
    assert {f["name"] for f in s["failures"]} == {"SomeOtherTask"}
    assert s["alert_found"] == 1 and s["known_baseline"] == 1
    assert [f["name"] for f in s["alert_found_tasks"]] == ["KgChainWatchdog"]
    assert [f["name"] for f in s["known_baseline_tasks"]] == ["PytestWeekly"]
    assert "发现坏链" in s["alert_found_tasks"][0]["exit_semantics"]
    assert "已知基线" in s["known_baseline_tasks"][0]["exit_semantics"]
    assert "alert_found" in s["note"] and "known_baseline" in s["note"]
    # 账本仍 append-only 记录全部 3 条，且映射行带 task_semantics 字段
    ledger = os.path.join(isolated_data_dir, ta.LEDGER_ALL_NAME)
    rows = [json.loads(x) for x in open(ledger, encoding="utf-8") if x.strip()]
    assert len(rows) == 3
    by_name = {r["name"]: r for r in rows}
    assert "task_semantics" in by_name["KgChainWatchdog"]
    assert "task_semantics" in by_name["PytestWeekly"]
    assert "task_semantics" not in by_name["SomeOtherTask"]


def test_window_diff_excludes_task_by_design(isolated_data_dir):
    """窗口差集排除 by-design 两类；真实失败仍持续失败。"""
    _seed_ledger(isolated_data_dir, [
        {"KgChainWatchdog": 1, "PytestWeekly": 1, "RealFail": 1},
        {"KgChainWatchdog": 1, "PytestWeekly": 1, "RealFail": 1},
    ])
    r = ta.window_diff(hours=1.5)
    assert "error" not in r
    assert r["persisting_failures"] == ["RealFail"]
    assert "KgChainWatchdog" not in r["persisting_failures"]
    assert "PytestWeekly" not in r["persisting_failures"]


def test_scan_text_print_shows_semantics_buckets(isolated_data_dir, monkeypatch, capsys):
    """文本摘要必须显示「告警发现/已知基线」计数与明细（不淹没在 failures 里）。"""
    def fake_scan(audit_family_only=True):
        return {
            "total_scanned": 2, "ok": 0, "failed": 0, "upstream_hold": 0,
            "launcher_refused": 0, "cancelled": 0, "running": 0, "terminated": 0,
            "alert_found": 1, "known_baseline": 1, "disabled": 0, "never_run": 0,
            "failures": [],
            "alert_found_tasks": [{"name": "KgChainWatchdog", "last_result": 1,
                                   "exit_semantics": "发现坏链告警(by-design，非任务失败)",
                                   "last_run": "2026-10-27 08:00:10"}],
            "known_baseline_tasks": [{"name": "PytestWeekly", "last_result": 1,
                                      "exit_semantics": "k_stock 2 个既存失败(已知基线，非任务失败)",
                                      "last_run": "2026-10-24 09:00:01"}],
        }
    monkeypatch.setattr(ta, "scan", fake_scan)
    rc = ta.main(["--scan"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "告警发现 1" in out and "已知基线 1" in out
    assert "[ALERT_FOUND] KgChainWatchdog" in out
    assert "[BASELINE] PytestWeekly" in out


def test_terminated_exit_includes_dbg_terminate():
    assert 1073807364 in ta.TERMINATED_EXITS
    assert 1073807364 in ta.NON_FAILURE_RC
    assert 1073807364 not in ta.TASK_RC_SEMANTICS.values()
