# -*- coding: utf-8 -*-
"""tools/weekly_kpi_board.py 单测（WO-LEGION-WEEKLY-KPI-20261031）。

覆盖：各指标 collector 的达标/失败/unknown 分类、gate 清单解析、fast gate 覆盖口径、
计划台账自指豁免、TaskStore append-only 快照语义、md/json 输出、CLI 退出码。
全部用 tmp_path 假仓库 + monkeypatch 依赖，不读真实账本、不联网、不起服务。
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

TOOLS = Path(__file__).resolve().parents[1]
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import weekly_kpi_board as wkb  # noqa: E402

NOW = dt.datetime(2026, 11, 1, 20, 0, 0)
DATE = "2026-11-01"


# ----------------------------------------------------------------------
# fixtures / 假仓库
# ----------------------------------------------------------------------
def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


GATE_PS1 = r"""
$paths = @{
    # 注释行不应被解析
    'portal' = @(
        'portal/tests',
        'portal/test_bot_ws_backoff.py'
    )
    'kstock' = 'k_stock/tests'
    'agent'  = 'agent_platform/tests'; 'quant' = 'quant_backtest/tests'
    'tools'  = 'tools/tests'
    'ide'    = @(
        'agent_platform/tests/test_ide_release_artifact.py'
    )
    'agent_key' = @(
        'agent_platform/tests/test_covered_agent.py'
    )
    'quant_key' = @()
    'tools_key' = @(
        'tools/tests/test_covered_tool.py'
    )
}
# ===== 后续区块 =====
$newFailures = 0
"""


def _tasks_payload(*, doing=0, review=0, owners=None, d1_doing=0, bad_owner=False):
    tasks = {}
    owners = owners or []
    for i in range(doing):
        tid = "WO-KPI-DOING-%02d" % i
        tasks[tid] = {
            "desc": "task %d" % i,
            "branch": "task/%s" % tid,
            "worktree": "C:\\fake\\.worktrees\\%s" % tid,
            "status": "doing",
            "owner": owners[i] if i < len(owners) else "DSH-Lead",
            "created": "2026-10-30 10:00:00",
        }
    for i in range(review):
        tid = "WO-KPI-REVIEW-%02d" % i
        tasks[tid] = {
            "desc": "task %d" % i,
            "branch": "task/%s" % tid,
            "worktree": "C:\\fake\\.worktrees\\%s" % tid,
            "status": "review",
            "owner": "ACAD1",
            "created": "2026-10-30 10:00:00",
        }
    for i in range(d1_doing):
        tid = "WO-INV-WEEK-20261029-%d" % i
        tasks[tid] = {"desc": "D1 周单", "status": "doing", "owner": "T%d" % i}
    if bad_owner:
        tasks["WO-KPI-UNOWNED"] = {
            "desc": "无主单", "status": "doing", "owner": "unknown-agent",
            "branch": "task/WO-KPI-UNOWNED", "worktree": "C:\\fake\\.worktrees\\u"}
    return tasks


def make_repo(tmp_path: Path, *, tasks=None, plan_plans=None, plan_generated=None,
              taskstore_statuses=None, outcome_rows=None, candidates=None,
              gate_ps1=GATE_PS1, coverage_files=None):
    repo = tmp_path / "repo"
    repo.mkdir(parents=True, exist_ok=True)
    tasks = _tasks_payload(doing=2) if tasks is None else tasks
    _write(repo / ".worktrees" / "tasks.json",
           json.dumps(tasks, ensure_ascii=False, indent=1))
    # 计划台账
    plans = plan_plans if plan_plans is not None else [
        {"path": "docs/plans/a.md", "state": "declared"},
        {"path": "docs/plans/b.md", "state": "declared"},
        {"path": "docs/plans/PLAN_REGISTER_ALL_20261031.md", "state": "unspecified"},
    ]
    generated = plan_generated or NOW.strftime("%Y-%m-%d %H:%M:%S")
    _write(repo / "docs" / "plans" / "PLAN_REGISTER_ALL_20261031.json",
           json.dumps({"schema": "plan-register-all/v1", "generated_at": generated,
                       "total": len(plans), "counts": {"declared": len(plans) - 1},
                       "plans": plans}, ensure_ascii=False))
    # TaskStore append-only 快照（两行：先 doing 后 done → 取最后）
    rows = []
    for tid, st in (taskstore_statuses or
                    {"T-1": "done", "T-2": "cancelled", "T-3": "blocked"}).items():
        rows.append({"task_id": tid, "status": st, "title": "x"})
    _write(repo / "agent_platform" / "data" / "org" / "tasks" / "tasks_202610.jsonl",
           "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n")
    # outcome
    out_rows = outcome_rows if outcome_rows is not None else [
        {"decision_id": "D1-20261031-abcdef0123", "hit": 1, "symbol": "600519"},
        {"decision_id": "", "hit": None, "symbol": "000001"},
    ]
    _write(repo / "agent_platform" / "data" / "shadow" / "decision_outcomes.jsonl",
           "\n".join(json.dumps(r, ensure_ascii=False) for r in out_rows) + "\n")
    # 候选证据包
    for name in (candidates or ["RA-20261031-LCS1-600519-v1.json",
                                "RA-20261031-LCS1-600519-v2.json"]):
        _write(repo / "agent_platform" / "data" / "academy" / "artifacts" / name, "{}")
    # gate 配置 + 覆盖文件
    _write(repo / "tools" / "test_gate.ps1", gate_ps1)
    _write(repo / "agent_platform" / "tests" / "test_ide_release_artifact.py",
           "def test_artifact():\n    assert True\n")
    files = coverage_files if coverage_files is not None else {
        "tools/tests/test_covered_tool.py": "a = 1\nb = 2\n\ndef test_x():\n    assert a\n",
        "tools/tests/test_uncovered_tool.py": "x = 1\n",
    }
    for rel, content in files.items():
        _write(repo / rel, content)
    return repo


def make_ctx(repo: Path, tmp_path: Path, *, temp_dir=None, now=NOW):
    return wkb.Ctx(repo=repo, date=DATE, now=now,
                   release_root=tmp_path / "release",
                   gate_log_dirs=[Path(temp_dir or (tmp_path / "temp"))],
                   milestone="2026-11-07")


def _fake_guard_ok():
    return {"schema": "release_artifact_guard/v1", "ok": True, "exit_code": 0,
            "pointer": {"version": "20261031_120000_abc"}, "manifest": {"version": "x"},
            "summary": {"files_ok": 2, "files_total": 2},
            "junction": {"drift": False}, "errors": []}


def _patch_ok(monkeypatch):
    monkeypatch.setattr(wkb, "_run_worktree_check", lambda ctx: {
        "verdict": "PASS", "violations": [],
        "counts": {"active": 2, "violations": 0}})
    monkeypatch.setattr(wkb, "_run_release_guard", lambda ctx: _fake_guard_ok())
    monkeypatch.setattr(wkb, "_git_bundle_window",
                        lambda ctx, since: [])  # 无 auto-rebuild 增长
    return monkeypatch


def _write_gate_log(temp_dir: Path, verdict="GATE PASS: 无新增失败", name="test_gate_fast_20261031_120000.log"):
    temp_dir.mkdir(parents=True, exist_ok=True)
    p = temp_dir / name
    p.write_text(verdict + "\n", encoding="utf-8")
    return p


# ----------------------------------------------------------------------
# 单元：解析 / 分类
# ----------------------------------------------------------------------
def test_parse_gate_paths():
    paths = wkb.parse_gate_paths(GATE_PS1)
    assert paths["kstock"] == ["k_stock/tests"]
    assert paths["agent"] == ["agent_platform/tests"]
    assert "agent_platform/tests/test_ide_release_artifact.py" in paths["ide"]
    assert paths["tools_key"] == ["tools/tests/test_covered_tool.py"]
    assert paths["quant_key"] == []


def test_parse_gate_paths_missing_block_raises():
    with pytest.raises(wkb.InputError):
        wkb.parse_gate_paths("$nothing = 1")


def test_metric_status_validation_and_cell_escape():
    m = wkb._metric("k", "标题", 1, "1", "0", wkb.STATUS_PASS, evidence="a|b")
    assert m["status"] == "pass" and m["evidence"] == "a|b"
    with pytest.raises(ValueError):
        wkb._metric("k", "t", 1, "1", "0", "bogus")
    assert "\\|" in wkb._md_cell("a|b")


def test_collect_devtask_open_boundary(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path, tasks=_tasks_payload(doing=20))
    ctx = make_ctx(repo, tmp_path)
    m = wkb.collect_devtask_open(ctx)
    assert m["value"] == 20 and m["status"] == wkb.STATUS_PASS
    repo2 = make_repo(tmp_path / "r2", tasks=_tasks_payload(doing=14, review=7))
    ctx2 = make_ctx(repo2, tmp_path)
    m2 = wkb.collect_devtask_open(ctx2)
    assert m2["value"] == 21 and m2["status"] == wkb.STATUS_FAIL
    assert "21" in m2["blocker"]


def test_collect_unknown_agent_placeholders(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path, tasks=_tasks_payload(doing=2, bad_owner=True))
    m = wkb.collect_unknown_agent(make_ctx(repo, tmp_path))
    assert m["value"] == 1 and m["status"] == wkb.STATUS_FAIL


def test_collect_missing_worktree_detects_violation(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    monkeypatch.setattr(wkb, "_run_worktree_check", lambda ctx: {
        "verdict": "FAIL",
        "violations": [{"code": "ACTIVE_WORKTREE_NOT_FOUND", "task": "WO-X"},
                       {"code": "ACTIVE_BRANCH_NOT_FOUND", "task": "WO-Y"}],
        "counts": {"active": 2, "violations": 2}})
    repo = make_repo(tmp_path)
    m = wkb.collect_missing_worktree(make_ctx(repo, tmp_path))
    assert m["value"] == 1 and m["status"] == wkb.STATUS_FAIL
    assert m["detail"]["branch_missing"] == ["WO-Y"]


def test_taskstore_takes_last_snapshot_status(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    p = repo / "agent_platform" / "data" / "org" / "tasks" / "tasks_202611.jsonl"
    _write(p, json.dumps({"task_id": "T-1", "status": "doing"}) + "\n")
    m = wkb.collect_taskstore_doing(make_ctx(repo, tmp_path))
    # tasks_202610 里 T-1 为 done，但更新的 tasks_202611 快照为 doing（按文件序、行序取最后）
    assert m["value"] == 1 and m["status"] == wkb.STATUS_PASS


def test_taskstore_missing_ledger_is_input_error(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    with pytest.raises(wkb.InputError):
        wkb.collect_taskstore_doing(make_ctx(repo, tmp_path))


def test_plan_status_exempts_generated_self_reference(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)  # 唯一 unspecified 是 PLAN_REGISTER_ALL 自指
    m = wkb.collect_plan_status(make_ctx(repo, tmp_path))
    assert m["value"] == 100.0 and m["status"] == wkb.STATUS_PASS


def test_plan_status_real_unspecified_fails(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path, plan_plans=[
        {"path": "docs/plans/a.md", "state": "declared"},
        {"path": "docs/plans/real_pending.md", "state": "unspecified"}])
    m = wkb.collect_plan_status(make_ctx(repo, tmp_path))
    assert m["status"] == wkb.STATUS_FAIL and m["value"] == 50.0
    assert "real_pending.md" in m["blocker"]


def test_plan_status_stale_register_warns(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    old = (NOW - dt.timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    repo = make_repo(tmp_path, plan_generated=old)
    m = wkb.collect_plan_status(make_ctx(repo, tmp_path))
    assert m["status"] == wkb.STATUS_WARN and "未刷新" in m["blocker"]


def test_version_all_match_fail_and_unknown(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    ctx = make_ctx(repo, tmp_path)
    monkeypatch.setattr(wkb, "_run_release_guard", lambda ctx: {
        **_fake_guard_ok(), "ok": False, "exit_code": 1,
        "errors": ["files_mismatch: app.js"]})
    assert wkb.collect_version_all_match(ctx)["status"] == wkb.STATUS_FAIL
    monkeypatch.setattr(wkb, "_run_release_guard", lambda ctx: {
        **_fake_guard_ok(), "ok": False, "exit_code": 2, "errors": ["release_root_missing"]})
    assert wkb.collect_version_all_match(ctx)["status"] == wkb.STATUS_UNKNOWN


def test_e2e_artifact_pass_warn_fail(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    ctx = make_ctx(repo, tmp_path, temp_dir=tmp)
    m = wkb.collect_e2e_artifact(ctx)
    assert m["status"] == wkb.STATUS_PASS
    # 无日志 → unknown（不伪造 pass）
    m2 = wkb.collect_e2e_artifact(make_ctx(repo, tmp_path, temp_dir=tmp_path / "empty"))
    assert m2["status"] == wkb.STATUS_UNKNOWN
    # 未入 gate → fail
    repo3 = make_repo(tmp_path / "r3", gate_ps1="$paths = @{\n'portal' = @()\n}\n")
    m3 = wkb.collect_e2e_artifact(make_ctx(repo3, tmp_path, temp_dir=tmp))
    assert m3["status"] == wkb.STATUS_FAIL


def test_d1_weekly_doing(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path, tasks=_tasks_payload(doing=2, d1_doing=4))
    m = wkb.collect_d1_weekly(make_ctx(repo, tmp_path))
    assert m["value"] == 4 and m["status"] == wkb.STATUS_FAIL


def test_outcome_hit_contract_metric(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path, outcome_rows=[{"hit": None}, {"hit": 1}])
    m = wkb.collect_outcome_hit(make_ctx(repo, tmp_path))
    # legacy 行有 hit 但无 decision_id → 契约口径 0 → fail（不拿 legacy 充数）
    assert m["value"] == 0 and m["status"] == wkb.STATUS_FAIL


def test_acad1_candidates_dedupe(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    m = wkb.collect_acad1_candidates(make_ctx(repo, tmp_path))
    assert m["value"] == 1 and m["status"] == wkb.STATUS_PASS
    assert m["detail"]["candidates"][0]["versions"] == ["v1", "v2"]


def test_fast_gate_coverage_dynamic(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    m = wkb.collect_fast_gate_coverage(make_ctx(repo, tmp_path))
    assert m["status"] == wkb.STATUS_PASS
    assert m["value"] >= wkb.TARGET_FAST_GATE_COVERAGE_PCT
    assert "非 coverage.py" in m["evidence"]


def test_bundle_commits_growth_fails(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)

    def fake_bundle(ctx, since):
        if since >= wkb.PLAN_V2_PUBLISHED:
            return [{"commit": "abc123", "date": "2026-10-31 12:00",
                     "subject": "chore(ide): auto rebuild agent_ide_bundle after merge X"}]
        return [{"commit": "abc123", "date": "2026-10-31 12:00",
                 "subject": "chore(ide): auto rebuild agent_ide_bundle after merge X"}]

    monkeypatch.setattr(wkb, "_git_bundle_window", fake_bundle)
    m = wkb.collect_bundle_commits(make_ctx(repo, tmp_path))
    assert m["status"] == wkb.STATUS_FAIL and m["value"] == 1


# ----------------------------------------------------------------------
# 聚合 / 渲染 / 落盘 / CLI
# ----------------------------------------------------------------------
def test_build_board_all_pass_exit0(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    board = wkb.build_board(repo, DATE, now=NOW, temp_dir=tmp)
    assert board["exit_code"] == 0 and board["verdict"] == "PASS"
    assert board["summary"][wkb.STATUS_FAIL] == 0
    assert board["summary"][wkb.STATUS_UNKNOWN] == 0
    assert len(board["metrics"]) == 12
    assert board["schema"] == wkb.SCHEMA


def test_build_board_fail_exit1(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path, tasks=_tasks_payload(doing=25),
                     outcome_rows=[{"decision_id": "", "hit": None}])
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    board = wkb.build_board(repo, DATE, now=NOW, temp_dir=tmp)
    assert board["exit_code"] == 1 and board["verdict"] == "FAIL"
    fails = [m["key"] for m in board["metrics"] if m["status"] == wkb.STATUS_FAIL]
    assert "devtask_open" in fails and "outcome_hit" in fails


def test_build_board_unknown_exit2(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    (repo / ".worktrees" / "tasks.json").unlink()  # 关键账本缺失 → 多个 unknown
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    board = wkb.build_board(repo, DATE, now=NOW, temp_dir=tmp)
    assert board["exit_code"] == 2 and board["verdict"] == "UNKNOWN"
    assert board["summary"][wkb.STATUS_FAIL] == 0
    unknown = [m["key"] for m in board["metrics"] if m["status"] == wkb.STATUS_UNKNOWN]
    assert "devtask_open" in unknown and "d1_weekly" in unknown
    assert any("输入缺失" in m["evidence"] for m in board["metrics"]
               if m["key"] == "devtask_open")


def test_build_board_single_collector_crash_is_unknown(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)

    def boom(ctx):
        raise RuntimeError("boom")

    monkeypatch.setattr(wkb, "collect_taskstore_doing", boom)
    # 直接注入 COLLECTORS（模拟单指标崩溃被兜底）
    collectors = [(k, t, (boom if k == "taskstore_doing" else fn))
                  for k, t, fn in wkb.COLLECTORS]
    monkeypatch.setattr(wkb, "COLLECTORS", collectors)
    board = wkb.build_board(repo, DATE, now=NOW, temp_dir=tmp)
    m = next(x for x in board["metrics"] if x["key"] == "taskstore_doing")
    assert m["status"] == wkb.STATUS_UNKNOWN and "boom" in m["evidence"]
    assert board["exit_code"] == 2


def test_render_markdown_sections(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    board = wkb.build_board(repo, DATE, now=NOW, temp_dir=tmp)
    md = wkb.render_markdown(board)
    for token in ("周日 KPI 看板", "devtask open", "missing_worktree",
                  "fast gate 覆盖", "复跑与口径说明", "0 增长"):
        assert token in md
    assert "| PASS | WARN | FAIL | UNKNOWN |" in md


def test_write_outputs_names_and_json(tmp_path, monkeypatch):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    board = wkb.build_board(repo, DATE, now=NOW, temp_dir=tmp)
    out = tmp_path / "deliverables"
    md_path, json_path = wkb.write_outputs(board, repo, str(out))
    assert md_path.name == "2026-11-01_周日KPI看板_v1.md"
    assert json_path.name == "2026-11-01_周日KPI看板_v1.json"
    assert md_path.is_file() and json_path.is_file()
    reloaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert reloaded["schema"] == wkb.SCHEMA and len(reloaded["metrics"]) == 12


def test_cli_no_write_json(tmp_path, monkeypatch, capsys):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    rc = wkb.main(["--repo", str(repo), "--date", DATE, "--no-write",
                   "--json", "--temp-dir", str(tmp)])
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["schema"] == wkb.SCHEMA
    assert not (repo / "deliverables").exists()


def test_cli_writes_files(tmp_path, monkeypatch, capsys):
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)
    tmp = tmp_path / "ge"
    _write_gate_log(tmp)
    out_dir = tmp_path / "out"
    rc = wkb.main(["--repo", str(repo), "--date", DATE, "--out", str(out_dir),
                   "--temp-dir", str(tmp)])
    capsys.readouterr()
    assert rc == 0
    assert (out_dir / ("%s_周日KPI看板_v1.md" % DATE)).is_file()
    assert (out_dir / ("%s_周日KPI看板_v1.json" % DATE)).is_file()


def test_cli_bad_inputs_exit2(tmp_path, monkeypatch, capsys):
    """关键输入缺失（tasks/台账/outcome/gate 配置）→ 无 fail、多 unknown → 退出码 2。"""
    _patch_ok(monkeypatch)
    repo = make_repo(tmp_path)  # 先建全量假仓库，再移除关键输入

    for rel in (".worktrees/tasks.json",
                "docs/plans/PLAN_REGISTER_ALL_20261031.json",
                "agent_platform/data/shadow/decision_outcomes.jsonl",
                "agent_platform/data/org/tasks/tasks_202610.jsonl",
                "tools/test_gate.ps1"):
        (repo / rel).unlink()
    for d in ("agent_platform/data/org/tasks",):
        (repo / d).rmdir()
    rc = wkb.main(["--repo", str(repo), "--no-write",
                   "--temp-dir", str(tmp_path / "t")])
    capsys.readouterr()
    assert rc == 2
