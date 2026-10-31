# -*- coding: utf-8 -*-
"""daily_three_ledger：日终三账快照测试（全离线 mock，不碰生产/不跑三工具）。

覆盖：任务账（统计/漂移/缺 owner/异常）、计划账（覆盖/unspecified/缺目录）、
证据账（pass/fail/unknown/非法 JSON/缺 verdict）、三账聚合判定、unknown 不伪造、
markdown 渲染、落盘文件名、CLI 退出码 0/1/2 与 --no-write。
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

TOOL = Path(__file__).resolve().parents[1] / "daily_three_ledger.py"
REPO = TOOL.parents[1]


def _load():
    spec = importlib.util.spec_from_file_location("daily_three_ledger_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MOD = _load()


# ---------------------------------------------------------------------------
# mock 输入构造
# ---------------------------------------------------------------------------
def _write_json(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n",
                    encoding="utf-8")
    return path


def _write_tasks(repo: Path, tasks: dict) -> Path:
    return _write_json(repo / ".worktrees" / "tasks.json", tasks)


def _write_plan(repo: Path, name: str, status=None, body="") -> Path:
    text = "# %s\n\n" % name
    if status is not None:
        text += "> STATUS: %s\n\n" % status
    text += body or "正文\n"
    path = repo / "docs" / "plans" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _audit_pass(**over) -> dict:
    data = {"ok": True, "generated_at": "2026-10-31 17:00:00",
            "open_tasks": 0, "summary": {}, "rows": []}
    data.update(over)
    return data


def _audit_drift() -> dict:
    return {"ok": True, "generated_at": "2026-10-31 17:00:00", "open_tasks": 3,
            "summary": {"ahead_unmerged": 2, "merged_but_open": 1},
            "rows": [
                {"tid": "T-A", "status": "doing", "owner": "DEV1",
                 "category": "ahead_unmerged", "ahead": 2,
                 "worktree_exists": True, "dirty": False},
                {"tid": "T-B", "status": "review", "owner": "DEV2",
                 "category": "ahead_unmerged", "ahead": 1,
                 "worktree_exists": True, "dirty": False},
                {"tid": "T-C", "status": "doing", "owner": "ACAD1",
                 "category": "merged_but_open", "ahead": 0,
                 "worktree_exists": True, "dirty": False},
            ]}


def _pack(verdict="pass", criteria=None, **over) -> dict:
    data = {
        "schema": "m1-evidence-pack/v1",
        "generated_at": "2026-10-31T17:00:00+08:00",
        "verdict": verdict,
        "counts": {"pass": 6, "fail": 0, "unknown": 0, "total": 6,
                   "required_total": 3, "required_pass": 3, "required_fail": 0,
                   "required_unknown": 0},
        "verdict_reasons": {"required_fail": [], "required_unknown": [],
                            "advisory_not_pass": []},
        "criteria": criteria if criteria is not None else [
            {"id": "M1-KPI", "required": True, "status": "pass",
             "detail": "verdict=pass pass=9 fail=0 unknown=0（共 9 项）"},
            {"id": "W1-DRILL", "required": True, "status": "pass",
             "detail": "ok=True 场景 5/5"},
            {"id": "W2-INVARIANT", "required": True, "status": "pass",
             "detail": "verdict=PASS 违规=0"},
            {"id": "M4-SIGNOFF", "required": False, "status": "pass",
             "detail": "approve=3"},
        ],
        "services": {"m4": {"status": "pending", "detail": "approve=0"}},
        "gaps": [],
    }
    data.update(over)
    return data


def _build(repo: Path, audit=None, pack=None, date="2026-10-31", **kwargs):
    """便捷聚合：注入 audit/pack 为 dict 时写入临时文件再走 file 模式。"""
    audit_path = None
    if audit is not None:
        audit_path = _write_json(repo / "audit.json", audit)
    evidence_path = None
    if pack is not None:
        evidence_path = _write_json(repo / "pack.json", pack)
    return MOD.build_snapshot(date=date, repo=repo,
                              audit_json=audit_path, evidence_json=evidence_path,
                              **kwargs)


# ---------------------------------------------------------------------------
# 任务账
# ---------------------------------------------------------------------------
def test_task_counts_and_pass(tmp_path):
    _write_tasks(tmp_path, {
        "T-A": {"status": "done", "owner": "DEV1"},
        "T-B": {"status": "doing", "owner": "DSH-Lead"},
        "T-C": {"status": "review", "owner": ""},
        "T-D": {"status": "aborted", "owner": "unknown-agent"},
    })
    ledger = MOD.collect_task_ledger(repo=tmp_path,
                                     audit_json=_write_json(tmp_path / "a.json",
                                                            _audit_pass()))
    counts = ledger["counts"]
    assert ledger["status"] == "pass"
    assert counts["tasks_total"] == 4
    assert counts["doing"] == 1 and counts["review"] == 1
    assert counts["done"] == 1 and counts["aborted"] == 1
    assert counts["missing_owner"] == 2
    assert ledger["drift"]["drift_total"] == 0


def test_task_drift_fail_and_gap_rows(tmp_path):
    _write_tasks(tmp_path, {"T-A": {"status": "doing", "owner": "DEV1"}})
    ledger = MOD.collect_task_ledger(repo=tmp_path,
                                     audit_json=_write_json(tmp_path / "a.json",
                                                            _audit_drift()))
    assert ledger["status"] == "fail"
    assert ledger["drift"]["drift_total"] == 3
    assert len(ledger["gaps"]) == 3
    assert {row["category"] for row in ledger["gaps"]} == {"ahead_unmerged",
                                                           "merged_but_open"}


def test_task_tasks_json_missing_unknown(tmp_path):
    ledger = MOD.collect_task_ledger(repo=tmp_path,
                                     audit_json=_write_json(tmp_path / "a.json",
                                                            _audit_pass()))
    assert ledger["status"] == "unknown"
    assert "tasks.json" in ledger["error"]
    assert ledger["counts"] == {}


def test_task_audit_json_missing_unknown(tmp_path):
    _write_tasks(tmp_path, {"T-A": {"status": "done", "owner": "DEV1"}})
    ledger = MOD.collect_task_ledger(repo=tmp_path,
                                     audit_json=tmp_path / "nope.json")
    assert ledger["status"] == "unknown"
    assert ledger["counts"]["tasks_total"] == 1


def test_task_audit_not_ok_unknown(tmp_path):
    _write_tasks(tmp_path, {"T-A": {"status": "done", "owner": "DEV1"}})
    bad = _write_json(tmp_path / "a.json", {"ok": False, "error": "tasks.json 不可读"})
    ledger = MOD.collect_task_ledger(repo=tmp_path, audit_json=bad)
    assert ledger["status"] == "unknown"
    assert "ok=false" in ledger["error"]


# ---------------------------------------------------------------------------
# 计划账
# ---------------------------------------------------------------------------
def test_plan_coverage_pass(tmp_path):
    _write_plan(tmp_path, "2026-10-01_a.md", "declared")
    _write_plan(tmp_path, "2026-10-02_b.md", "frozen（只读）")
    _write_plan(tmp_path, "2026-10-03_c.md")  # README-like plan without status?
    (tmp_path / "docs" / "plans" / "README.md").write_text("# 说明\n", encoding="utf-8")
    ledger = MOD.collect_plan_ledger(repo=tmp_path)
    counts = ledger["counts"]
    assert counts["plan_docs"] == 3
    assert counts["with_status"] == 2
    assert counts["unspecified"] == 1
    assert ledger["status"] == "fail"  # 有 1 份 unspecified → fail（诚实）
    assert counts["coverage_pct"] == pytest.approx(66.67, abs=0.01)


def test_plan_all_status_pass(tmp_path):
    _write_plan(tmp_path, "2026-10-01_a.md", "declared")
    _write_plan(tmp_path, "2026-10-02_b.md", "done")
    ledger = MOD.collect_plan_ledger(repo=tmp_path)
    assert ledger["status"] == "pass"
    assert ledger["counts"]["unspecified"] == 0
    assert ledger["counts"]["coverage_pct"] == 100.0


def test_plan_dir_missing_unknown(tmp_path):
    ledger = MOD.collect_plan_ledger(repo=tmp_path)
    assert ledger["status"] == "unknown"
    assert "不存在" in ledger["error"]


def test_plan_top_readme_excluded_but_subdir_fixture_counted(tmp_path):
    # 顶层 README/生成台账不算计划；子目录夹具 README（有 STATUS）照常计入
    _write_plan(tmp_path, "2026-10-01_a.md", "declared")
    plans = tmp_path / "docs" / "plans"
    (plans / "README.md").write_text("# 目录说明\n", encoding="utf-8")
    (plans / "PLAN_REGISTER_ALL_20261031.md").write_text("# 台账\n", encoding="utf-8")
    fixture = plans / "2026-10-28_W1模板"
    fixture.mkdir(parents=True)
    (fixture / "README.md").write_text("# 夹具\n\n> STATUS: frozen\n", encoding="utf-8")
    counts = MOD.scan_plans(plans)
    assert counts["plan_docs"] == 2
    assert counts["with_status"] == 2
    assert counts["unspecified"] == 0


def test_tool_script_prefers_repo_tools(tmp_path):
    (tmp_path / "tools").mkdir(parents=True)
    script = tmp_path / "tools" / "task_board_audit.py"
    script.write_text("# stub\n", encoding="utf-8")
    assert MOD._tool_script(tmp_path, "task_board_audit.py") == script
    assert MOD._tool_script(tmp_path / "no_tools", "task_board_audit.py").name == \
        "task_board_audit.py"
    assert MOD._tool_script(tmp_path / "no_tools", "task_board_audit.py").parent == \
        MOD.TOOLS_DIR


def test_plan_empty_dir_unknown(tmp_path):
    (tmp_path / "docs" / "plans").mkdir(parents=True)
    ledger = MOD.collect_plan_ledger(repo=tmp_path)
    assert ledger["status"] == "unknown"
    assert "无计划文档" in ledger["error"]


# ---------------------------------------------------------------------------
# 证据账
# ---------------------------------------------------------------------------
def test_evidence_pass_maps_kpi_summary(tmp_path):
    pack_path = _write_json(tmp_path / "pack.json", _pack())
    ledger = MOD.collect_evidence_ledger(repo=tmp_path, evidence_json=pack_path)
    assert ledger["status"] == "pass"
    assert ledger["pack"]["verdict"] == "pass"
    assert "verdict=pass" in ledger["pack"]["kpi_summary"]["m1_kpi"]
    assert ledger["pack"]["kpi_summary"]["m4_signoff"] == "pending"
    assert ledger["gaps"] == []


def test_evidence_fail_marks_gaps(tmp_path):
    criteria = [
        {"id": "M1-KPI", "required": True, "status": "fail", "detail": "pass=7 fail=2"},
        {"id": "W1-DRILL", "required": True, "status": "pass", "detail": "ok=True"},
        {"id": "M4-SIGNOFF", "required": False, "status": "fail", "detail": "approve=0"},
    ]
    pack = _pack(verdict="fail", criteria=criteria,
                 verdict_reasons={"required_fail": ["M1-KPI"],
                                  "required_unknown": [],
                                  "advisory_not_pass": ["M4-SIGNOFF"]})
    ledger = MOD.collect_evidence_ledger(repo=tmp_path,
                                         evidence_json=_write_json(tmp_path / "p.json", pack))
    assert ledger["status"] == "fail"
    assert {gap["id"] for gap in ledger["gaps"]} == {"M1-KPI", "M4-SIGNOFF"}


def test_evidence_unknown_keeps_error(tmp_path):
    pack = {"schema": "m1-evidence-pack/v1", "verdict": "unknown",
            "error": "tool timeout", "criteria": []}
    ledger = MOD.collect_evidence_ledger(repo=tmp_path,
                                         evidence_json=_write_json(tmp_path / "p.json", pack))
    assert ledger["status"] == "unknown"
    assert "tool timeout" in ledger["error"]


def test_evidence_invalid_json_unknown(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not-json", encoding="utf-8")
    ledger = MOD.collect_evidence_ledger(repo=tmp_path, evidence_json=path)
    assert ledger["status"] == "unknown"
    assert "非 JSON" in ledger["error"]


def test_evidence_missing_file_unknown(tmp_path):
    ledger = MOD.collect_evidence_ledger(repo=tmp_path,
                                         evidence_json=tmp_path / "nope.json")
    assert ledger["status"] == "unknown"
    assert "不存在" in ledger["error"]


def test_evidence_illegal_verdict_unknown(tmp_path):
    pack = {"schema": "m1-evidence-pack/v1", "verdict": "weird", "criteria": []}
    ledger = MOD.collect_evidence_ledger(repo=tmp_path,
                                         evidence_json=_write_json(tmp_path / "p.json", pack))
    assert ledger["status"] == "unknown"
    assert "verdict 非法" in ledger["error"]


# ---------------------------------------------------------------------------
# 聚合判定
# ---------------------------------------------------------------------------
def _full_repo(tmp_path: Path) -> Path:
    _write_tasks(tmp_path, {"T-A": {"status": "done", "owner": "DEV1"}})
    _write_plan(tmp_path, "2026-10-01_a.md", "declared")
    return tmp_path


def test_snapshot_all_pass(tmp_path):
    _full_repo(tmp_path)
    snap = _build(tmp_path, audit=_audit_pass(), pack=_pack())
    assert snap["verdict"] == "pass"
    assert snap["passed"] is True
    assert snap["counts"] == {"ledgers_total": 3, "pass": 3, "fail": 0, "unknown": 0}
    assert snap["gaps"] == []
    assert snap["schema"] == "daily-three-ledger/v1"
    assert snap["date"] == "2026-10-31"


def test_snapshot_fail_beats_unknown(tmp_path):
    _full_repo(tmp_path)
    snap = _build(tmp_path, audit=_audit_drift(), pack=_pack())
    assert snap["verdict"] == "fail"
    assert snap["counts"]["fail"] == 1


def test_snapshot_unknown_when_input_missing(tmp_path):
    _full_repo(tmp_path)
    # 计划目录不存在 → 计划账 unknown（任务/证据 pass）
    (tmp_path / "docs" / "plans").rename(tmp_path / "docs" / "plans_moved")
    snap = _build(tmp_path, audit=_audit_pass(), pack=_pack())
    assert snap["verdict"] == "unknown"
    assert snap["counts"]["unknown"] == 1


def test_snapshot_no_fabrication_on_task_missing(tmp_path):
    _write_plan(tmp_path, "2026-10-01_a.md", "declared")
    snap = _build(tmp_path, audit=_audit_pass(), pack=_pack())
    task = snap["ledgers"]["task"]
    assert task["status"] == "unknown"
    assert task["counts"] == {}
    assert "tasks.json" in task["error"]
    # 渲染不把 unknown 写成 0 余额
    md = MOD.render_markdown(snap)
    assert "UNKNOWN" in md
    assert "tasks.json 不可读" in md


def test_snapshot_invalid_date_raises(tmp_path):
    _full_repo(tmp_path)
    with pytest.raises(ValueError):
        _build(tmp_path, audit=_audit_pass(), pack=_pack(), date="2026/10/31")


# ---------------------------------------------------------------------------
# 渲染 / 落盘
# ---------------------------------------------------------------------------
def test_render_markdown_sections(tmp_path):
    _full_repo(tmp_path)
    snap = _build(tmp_path, audit=_audit_drift(), pack=_pack(verdict="fail"))
    md = MOD.render_markdown(snap)
    for token in ("# 日终三账快照", "三账汇总", "任务账", "计划账", "证据账",
                  "unknown 与缺口清单", "输入源采集", "说明"):
        assert token in md
    assert "FAIL" in md
    assert "| 任务账 |" in md


def test_md_cell_escapes_pipe():
    assert MOD._md_cell("a|b\nc") == "a\\|b c"
    assert MOD._md_cell(None) == "-"


def test_output_paths_date(tmp_path):
    md, js = MOD.output_paths(tmp_path, "2026-10-31")
    assert md.name == "2026-10-31_日终三账快照_v1.md"
    assert js.name == "2026-10-31_日终三账快照_v1.json"
    assert md.parent == tmp_path


def test_write_snapshot_files(tmp_path):
    _full_repo(tmp_path)
    snap = _build(tmp_path, audit=_audit_pass(), pack=_pack())
    out = tmp_path / "deliverables"
    written = MOD.write_snapshot(snap, out)
    md_path = Path(written["markdown"])
    json_path = Path(written["json"])
    assert md_path.is_file() and json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["schema"] == "daily-three-ledger/v1"
    assert data["date"] == "2026-10-31"
    assert "日终三账快照" in md_path.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _cli_args(tmp_path: Path, *, out=None, no_write=True, date="2026-10-31",
              json_mode=True, extra=None) -> list:
    args = ["--repo", str(tmp_path),
            "--tasks-audit-json", str(_write_json(tmp_path / "audit.json", _audit_pass())),
            "--evidence-json", str(_write_json(tmp_path / "pack.json", _pack())),
            "--date", date]
    args += ["--json"] if json_mode else ["--markdown"]
    if no_write:
        args.append("--no-write")
    if out:
        args += ["--out", str(out)]
    if extra:
        args += list(extra)
    return args


def test_main_no_write_exit0(tmp_path, capsys):
    _full_repo(tmp_path)
    out = tmp_path / "deliverables"
    code = MOD.main(_cli_args(tmp_path, out=out, no_write=True))
    captured = capsys.readouterr()
    assert code == 0
    assert not out.exists()
    payload = json.loads(captured.out)
    assert payload["verdict"] == "pass"


def test_main_write_creates_files_exit0(tmp_path):
    _full_repo(tmp_path)
    out = tmp_path / "deliverables"
    code = MOD.main(_cli_args(tmp_path, out=out, no_write=False))
    assert code == 0
    assert (out / "2026-10-31_日终三账快照_v1.md").is_file()
    assert (out / "2026-10-31_日终三账快照_v1.json").is_file()


def test_main_drift_exit1(tmp_path):
    _write_tasks(tmp_path, {"T-A": {"status": "doing", "owner": "DEV1"}})
    _write_plan(tmp_path, "2026-10-01_a.md", "declared")
    args = _cli_args(tmp_path)
    # 覆盖 audit 为漂移版
    args[args.index("--tasks-audit-json") + 1] = str(
        _write_json(tmp_path / "audit.json", _audit_drift()))
    assert MOD.main(args) == 1


def test_main_unknown_exit2(tmp_path):
    _full_repo(tmp_path)
    (tmp_path / "docs" / "plans").rename(tmp_path / "docs" / "moved")
    assert MOD.main(_cli_args(tmp_path)) == 2


def test_main_invalid_date_exit2(tmp_path, capsys):
    _full_repo(tmp_path)
    args = _cli_args(tmp_path, date="20261031")
    assert MOD.main(args) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_main_markdown_mode(tmp_path, capsys):
    _full_repo(tmp_path)
    args = _cli_args(tmp_path, json_mode=False)
    assert MOD.main(args) == 0
    assert "# 日终三账快照" in capsys.readouterr().out
