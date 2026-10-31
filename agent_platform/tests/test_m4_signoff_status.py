# -*- coding: utf-8 -*-
"""M4 签收状态只读看板 离线测试（WO-M4-SIGNOFF-STATUS-20261031）。

覆盖：complete（签字齐+证据齐）/ incomplete（缺签）/ 坏配置（坏 JSON、缺文件）/
缺证据 / 角色映射缺失 / annotate 引用试点点击不计签 / 默认 task 与台账候选 /
只读（不改 approvals/配置、--no-write 零写盘）/ 退出码 0-1-2。

隔离纪律：所有台账/配置/证据夹具在 tmp_path，不触生产 approvals.jsonl、
不改签字、不联网。
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

WT = Path(__file__).resolve().parents[2]
_TOOL = WT / "tools" / "m4_signoff_status.py"
_spec = importlib.util.spec_from_file_location("m4_signoff_status", str(_TOOL))
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

TASK = mod.DEFAULT_TASK
ROLES = ["QA1", "GUANYU", "VERIFY1", "A0"]
OPERATORS = {"ou_qa1": "QA1", "ou_guanyu": "GUANYU",
             "ou_verify1": "VERIFY1", "ou_a0": "A0"}
EVIDENCE_FILES = {
    "deliverables/2026-10-31_M4独立复验与收口评估_v1.md":
        "# M4 独立复验与收口评估\n- 结论：技术验收 PASS；签字未完成\n",
    "work/audit-20261031/m4_tests.txt": "30 passed, 1 warning in 1.34s\n",
    "work/audit-20261031/m4_smoke.txt": "PASS：作战屏 v1 冒烟\n",
}


def _approval(operator, ts, task=TASK, action="approve"):
    return {"ts": ts, "schema": "approval/v1", "task": task,
            "action": action, "operator": operator}


def _write_ledger(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                            for r in rows), encoding="utf-8")
    return path


def _write_config(path, mapping):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"map": mapping, "note": "test"},
                               ensure_ascii=False), encoding="utf-8")
    return path


def _write_evidence(root):
    """造 3 份必检证据（m4_tests 故意写成 UTF-16，覆盖编码回退）。"""
    root = Path(root)
    for rel, content in EVIDENCE_FILES.items():
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.suffix == ".txt":
            target.write_bytes(content.encode("utf-16"))
        else:
            target.write_text(content, encoding="utf-8")
    return root


def _complete_ledger(tmp_path):
    return _write_ledger(tmp_path / "approvals.jsonl", [
        _approval(op, "2026-10-31T10:0%d:00" % i)
        for i, op in enumerate(OPERATORS)])


def _run(tmp_path, capsys, *extra, task=TASK):
    evidence = tmp_path / "evidence"
    ledger = tmp_path / "approvals.jsonl"
    config = tmp_path / "approval_signers.json"
    report_dir = tmp_path / "reports"
    rc = mod.main(["--ledger", str(ledger), "--config", str(config),
                   "--evidence-root", str(evidence), "--task", task,
                   "--report-dir", str(report_dir), "--json", *extra])
    out = capsys.readouterr().out
    report = json.loads(out) if out.strip() else None
    return rc, report, report_dir


# ---------------------------------------------------------------------------
# complete / incomplete
# ---------------------------------------------------------------------------

def test_complete_signoff_and_evidence(tmp_path, capsys):
    _complete_ledger(tmp_path)
    _write_config(tmp_path / "approval_signers.json", OPERATORS)
    _write_evidence(tmp_path / "evidence")

    rc, report, report_dir = _run(tmp_path, capsys)

    assert rc == 0
    assert report["verdict"] == "complete" and report["complete"] is True
    assert report["state"] == "complete"
    assert report["signed"] == ROLES
    assert report["missing"] == []
    assert report["signed_count"] == 4
    assert report["evidence_missing"] == []
    assert report["role_map"]["covered_roles"] == ROLES
    assert report["role_map"]["valid"] is True
    assert report["reasons"] == []
    # 证据摘要按编码回退读出（m4_tests 为 UTF-16）
    by_key = {e["key"]: e for e in report["evidence"]}
    assert by_key["m4_tests"]["exists"] is True
    assert "30 passed" in by_key["m4_tests"]["summary"]
    assert "PASS" in by_key["m4_smoke"]["summary"]
    assert by_key["m4_eval"]["sha16"] and by_key["m4_eval"]["mtime"]
    # 报告落盘 + 只读声明
    saved = json.loads((report_dir / report["report_path"].split("\\")[-1]
                        ).read_text(encoding="utf-8"))
    assert saved["verdict"] == "complete"
    assert report["read_only"] is True


def test_incomplete_missing_signatures(tmp_path, capsys):
    _write_ledger(tmp_path / "approvals.jsonl", [
        _approval("ou_qa1", "2026-10-31T10:00:00"),
        _approval("ou_guanyu", "2026-10-31T10:01:00"),
    ])
    _write_config(tmp_path / "approval_signers.json", OPERATORS)
    _write_evidence(tmp_path / "evidence")

    rc, report, _ = _run(tmp_path, capsys)

    assert rc == 1
    assert report["complete"] is False and report["signoff_complete"] is False
    assert report["missing"] == ["VERIFY1", "A0"]
    assert report["signed"] == ["QA1", "GUANYU"]
    assert any("签字未齐" in r for r in report["reasons"])
    # 证据本身没缺，缺的是签字
    assert report["evidence_missing"] == []


# ---------------------------------------------------------------------------
# 坏配置（不崩、不猜）
# ---------------------------------------------------------------------------

def test_bad_config_json_does_not_crash(tmp_path, capsys):
    _complete_ledger(tmp_path)
    bad = tmp_path / "approval_signers.json"
    bad.write_text("{bad json", encoding="utf-8")
    _write_evidence(tmp_path / "evidence")

    rc, report, _ = _run(tmp_path, capsys)

    assert rc == 1                      # 状态 incomplete，不是工具崩溃 2
    assert report["role_map"]["valid"] is False
    assert "bad_json" in report["role_map"]["error"]
    assert report["state"] == "incomplete"
    assert report["reason"] == "roles_unresolved"
    assert any("签字角色映射配置错误" in r for r in report["reasons"])


def test_missing_config_file_reports_unresolved(tmp_path, capsys):
    _complete_ledger(tmp_path)
    _write_evidence(tmp_path / "evidence")
    # 不写 config 文件
    rc, report, _ = _run(tmp_path, capsys)
    assert rc == 1
    assert report["role_map"]["exists"] is False
    assert "missing" in report["role_map"]["error"]
    assert report["reason"] == "roles_unresolved"


# ---------------------------------------------------------------------------
# 缺证据 / 角色映射缺失
# ---------------------------------------------------------------------------

def test_missing_required_evidence_incomplete(tmp_path, capsys):
    _complete_ledger(tmp_path)
    _write_config(tmp_path / "approval_signers.json", OPERATORS)
    (tmp_path / "evidence").mkdir()      # 空证据根 → 三份必检全缺

    rc, report, _ = _run(tmp_path, capsys)

    assert rc == 1
    assert report["signoff_complete"] is True   # 签字齐
    assert report["complete"] is False          # 但证据缺 → incomplete
    assert report["evidence_missing"] == ["m4_eval", "m4_tests", "m4_smoke"]
    assert any("必备证据缺失" in r for r in report["reasons"])


def test_role_mapping_missing_roles(tmp_path, capsys):
    _complete_ledger(tmp_path)
    _write_config(tmp_path / "approval_signers.json",
                  {"ou_qa1": "QA1", "ou_guanyu": "GUANYU"})
    _write_evidence(tmp_path / "evidence")

    rc, report, _ = _run(tmp_path, capsys)

    assert rc == 1
    assert report["role_map"]["covered_roles"] == ["QA1", "GUANYU"]
    assert report["role_map"]["missing_roles"] == ["VERIFY1", "A0"]
    assert report["missing"] == ["VERIFY1", "A0"]
    assert any("角色映射未覆盖" in w for w in report["warnings"])


# ---------------------------------------------------------------------------
# annotate 引用的试点点击不计签（复用 evaluate_signoff 语义）
# ---------------------------------------------------------------------------

def test_annotated_pilot_clicks_excluded(tmp_path, capsys):
    rows = [_approval(op, "2026-10-31T10:0%d:00" % i)
            for i, op in enumerate(OPERATORS)]
    rows.append({"ts": "2026-10-31T11:00:00", "schema": "approval/v1",
                 "task": TASK, "action": "annotate", "operator": "DSH-Lead",
                 "note": "试点点击不计签",
                 "refs": ["2026-10-31T10:00:00"]})
    _write_ledger(tmp_path / "approvals.jsonl", rows)
    _write_config(tmp_path / "approval_signers.json", OPERATORS)
    _write_evidence(tmp_path / "evidence")

    rc, report, _ = _run(tmp_path, capsys)

    assert rc == 1
    assert report["excluded_ts"] == ["2026-10-31T10:00:00"]
    assert report["signed_count"] == 3
    assert report["missing"] == ["QA1"]


# ---------------------------------------------------------------------------
# 默认 task / 台账候选任务
# ---------------------------------------------------------------------------

def test_available_tasks_and_unknown_task_warning(tmp_path, capsys):
    _write_ledger(tmp_path / "approvals.jsonl", [
        _approval("ou_qa1", "2026-10-31T10:00:00"),
        _approval("ou_guanyu", "2026-10-31T10:01:00"),
        _approval("ou_x", "2026-10-30T10:00:00", task="M4/v1.1 验收签字（试点）"),
        _approval("DSH-Lead", "2026-10-30T11:00:00", task="M4/v1.1",
                  action="annotate"),
    ])
    _write_config(tmp_path / "approval_signers.json", OPERATORS)
    _write_evidence(tmp_path / "evidence")

    rc, report, _ = _run(tmp_path, capsys)
    assert rc == 1
    available = report["approvals"]["available_m4_tasks"]
    assert available[TASK] == 2
    assert available["M4/v1.1 验收签字（试点）"] == 1
    assert available["M4/v1.1"] == 1

    # 选错 task：断言给出候选提示（不静默空）
    rc2, report2, _ = _run(tmp_path, capsys, task="不存在的 M4 任务")
    assert rc2 == 1
    assert report2["approvals"]["task_rows"] == 0
    assert any("台账候选 M4 任务" in w for w in report2["warnings"])


# ---------------------------------------------------------------------------
# 只读红线 / --no-write / 退出码 2
# ---------------------------------------------------------------------------

def test_read_only_and_no_write(tmp_path, capsys):
    ledger = _complete_ledger(tmp_path)
    config = _write_config(tmp_path / "approval_signers.json", OPERATORS)
    evidence = _write_evidence(tmp_path / "evidence")
    before = (ledger.read_bytes(), config.read_bytes(),
              (evidence / "work/audit-20261031/m4_tests.txt").read_bytes())

    rc, report, report_dir = _run(tmp_path, capsys, "--no-write")

    assert rc == 0
    assert report["read_only"] is True
    assert not report_dir.exists(), "--no-write 不得创建报告目录/文件"
    after = (ledger.read_bytes(), config.read_bytes(),
             (evidence / "work/audit-20261031/m4_tests.txt").read_bytes())
    assert before == after, "只读工具不得改动 approvals/配置/证据"


def test_internal_error_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mod, "build_report",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    rc = mod.main(["--ledger", str(tmp_path / "a.jsonl"),
                   "--config", str(tmp_path / "c.json"),
                   "--evidence-root", str(tmp_path), "--json"])
    data = json.loads(capsys.readouterr().out)
    assert rc == 2
    assert data["ok"] is False and "boom" in data["detail"]


def test_bad_args_exit_2(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        mod.main(["--date", "2026/10/31", "--json"])
    assert exc_info.value.code == 2
    with pytest.raises(SystemExit) as exc_info2:
        mod.main(["--required", " , ", "--json"])
    assert exc_info2.value.code == 2


# ---------------------------------------------------------------------------
# 证据查找顺序：多根时按顺序命中；缺失时给期望路径但不伪造
# ---------------------------------------------------------------------------

def test_evidence_lookup_order_and_missing_path(tmp_path):
    root_a = tmp_path / "root_a"
    root_b = tmp_path / "root_b"
    (root_b / "work/audit-20261031").mkdir(parents=True)
    (root_b / "work/audit-20261031/m4_tests.txt").write_text(
        "30 passed\n", encoding="utf-8")

    entries, missing = mod.collect_evidence([root_a, root_b])

    by_key = {e["key"]: e for e in entries}
    assert by_key["m4_tests"]["exists"] is True
    assert by_key["m4_tests"]["root"] == str(root_b)
    assert by_key["m4_eval"]["exists"] is False
    assert by_key["m4_eval"]["path"].startswith(str(root_a))
    assert "m4_eval" in missing and "m4_tests" not in missing


def test_default_evidence_roots_cover_platform_root():
    """默认证据根必须覆盖平台根（C:\\项目）：worktree 内跑也能看到 M4 证据。"""
    roots = mod.default_evidence_roots()
    assert roots[0] == mod.REPO
    parts = mod.REPO.parts
    if ".worktrees" in parts:
        main_repo = Path(*parts[:parts.index(".worktrees")])
    else:
        main_repo = mod.REPO
    assert main_repo in roots
    assert main_repo.parent in roots
