# -*- coding: utf-8 -*-
"""WO-HZ-COVTREND-20261031 单测：黄忠覆盖率趋势与 11-04 投影工具（tmp 夹具，不碰真实数据）。

覆盖点：
  · 逐日统计（runs/PASS/REJECT/OBSERVE + merge_done + 双口径覆盖率）正确；
  · merge_done 缺失 → coverage None（不按 0 编造）；
  · 投影公式（固定时钟/固定参数逐项复算）；
  · unique commit 按 sha 去重 + 演练台账/标注/台账 sha 三重排除；
  · 坏行/坏 JSON 容错 + fail-visible 计数；
  · target 已过去 → past_target 提示；
  · CLI --json / md+json 落盘。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_MOD = None
NOW = "2026-10-31T12:00"          # 固定时钟（naive → 本机 Asia/Shanghai）
TARGET = "2026-11-04T10:00"
SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
SHA_DRILL = "e" * 40


def _load():
    global _MOD
    if _MOD is None:
        p = Path(__file__).resolve().parent / "huangzhong_coverage_trend.py"
        spec = importlib.util.spec_from_file_location("huangzhong_coverage_trend", p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MOD = mod
    return _MOD


def _write_jsonl(p: Path, rows) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows),
                 encoding="utf-8")


def _merge_event(ts: str, task: str = "WO-TEST") -> dict:
    return {"ts": ts, "day": ts[:10], "kind": "merge_done", "task": task,
            "agent": "t", "detail": "守卫全绿"}


def _acceptance(root: Path, name: str, verdict: str = "PASS", sha: str = SHA_A,
                generated_at: str = "2026-10-30T10:00:00+08:00", **extra) -> Path:
    d = {"ok": True, "acceptance_id": "HZ-" + name, "generated_at": generated_at,
         "mode": "observe", "blocking": False,
         "commit": {"ok": True, "sha": sha}, "verdict": verdict, "reasons": []}
    d.update(extra)
    p = root / "work" / "huangzhong" / ("acceptance_%s.json" % name)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    return p


def _make_root(tmp_path: Path, daily=None, events=None, with_files: bool = True) -> Path:
    """构造 tmp 仓库布局：<root>/work/huangzhong/ + <root>/portal/data/dev_events.jsonl。"""
    root = tmp_path / "repo"
    root.mkdir(parents=True, exist_ok=True)
    if with_files:
        _write_jsonl(root / "work" / "huangzhong" / "daily.jsonl", daily or [])
        _write_jsonl(root / "portal" / "data" / "dev_events.jsonl", events or [])
    return root


# ---------------------------------------------------------------- 逐日统计

def test_daily_stats_per_day(tmp_path):
    mod = _load()
    daily = [
        {"ts": "2026-10-30T10:00:00+08:00", "commit": "123456789abc", "verdict": "PASS"},
        {"ts": "2026-10-30T11:00:00+08:00", "commit": "223456789abc", "verdict": "REJECT"},
        {"ts": "2026-10-31T01:00:00+08:00", "commit": "323456789abc", "verdict": "OBSERVE"},
        {"ts": "???", "verdict": "PASS"},  # 坏 ts → missing_ts
    ]
    events = [
        _merge_event("2026-10-30 10:00:00", "WO-A"),
        _merge_event("2026-10-30 12:00:00", "WO-B"),
        _merge_event("2026-10-31 09:00:00", "WO-C"),
        {"ts": "2026-10-30 10:00:00", "kind": "task_start", "task": "WO-X"},
        {"ts": "2026-10-31 09:00:00", "day": "2026-10-31", "kind": "commit", "task": "WO-C"},
    ]
    root = _make_root(tmp_path, daily=daily, events=events)
    # daily.jsonl 追加一行坏 JSON
    with (root / "work" / "huangzhong" / "daily.jsonl").open("a", encoding="utf-8") as f:
        f.write("{not-json\n")
    # 验收单：同 camp 同日两个不同 sha，其中一个重复 sha 去重
    _acceptance(root, "a", sha=SHA_A, generated_at="2026-10-30T09:00:00+08:00")
    _acceptance(root, "a2", sha=SHA_A, generated_at="2026-10-30T10:00:00+08:00")
    _acceptance(root, "c", sha=SHA_C, generated_at="2026-10-30T11:00:00+08:00")

    rep = mod.compute(repo_root=root, days=14, target=TARGET, now=mod._parse_ts(NOW),
                      write=False)
    assert rep["ok"] is True
    assert len(rep["days"]) == 14
    assert rep["days"][-1]["date"] == "2026-10-31"
    by_date = {r["date"]: r for r in rep["days"]}

    d30 = by_date["2026-10-30"]
    assert (d30["runs"], d30["pass"], d30["reject"], d30["observe"]) == (2, 1, 1, 0)
    assert d30["merge_done"] == 2
    assert d30["coverage"] == pytest.approx(1.0)
    assert d30["unique_commit"] == 2  # A（重复去重）+ C
    assert d30["unique_coverage"] == pytest.approx(1.0)

    d31 = by_date["2026-10-31"]
    assert (d31["runs"], d31["observe"]) == (1, 1)
    assert d31["merge_done"] == 1
    assert d31["coverage"] == pytest.approx(1.0)
    assert d31["unique_commit"] == 0

    # 空日：缺数据 → runs 0 / coverage None（不编造）
    empty = by_date["2026-10-20"]
    assert empty["runs"] == 0 and empty["merge_done"] == 0
    assert empty["coverage"] is None and empty["unique_coverage"] is None

    inp = rep["inputs"]
    assert inp["bad_lines"]["daily"] == 1
    assert inp["daily_missing_ts"] == 1
    assert rep["last7"]["runs"] == 3
    assert rep["last7"]["merge_done"] == 3
    assert rep["last7"]["coverage"] == pytest.approx(1.0)
    assert rep["last7"]["unique_commit"] == 2


def test_merge_done_missing_coverage_none(tmp_path):
    mod = _load()
    daily = [{"ts": "2026-10-31T01:00:00+08:00", "verdict": "PASS"}]
    events = [{"ts": "2026-10-31 01:00:00", "kind": "task_start", "task": "WO-X"}]
    root = _make_root(tmp_path, daily=daily, events=events)
    _acceptance(root, "a", sha=SHA_A, generated_at="2026-10-31T01:00:00+08:00")

    rep = mod.compute(repo_root=root, days=14, target=TARGET, now=mod._parse_ts(NOW),
                      write=False)
    assert rep["ok"] is True
    assert rep["days"][-1]["coverage"] is None
    assert rep["days"][-1]["unique_coverage"] is None
    assert rep["last7"]["coverage"] is None
    assert rep["last7"]["unique_coverage"] is None
    assert rep["projection"]["projected_merge_done"] is None
    assert rep["projection"]["projected_coverage"] is None
    assert rep["threshold_pass"] is None
    assert any("无 merge_done 事件" in w for w in rep["warnings"])


def test_window_days_param(tmp_path):
    mod = _load()
    root = _make_root(tmp_path, daily=[], events=[])
    rep = mod.compute(repo_root=root, days=5, target=TARGET, now=mod._parse_ts(NOW),
                      write=False)
    assert len(rep["days"]) == 5
    assert rep["days"][0]["date"] == "2026-10-27"
    assert rep["window"] == {"days": 5, "start": "2026-10-27", "end": "2026-10-31"}
    assert rep["last7"]["window_days"] == 5
    assert any("--days=5" in w for w in rep["warnings"])


# ---------------------------------------------------------------- 投影公式

def test_projection_formula_fixed_params(tmp_path):
    mod = _load()
    now = mod._parse_ts(NOW)
    target = mod._parse_ts(TARGET)
    summary = {"runs": 10, "avg_merge_daily": 10.0, "avg_daily_unique": 2.0}
    unique7d = {"unique_commit": 3}
    proj = mod.project(summary, unique7d, target, now, runs_per_day=24)
    # 2026-10-31 12:00 → 2026-11-04 10:00 = 3 天 22 小时 = 3.9166667 天
    assert proj["remaining_days"] == pytest.approx(3.9167, abs=1e-4)
    assert proj["past_target"] is False
    assert proj["projected_runs"] == pytest.approx(10 + 24 * (94 / 24), abs=0.1)
    assert proj["projected_merge_done"] == pytest.approx(70.0)
    assert proj["projected_coverage"] == pytest.approx(104 / 70, abs=1e-4)
    # unique 投影 = 近 7 日去重 3 + 每日 unique 均值 2.0 × 剩余天数
    assert proj["projected_unique_commits"] == pytest.approx(3 + 2.0 * (94 / 24), abs=0.1)
    assert proj["projected_unique_coverage"] == pytest.approx(
        (3 + 2.0 * (94 / 24)) / 70, abs=1e-4)
    assert proj["threshold_pass"] is True
    assert proj["runs_per_day"] == 24


def test_projection_compute_with_fixture(tmp_path):
    mod = _load()
    # 近 7 日（10-25 ~ 10-31）runs 合计 10、merge 合计 70；构造每日数据
    daily, events = [], []
    runs_plan = {"2026-10-27": 3, "2026-10-28": 2, "2026-10-29": 1,
                 "2026-10-30": 2, "2026-10-31": 2}
    for day, n in runs_plan.items():
        for i in range(n):
            daily.append({"ts": "%sT0%d:00:00+08:00" % (day, i + 1), "verdict": "PASS"})
    for i in range(7):
        day = "2026-10-%02d" % (25 + i)
        for j in range(10):
            events.append(_merge_event("%s %02d:00:00" % (day, j), "WO-%02d" % j))
    root = _make_root(tmp_path, daily=daily, events=events)
    _acceptance(root, "a", sha=SHA_A, generated_at="2026-10-30T09:00:00+08:00")
    _acceptance(root, "b", sha=SHA_B, generated_at="2026-10-31T09:00:00+08:00")
    _acceptance(root, "c", sha=SHA_C, generated_at="2026-10-31T10:00:00+08:00")

    rep = mod.compute(repo_root=root, days=14, target=TARGET,
                      now=mod._parse_ts(NOW), runs_per_day=48, write=False)
    last, proj = rep["last7"], rep["projection"]
    assert last["runs"] == 10
    assert last["merge_done"] == 70
    assert last["avg_merge_daily"] == pytest.approx(10.0)
    assert last["unique_commit"] == 3
    assert last["avg_daily_unique"] == pytest.approx(3 / 7, abs=0.01)  # 1+2 两日
    assert proj["projected_runs"] == pytest.approx(10 + 48 * (94 / 24), abs=0.1)
    assert proj["projected_merge_done"] == pytest.approx(70.0)
    assert proj["projected_coverage"] == pytest.approx(
        (10 + 48 * (94 / 24)) / 70, abs=1e-4)
    assert proj["projected_unique_commits"] == pytest.approx(
        3 + (3 / 7) * (94 / 24), abs=0.11)


def test_target_past_warning(tmp_path):
    mod = _load()
    daily = [{"ts": "2026-10-31T01:00:00+08:00", "verdict": "PASS"}]
    events = [_merge_event("2026-10-31 01:00:00")]
    root = _make_root(tmp_path, daily=daily, events=events)
    now = mod._parse_ts("2026-11-05T12:00")
    rep = mod.compute(repo_root=root, days=14, target=TARGET, now=now, write=False)
    proj = rep["projection"]
    assert proj["past_target"] is True
    assert proj["remaining_days"] == 0.0
    assert proj["projected_runs"] == pytest.approx(proj["window_runs_basis"])
    assert proj["note"] and "target 已过去" in proj["note"]
    assert any("target 已过去" in w for w in rep["warnings"])


# ---------------------------------------------------------------- unique 去重/演练排除

def test_unique_commit_dedup_and_drill_exclusion(tmp_path):
    mod = _load()
    root = _make_root(
        tmp_path,
        daily=[],
        events=[_merge_event("2026-10-30 %02d:00:00" % i) for i in range(1, 6)],
    )
    _acceptance(root, "a", sha=SHA_A, generated_at="2026-10-30T09:00:00+08:00")
    _acceptance(root, "a_dup", sha=SHA_A, generated_at="2026-10-30T10:00:00+08:00")
    _acceptance(root, "c", sha=SHA_C, generated_at="2026-10-30T11:00:00+08:00")
    _acceptance(root, "reject", verdict="REJECT", sha="d" * 40,
                generated_at="2026-10-30T12:00:00+08:00")
    # 演练单 1：台账按 acceptance_report 文件名排除
    _acceptance(root, "drill_file", sha="f" * 40,
                generated_at="2026-10-30T13:00:00+08:00")
    # 演练单 2：台账按 target_commit sha8 排除（文件名不在台账）
    _acceptance(root, "drill_sha", sha=SHA_DRILL,
                generated_at="2026-10-30T14:00:00+08:00")
    # 演练单 2b：台账里的非 PASS 演练单（REJECT）同样要显式排除留痕
    _acceptance(root, "drill_ledger_reject", verdict="REJECT", sha="e2" * 20,
                generated_at="2026-10-30T14:30:00+08:00")
    # 演练单 3：JSON 自带 drill=true
    _acceptance(root, "drill_flag", sha="f2" * 20, drill=True,
                generated_at="2026-10-30T15:00:00+08:00")
    # 演练单 4：同名 .md 首行标注 drill=true（台账缺失时兜底）
    p = _acceptance(root, "drill_md", sha="f3" * 20,
                    generated_at="2026-10-30T16:00:00+08:00")
    p.with_suffix(".md").write_text(
        "> 受控拒收演练单（drill=true，不计入 PASS 样本/覆盖率 KPI）\n# 黄忠验收单\n",
        encoding="utf-8")
    # 坏 JSON + 缺 generated_at
    (root / "work" / "huangzhong" / "acceptance_bad.json").write_text(
        "{broken", encoding="utf-8")
    _acceptance(root, "no_ts", sha="f4" * 20, generated_at="")
    ledger = {
        "schema": "huangzhong_drill_ledger/v1",
        "drills": [
            {"drill_id": "DRILL-T-1", "drill": True, "kind": "REJECT",
             "target_commit": SHA_DRILL,
             "acceptance_report": "work/huangzhong/acceptance_drill_file.json"},
            {"drill_id": "DRILL-T-2", "drill": True, "kind": "REJECT",
             "target_commit": "e2" * 20,
             "acceptance_report": "work/huangzhong/acceptance_drill_ledger_reject.json"},
        ],
    }
    drill_dir = root / "work" / "huangzhong" / "drills"
    drill_dir.mkdir(parents=True)
    (drill_dir / "drill_ledger_20261030.json").write_text(
        json.dumps(ledger, ensure_ascii=False), encoding="utf-8")

    rep = mod.compute(repo_root=root, days=14, target=TARGET,
                      now=mod._parse_ts(NOW), write=False)
    inp = rep["inputs"]
    assert inp["drill_entries"] == 2
    assert inp["acceptance_pass_sheets"] == 3  # a/a_dup/c（同 sha 去重后计 2 unique）
    assert len(inp["excluded_drills"]) == 5
    assert set(inp["excluded_drills"]) == {
        "acceptance_drill_file.json", "acceptance_drill_sha.json",
        "acceptance_drill_ledger_reject.json",
        "acceptance_drill_flag.json", "acceptance_drill_md.json"}
    assert inp["bad_lines"]["acceptance_invalid"] == 1
    assert any("缺 commit.sha" in w or "缺/坏 generated_at" in w for w in rep["warnings"])

    d30 = {r["date"]: r for r in rep["days"]}["2026-10-30"]
    assert d30["unique_commit"] == 2
    assert d30["unique_coverage"] == pytest.approx(2 / 5)
    assert rep["last7"]["unique_commit"] == 2


# ---------------------------------------------------------------- 缺文件 / CLI

def test_missing_files_fail_visible(tmp_path):
    mod = _load()
    root = tmp_path / "empty_repo"
    root.mkdir()
    rep = mod.compute(repo_root=root, days=14, target=TARGET,
                      now=mod._parse_ts(NOW), write=False)
    assert rep["ok"] is False
    assert set(rep["inputs"]["missing"]) == {"daily.jsonl", "dev_events.jsonl"}
    assert all(r["runs"] == 0 for r in rep["days"])
    assert all(r["coverage"] is None for r in rep["days"])
    assert any("daily.jsonl 缺失" in w for w in rep["warnings"])
    assert any("dev_events.jsonl 缺失" in w for w in rep["warnings"])

    rc = mod.main(["--repo-root", str(root), "--now", NOW, "--json", "--no-write"])
    assert rc == 1


def test_cli_json_and_md_output(tmp_path, capsys):
    mod = _load()
    daily = [{"ts": "2026-10-31T01:00:00+08:00", "verdict": "PASS"}]
    events = [_merge_event("2026-10-31 01:00:00")]
    root = _make_root(tmp_path, daily=daily, events=events)
    _acceptance(root, "a", sha=SHA_A, generated_at="2026-10-31T01:00:00+08:00")
    out = tmp_path / "out"

    rc = mod.main(["--repo-root", str(root), "--out-dir", str(out), "--days", "14",
                   "--target", TARGET, "--now", NOW, "--json"])
    assert rc == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["ok"] is True
    assert rep["last7"]["runs"] == 1 and rep["last7"]["merge_done"] == 1
    assert rep["paths"]["md"] and Path(rep["paths"]["md"]).is_file()
    assert Path(rep["paths"]["json"]).is_file()
    md_name = Path(rep["paths"]["md"]).name
    assert md_name.startswith("2026-11-04_黄忠覆盖率趋势_") and md_name.endswith(".md")

    md = Path(rep["paths"]["md"]).read_text(encoding="utf-8")
    for section in ("## 一、结论摘要", "## 二、逐日趋势", "## 三、近 7 天均值",
                    "## 四、2026-11-04T10:00 投影", "## 五、假设与风险",
                    "## 六、数据源与 fail-visible", "## 七、复现命令"):
        assert section in md, section
    assert "| 2026-10-31 | 1 |" in md
    saved = json.loads(Path(rep["paths"]["json"]).read_text(encoding="utf-8"))
    assert saved["days"][-1]["date"] == "2026-10-31"


def test_cli_bad_target_rc2(tmp_path, capsys):
    mod = _load()
    root = _make_root(tmp_path, daily=[], events=[])
    assert mod.main(["--repo-root", str(root), "--target", "not-a-date",
                     "--no-write", "--json"]) == 2
    assert "参数错误" in capsys.readouterr().err
