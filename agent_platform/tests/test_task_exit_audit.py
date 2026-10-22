"""test_task_exit_audit.py — 告警闭环工具测试（决策书 D1）。

纪律：不依赖真实计划任务盘面（会演进的真实数据不写死——S43 判例）；
用 monkeypatch 自造前置状态；账本/摘要写入 tmp 目录隔离。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))

import task_exit_audit as ta


@pytest.fixture()
def isolated_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(ta, "DATA_DIR", str(tmp_path / "audit"))
    return str(tmp_path / "audit")


FAKE_TASKS = [
    {"name": "LegionMorningMeeting", "state": "Ready", "last_result": 0,
     "last_run": "2026-10-22 09:00:00", "next_run": "2026-10-23 09:00:00"},
    {"name": "QuantWatchdog", "state": "Running", "last_result": 2,
     "last_run": "2026-10-22 01:27:58", "next_run": "2026-10-22 01:28:57"},
    {"name": "AlertWatch", "state": "Ready", "last_result": 1,
     "last_run": "2026-10-22 01:15:01", "next_run": "2026-10-22 01:45:00"},
    {"name": "KG-QuantRetune", "state": "Disabled", "last_result": 267011,
     "last_run": "", "next_run": ""},
    {"name": "AgentMondayCheckpoint", "state": "Ready", "last_result": 3221225786,
     "last_run": "2026-10-19 17:25:00", "next_run": "2026-10-26 17:25:00"},
]


def test_scan_classifies_and_writes(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS))
    s = ta.scan(audit_family_only=False)

    assert s["total_scanned"] == 5
    assert s["ok"] == 1
    assert s["failed"] == 3  # exit 2 / 1 / 0xC000013A
    assert s["disabled"] == 1  # Disabled 不计失败
    assert s["never_run"] == 0  # Disabled 的 267011 走 disabled 分支

    names = {f["name"] for f in s["failures"]}
    assert names == {"QuantWatchdog", "AlertWatch", "AgentMondayCheckpoint"}

    # 账本与摘要都落盘
    ledger = os.path.join(isolated_data_dir, ta.LEDGER_NAME)
    summary = os.path.join(isolated_data_dir, ta.SUMMARY_NAME)
    assert os.path.isfile(ledger) and os.path.isfile(summary)
    lines = [json.loads(x) for x in open(ledger, encoding="utf-8") if x.strip()]
    assert len(lines) == 5  # append-only，含全部扫描记录
    # 0xC000013A 语义可读
    cp = [f for f in s["failures"] if f["name"] == "AgentMondayCheckpoint"][0]
    assert "强杀" in cp["exit_semantics"]


def test_scan_empty_is_honest(isolated_data_dir, monkeypatch):
    """空态诚实：查不到任务不冒充健康。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: [])
    s = ta.scan(audit_family_only=False)
    assert s["total_scanned"] == 0 and s["failed"] == 0
    assert s["note"]


def test_family_filter(isolated_data_dir, monkeypatch):
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS) + [
        {"name": "GoogleUpdateTask", "state": "Ready", "last_result": 0,
         "last_run": "2026-10-01 00:00:00", "next_run": ""}])
    s = ta.scan(audit_family_only=True)
    assert s["total_scanned"] == 5  # 系统任务被滤除


def test_never_run_not_failure(isolated_data_dir, monkeypatch):
    """267011（尚未运行）在非 Disabled 态也不算失败。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: [
        {"name": "SpaceAgentTask", "state": "Ready", "last_result": 267011,
         "last_run": "", "next_run": "2026-10-23 00:00:00"}])
    s = ta.scan(audit_family_only=False)
    assert s["failed"] == 0 and s["never_run"] == 1


def test_summary_only_missing(isolated_data_dir, capsys):
    r = ta.summary_only()
    assert "error" in r


def test_ledger_append_only(isolated_data_dir, monkeypatch):
    """两次扫描账本累加，不覆盖。"""
    monkeypatch.setattr(ta, "query_tasks_via_powershell", lambda: list(FAKE_TASKS[:2]))
    ta.scan(audit_family_only=False)
    ta.scan(audit_family_only=False)
    ledger = os.path.join(isolated_data_dir, ta.LEDGER_NAME)
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
                f.write(json.dumps({
                    "at": ts.isoformat(timespec="seconds"),
                    "name": name, "state": "Ready",
                    "last_result": code, "exit_semantics": "",
                    "last_run": "", "next_run": "",
                }, ensure_ascii=False) + "\n")


def test_window_diff_classifies(isolated_data_dir, monkeypatch):
    """窗口对比三分类：QuantWatchdog 全程失败（persisting）、心跳 2→0（fixed）、Nginx 0→1（new）。
    时序：旧状态两轮（cutoff 前）→ 新状态一轮（cutoff 后），保证转换发生在窗口内。"""
    _seed_ledger(isolated_data_dir, [
        {"QuantWatchdog": 2, "AgentLegionHeartbeat": 2, "AI工作站门户Nginx": 0},
        {"QuantWatchdog": 2, "AgentLegionHeartbeat": 2, "AI工作站门户Nginx": 0},
        {"QuantWatchdog": 2, "AgentLegionHeartbeat": 0, "AI工作站门户Nginx": 1},
    ])
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
    _seed_ledger(isolated_data_dir, [
        {"KG-QuantRetune": 267011, "SpaceTask": 1},
        {"KG-QuantRetune": 267011, "SpaceTask": 1},
    ])
    # state 字段在 _seed_ledger 固定为 Ready，这里直接验证 267011 排除逻辑
    r = ta.window_diff(hours=1.5)
    assert "KG-QuantRetune" not in r.get("persisting_failures", [])
