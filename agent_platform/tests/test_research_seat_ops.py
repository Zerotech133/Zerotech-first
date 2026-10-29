# -*- coding: utf-8 -*-
"""研究值守双席测试（WO-LEGION-ACAD1-P2）。

覆盖：STRAT1 假设值守简报 / BACK1 冻结值守（WAITING_DATA1 → 回执齐后 READY）/
双席周报 / dry-run 零写 / 缺真源退出码 2。全部 --no-emit，避免污染真实事件层。
"""

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "research_seat_ops.py"
FREEZE_FIELDS = ("data_version", "sample_in", "sample_out", "cost_model")


def _mkroot(tmp_path: Path) -> Path:
    academy = tmp_path / "agent_platform" / "data" / "academy"
    academy.mkdir(parents=True)
    topics = [
        {
            "topic_id": "AC-TEST-LCS1",
            "title": "测试策略课题",
            "direction": "strategy_lab",
            "owner": "STRAT1",
            "status": "hypothesis",
            "question": "问题",
            "object": "对象",
            "hypothesis": "假设",
            "evidence_scope": "口径",
            "method": "回测",
            "conclusion": "待验证",
            "action": "补证",
            "risks": "风险",
        }
    ]
    (academy / "research_topics.json").write_text(
        json.dumps(topics, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (academy / "backtests.jsonl").write_text(
        json.dumps(
            {
                "topic_id": "AC-TEST-LCS1",
                "status": "queued",
                "owner": "BACK1",
                "strategy": "LCS-1",
                "symbol": "600337",
                "data_version": "待DATA1冻结",
                "sample_in": "待登记",
                "sample_out": "待登记",
                "cost_model": "待登记",
                "go_no_go": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    return tmp_path


def _run(root: Path, action: str, *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(TOOL),
            action,
            "--root",
            str(root),
            "--no-emit",
            *extra,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write_receipt(root: Path) -> None:
    receipts = root / "agent_platform" / "data" / "academy" / "freeze_receipts"
    receipts.mkdir(parents=True, exist_ok=True)
    payload = {
        "topic_id": "AC-TEST-LCS1",
        "data_version": "ts_daily@2026-10-26#sha256:abc",
        "sample_in": "2025-01-01~2025-12-31 CN 500",
        "sample_out": "2026-01-01~2026-06-30 CN 500",
        "cost_model": "fee:v1 slip:v1 impact:v1",
        "sha256": "abc",
        "frozen_at": "2026-10-27T09:00:00+08:00",
        "by": "DATA1",
    }
    (receipts / "AC-TEST-LCS1.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _add_dispatch(root: Path, suggested: str = "WO-LCS1-HARNESS-20261027") -> str:
    d = root / "agent_platform" / "data" / "academy" / "dispatch_requests"
    d.mkdir(parents=True, exist_ok=True)
    payload = {
        "request_id": "WO-REQ-TEST",
        "target": "ARCH1",
        "suggested_ticket": suggested,
        "requirement_doc": "deliverables/req.md",
        "status": "pending_dispatch",
        "created_at": "2026-10-27T11:00:00+08:00",
    }
    (d / "WO-REQ-TEST.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return suggested


def test_strat1_brief(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "strat1")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "ops" / "strat1").glob(
            "*_brief.md"
        )
    )
    assert len(files) == 1
    text = _read(files[0])
    assert "策略假设值守" in text
    assert "AC-TEST-LCS1" in text


def test_back1_waiting_data1(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "back1")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "ops" / "back1").glob(
            "*_brief.md"
        )
    )
    assert len(files) == 1
    text = _read(files[0])
    assert "WAITING_DATA1" in text
    assert "DATA1 冻结回执模板" in text
    for f in FREEZE_FIELDS:
        assert f in text


def test_back1_ready_after_receipt(tmp_path):
    root = _mkroot(tmp_path)
    _write_receipt(root)
    proc = _run(root, "back1")
    assert proc.returncode == 0, proc.stderr
    # 回执齐但登记四字段仍为“待*” → 仍 WAITING（冻结以登记真源为准）
    text = _read(
        list(
            (root / "agent_platform" / "data" / "academy" / "ops" / "back1").glob(
                "*_brief.md"
            )
        )[0]
    )
    assert "WAITING_DATA1" in text


def test_back1_ready_when_fields_and_receipt(tmp_path):
    root = _mkroot(tmp_path)
    # 登记四字段齐 + 回执在 → READY
    bt = root / "agent_platform" / "data" / "academy" / "backtests.jsonl"
    row = {
        "topic_id": "AC-TEST-LCS1",
        "status": "queued",
        "owner": "BACK1",
        "strategy": "LCS-1",
        "symbol": "600337",
        "data_version": "ts_daily@2026-10-26#sha256:abc",
        "sample_in": "2025-01-01~2025-12-31 CN 500",
        "sample_out": "2026-01-01~2026-06-30 CN 500",
        "cost_model": "fee:v1 slip:v1 impact:v1",
        "go_no_go": "pending",
    }
    bt.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_receipt(root)
    proc = _run(root, "back1")
    assert proc.returncode == 0, proc.stderr
    text = _read(
        list(
            (root / "agent_platform" / "data" / "academy" / "ops" / "back1").glob(
                "*_brief.md"
            )
        )[0]
    )
    assert "READY" in text
    assert "可开跑" in text


def test_back1_dispatch_pending(tmp_path):
    root = _mkroot(tmp_path)
    suggested = _add_dispatch(root)
    proc = _run(root, "back1")
    assert proc.returncode == 0, proc.stderr
    text = _read(
        list(
            (root / "agent_platform" / "data" / "academy" / "ops" / "back1").glob(
                "*_brief.md"
            )
        )[0]
    )
    assert "派单跟踪" in text
    assert "pending_dispatch" in text
    assert suggested in text
    assert "pending=1 / dispatched=0" in text


def test_back1_dispatch_detected(tmp_path):
    root = _mkroot(tmp_path)
    suggested = _add_dispatch(root)
    wt = root / ".worktrees"
    wt.mkdir(parents=True)
    tasks = {suggested: {"desc": "LCS-1 harness 实施（需求 WO-REQ-TEST）",
                          "owner": "BACK1", "status": "doing"}}
    (wt / "tasks.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    proc = _run(root, "back1")
    assert proc.returncode == 0, proc.stderr
    text = _read(
        list(
            (root / "agent_platform" / "data" / "academy" / "ops" / "back1").glob(
                "*_brief.md"
            )
        )[0]
    )
    assert "dispatched" in text
    assert suggested in text
    assert "BACK1" in text
    assert "pending=0 / dispatched=1" in text


def test_back1_overdue_doing_escalation(tmp_path):
    root = _mkroot(tmp_path)
    wt = root / ".worktrees"
    wt.mkdir()
    old = (datetime.now() - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S")
    new = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    (wt / "tasks.json").write_text(
        json.dumps(
            {
                "WO-OLD": {"status": "doing", "owner": "DATA1", "created": old,
                           "desc": "冻结回执"},
                "WO-NEW": {"status": "doing", "owner": "BACK1", "created": new,
                           "desc": "新单"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    proc = _run(root, "back1")
    assert proc.returncode == 0, proc.stderr
    brief = next(
        (root / "agent_platform" / "data" / "academy" / "ops" / "back1").glob(
            "*_brief.md"
        )
    )
    text = _read(brief)
    assert "超期 doing" in text
    assert "WO-OLD" in text
    assert "WO-NEW" not in text
    assert "ESCALATE" in text


def test_weekly_both(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "ops" / "weekly").glob(
            "*_weekly.md"
        )
    )
    assert len(files) == 1
    text = _read(files[0])
    assert "STRAT1 摘要" in text
    assert "BACK1 摘要" in text
    assert "WAITING_DATA1" in text


def test_dry_run_zero_write(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "back1", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["dry_run"] is True
    assert not (root / "agent_platform" / "data" / "academy" / "ops").exists()


def test_missing_registry_exit_2(tmp_path):
    proc = _run(tmp_path, "strat1")
    assert proc.returncode == 2
    assert "research_topics.json missing" in proc.stdout
