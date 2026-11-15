# -*- coding: utf-8 -*-
"""gate 选取与告警去重（2026-11-16 自查）。

背景（判例）
-----------
生产报告连续多日出现 4 条同内容告警「学习线 gate 产出过期 128.17h」，4 条分别挂在
学习-入库/流水线/治理/标准化四个任务上，但其实是同一个事实。追查发现两层原因：

  ① ``gate_status()`` 排序键把「名字严格为 learning_gate_YYYYMMDD.json」的文件
     排在首位，且只看 ``files[0]``。全量报告由 **周任务**（每周一 10:00）产出，
     自上周一之后再无新的全量报告，于是被永久锁定在那一份，age 单调增长、
     每天必红（一周里 6 天）。它同时把「分阶段 gate 一直在产」这个事实整个遮住。
  ② 四个任务共用同一个 ``gate_status()`` 结果，同一事实被复制成 4 条告警。

因此「学习线 gate 停了」是误判：**汇总 gate 是周报，周期天然 168h**，用 36h 阈值
衡量必然过期。本文件把这两条钉成可回归的判据。

纪律：日期/编号一律相对时间构造，不写字面量（本机输出通道有串味史）。
运行：python -m pytest -q tools/tests/test_gate_selection_20261116.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools" / "research"))

_NOW = datetime.now().astimezone()


def _load():
    p = ROOT / "tools" / "research" / "daily_tasks_health.py"
    spec = importlib.util.spec_from_file_location("daily_tasks_health_gate_under_test", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _make_gate(dirp: Path, *, days_ago: float, suffix: str, ok: bool = True,
               checks: int = 8, fail: int = 0) -> Path:
    """造一份 gate 报告：文件名日期 = generated_at 日期，mtime 同步对齐。"""
    ts = _NOW - timedelta(days=days_ago)
    stage = "all" if suffix == "" else suffix.lstrip("_")
    name = f"learning_gate_{ts.strftime('%Y%m%d')}{suffix}.json"
    p = dirp / name
    data = {
        "generated_at": ts.isoformat(timespec="seconds"),
        "stage": stage,
        "projects": [],
        "summary": {"projects": 1, "checks": checks, "fail": fail},
        "ok": ok,
    }
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.utime(p, (ts.timestamp(), ts.timestamp()))
    return p


# ---------------------------------------------------------------------------
# ① 选取：按 generated_at 取最新，而不是「全量形态恒优先」
# ---------------------------------------------------------------------------
def test_picks_latest_stage_report_over_week_old_full(tmp_path):
    """全量报告是 7 天前的、分阶段报告是 3 小时前的 → 必须选分阶段那份。

    旧实现会选 7 天前的全量（名字形态优先），于是报「过期 168h」而完全看不见
    分阶段报告每天在产。这条是「停 5 天」误判的直接来源。
    """
    mod = _load()
    _make_gate(tmp_path, days_ago=7.0, suffix="", checks=25)
    _make_gate(tmp_path, days_ago=0.12, suffix="_intake", ok=False, fail=1)

    st = mod.gate_status(_NOW, reports_dir=tmp_path)

    assert st["exists"] is True
    assert st["is_full"] is False, "选了全量报告，说明仍在按名字形态优先"
    assert st["stage"] == "intake"
    assert st["age_hours"] is not None and st["age_hours"] < 4.0
    assert st["fresh"] is True, "分阶段报告 3 小时前产出，不该判过期"


def test_full_report_is_picked_when_it_is_the_newest(tmp_path):
    """全量报告确实最新（周一 10:00 之后）→ 应选全量，且带上 is_full 标记。"""
    mod = _load()
    _make_gate(tmp_path, days_ago=0.5, suffix="", checks=25)
    _make_gate(tmp_path, days_ago=0.9, suffix="_intake", ok=False, fail=1)

    st = mod.gate_status(_NOW, reports_dir=tmp_path)

    assert st["is_full"] is True
    assert st["stage"] == "all"
    assert st["filename"].endswith(".json") and "_intake" not in st["filename"]


def test_generated_at_wins_over_mtime(tmp_path):
    """补跑/复制会让 mtime 晚于真实生成时间 —— 必须按报告内 generated_at 判新旧。"""
    mod = _load()
    old = _make_gate(tmp_path, days_ago=3.0, suffix="", checks=25)
    new = _make_gate(tmp_path, days_ago=0.2, suffix="_units")
    # 把「旧」文件的 mtime 改到最新，模拟补跑覆盖
    future = (_NOW + timedelta(minutes=1)).timestamp()
    os.utime(old, (future, future))

    st = mod.gate_status(_NOW, reports_dir=tmp_path)

    assert st["stage"] == "units", "被 mtime 骗了，选成了补跑过的旧报告"


# ---------------------------------------------------------------------------
# ② 新鲜度阈值必须按生产者周期：周报不能用日报的 36h
# ---------------------------------------------------------------------------
def test_weekly_full_gate_not_stale_after_five_days(tmp_path):
    """只有 5 天前的全量报告（周任务的正常状态）→ 不算过期。"""
    mod = _load()
    _make_gate(tmp_path, days_ago=5.0, suffix="", checks=25)

    st = mod.gate_status(_NOW, reports_dir=tmp_path)

    assert st["is_full"] is True
    assert st["age_hours"] > 100
    assert st["fresh"] is True, "周报被 36h 阈值判过期，等于每周固定误报 6 天"
    assert st["max_age_hours"] >= 168


def test_stage_gate_still_held_to_daily_threshold(tmp_path):
    """分阶段 gate 是日报节奏 → 超过 36h 仍应判过期（阈值没有被整体放宽）。"""
    mod = _load()
    _make_gate(tmp_path, days_ago=3.0, suffix="_intake")

    st = mod.gate_status(_NOW, reports_dir=tmp_path)

    assert st["is_full"] is False
    assert st["fresh"] is False


# ---------------------------------------------------------------------------
# ③ 告警文本要能指认是哪一段 gate 失败
# ---------------------------------------------------------------------------
def _task_stub(**kw):
    base = {"name": "ZeroBound_LearningIntake", "label": "学习-入库", "gate": True,
            "stale_hours": 36}
    base.update(kw)
    return base


def _info_ok():
    return {"found": True, "last_run_time": "x", "last_result": 0, "status": "Ready"}


def _log_ok():
    return {"exists": True, "timestamp": None, "rc": 0, "rcs": None,
            "line": "rc=0", "age_hours": 0.5}


def _out_ok():
    return {"path": "p", "exists": True, "fresh": True, "age_hours": 0.5, "spec": {}}


def test_stage_failure_is_attributed_in_issue_text():
    """分阶段 gate 未通过 → issue 必须写明是哪一段，不能只说「gate 未通过」。"""
    mod = _load()
    task = mod.check_task(
        _task_stub(), _NOW, info=_info_ok(), log=_log_ok(), outputs=[_out_ok()],
        gate={"exists": True, "ok": False, "age_hours": 0.2, "fresh": True,
              "is_full": False, "stage": "intake",
              "summary": {"projects": 8, "checks": 8, "fail": 1}, "filename": "f.json"})

    assert task["status"] == "alert"
    joined = " | ".join(task["issues"])
    assert "gate(intake)" in joined, f"未归因到具体阶段：{joined}"
    assert "fail=1" in joined, f"未带失败计数：{joined}"


def test_full_gate_failure_keeps_plain_gate_label():
    """全量 gate 未通过 → 保持「gate 未通过」，不硬套阶段名。"""
    mod = _load()
    task = mod.check_task(
        _task_stub(), _NOW, info=_info_ok(), log=_log_ok(), outputs=[_out_ok()],
        gate={"exists": True, "ok": False, "age_hours": 2.0, "fresh": True,
              "is_full": True, "stage": "all",
              "summary": {"projects": 7, "checks": 25, "fail": 2}, "filename": "f.json"})

    joined = " | ".join(task["issues"])
    assert "gate 未通过" in joined
    assert "gate(all)" not in joined


# ---------------------------------------------------------------------------
# ④ 同一事实不重复计数：内容相同的告警折叠，但真实条数不丢
# ---------------------------------------------------------------------------
def _alert_task(name: str, label: str, issues: list[str]) -> dict:
    return {"name": name, "label": label, "status": "alert", "issues": list(issues),
            "task": {"found": True, "last_result": 0}, "log": {"line": "rc=0"},
            "outputs": [{"path": "p", "age_hours": 1.0}], "gate": {"ok": None}}


def test_identical_alerts_collapsed_but_count_preserved():
    """4 个任务报同一条内容 → 告警条目折叠为 1，但 summary 仍记 4 个告警任务。"""
    mod = _load()
    same = ["gate 产出过期 128.17h（>36h）"]
    tasks = [_alert_task(f"T{i}", f"L{i}", same) for i in range(4)]

    rep = mod.build_report(tasks, _NOW, tag="t")

    assert rep["summary"]["alert"] == 4, "summary 应保留真实告警任务数"
    assert len(rep["alerts"]) == 1, "相同内容应折叠为一条"
    assert rep["alerts"][0]["dup_count"] == 4
    assert set(rep["alerts"][0]["dup_tasks"]) == {"T0", "T1", "T2", "T3"}
    # 告警文本里同一句只出现一次
    assert rep["alert_text"].count("gate 产出过期 128.17h") == 1


def test_distinct_alerts_not_collapsed():
    """内容不同的告警不许折叠 —— 去重只看完全相同的 issues。"""
    mod = _load()
    tasks = [_alert_task("A", "a", ["日志 rc=1"]),
             _alert_task("B", "b", ["产出缺失：x"])]

    rep = mod.build_report(tasks, _NOW, tag="t")

    assert len(rep["alerts"]) == 2
    assert rep["summary"]["alert"] == 2


def test_single_task_summary_shape_unchanged():
    """单任务场景 summary 仍恰好三个键（既有消费面依赖）。"""
    mod = _load()
    rep = mod.build_report([_alert_task("A", "a", ["x"])], _NOW, tag="t")
    assert set(rep["summary"]) == {"total", "ok", "alert"}
