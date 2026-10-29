# -*- coding: utf-8 -*-
"""研究院运营三件套测试（WO-LEGION-ACAD1-P0）。

覆盖：intake/review/weekly 产出文件与内容 / --dry-run 零写 / 缺真源退出码 2 /
事件层新 kind 已登记（research_intake/research_review/research_report）。
全部用例走 --no-emit，避免污染真实 dev_events。
"""

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "academy_ops.py"
NEW_KINDS = {"research_intake", "research_review", "research_report"}


def _mkroot(tmp_path: Path) -> Path:
    academy = tmp_path / "agent_platform" / "data" / "academy"
    academy.mkdir(parents=True)
    topics = [
        {
            "topic_id": "AC-TEST-1",
            "title": "测试课题",
            "direction": "strategy_lab",
            "owner": "STRAT1",
            "sponsor": "D1",
            "question": "问题",
            "object": "对象",
            "hypothesis": "假设",
            "evidence_scope": "口径",
            "method": "回测",
            "conclusion": "待验证",
            "action": "补证",
            "risks": "风险",
            "status": "intake",
            "updated": "2026-10-27T00:00:00",
        },
        {
            "topic_id": "AC-TEST-2",
            "title": "待评审课题",
            "direction": "commercialization_lab",
            "owner": "COMM1",
            "status": "review",
            "conclusion": "暂定结论",
            "decision": "",
        },
    ]
    (academy / "research_topics.json").write_text(
        json.dumps(topics, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (academy / "backtests.jsonl").write_text(
        json.dumps(
            {
                "topic_id": "AC-TEST-1",
                "status": "queued",
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


def test_events_kinds_registered():
    sys.path.insert(0, str(REPO / "tools"))
    try:
        import events  # noqa: WPS433

        assert NEW_KINDS.issubset(set(events.KINDS))
    finally:
        if str(REPO / "tools") in sys.path:
            sys.path.remove(str(REPO / "tools"))


def test_intake_writes_brief(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "intake")
    assert proc.returncode == 0, proc.stderr
    out = root / "agent_platform" / "data" / "academy" / "briefs"
    files = list(out.glob("*_intake.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "AC-TEST-1" in text
    assert "立项简报" in text


def test_dry_run_zero_write(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["dry_run"] is True
    assert not (root / "agent_platform" / "data" / "academy" / "reports").exists()


def test_review_lists_pending(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "review")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "reviews").glob("*_review.md")
    )
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "AC-TEST-2" in text
    assert "评审检查表" in text
    # 数据冻结跟踪（P1）：四字段未冻结须显形，不得默认已冻结
    assert "数据冻结跟踪" in text
    assert "待DATA1冻结" in text
    assert "0/1 已冻结" in text
    assert "data_version" in text and "cost_model" in text


def test_weekly_contains_freeze_progress(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "reports").glob("*_weekly.md")
    )
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "数据冻结" in text
    assert "0/1 已冻结" in text


def test_weekly_contains_kpi(tmp_path):
    root = _mkroot(tmp_path)
    proc = _run(root, "weekly")
    assert proc.returncode == 0, proc.stderr
    files = list(
        (root / "agent_platform" / "data" / "academy" / "reports").glob("*_weekly.md")
    )
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "KPI 对照" in text
    assert "AC-TEST-1" not in text or "课题在册" in text


def test_missing_registry_exit_2(tmp_path):
    proc = _run(tmp_path, "intake")
    assert proc.returncode == 2
    assert "research_topics.json missing" in proc.stdout


def test_review_includes_strategy_portfolio(tmp_path):
    root = _mkroot(tmp_path)
    academy = root / "agent_platform" / "data" / "academy"
    (academy / "strategy_portfolio.json").write_text(json.dumps({
        "schema": "academy_strategy_portfolio/v1",
        "strategies": [
            {"id": "qpzm", "name": "青萍之末", "health": "green", "health_evidence": "RpsDaily rc=0"},
            {"id": "strong", "name": "板块强弱", "health": "red", "health_evidence": "rc=3"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    proc = _run(root, "review")
    assert proc.returncode == 0, proc.stderr
    files = list((academy / "reviews").glob("*_review.md"))
    text = files[0].read_text(encoding="utf-8")
    assert "策略组合" in text
    assert "strong" in text
    assert "待处理" in text and "rc=3" in text
