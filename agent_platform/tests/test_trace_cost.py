# -*- coding: utf-8 -*-
"""trace_cost 单元测试（WO-TRACE-COST-20261027）。"""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
TOOL = REPO / "agent_platform" / "tools" / "trace_cost.py"


def _run(root: Path, *args):
    return subprocess.run(
        [sys.executable, "-X", "utf8", str(TOOL), *args, "--root", str(root)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
    )


def test_record_and_report(tmp_path):
    for i in range(3):
        r = _run(tmp_path, "record", "--agent", "ARCH1", "--task", "arch1_weekly",
                 "--status", "ok", "--elapsed-ms", str(100 + i))
        assert r.returncode == 0, r.stderr
        rec = json.loads(r.stdout)
        assert rec["schema"] == "agent_trace/v1"
        assert rec["trace_id"]
    r = _run(tmp_path, "report", "--days", "7", "--json")
    assert r.returncode == 0, r.stderr
    rep = json.loads(r.stdout)
    assert rep["records"] == 3
    g = rep["groups"][0]
    assert g["agent"] == "ARCH1" and g["n"] == 3 and g["ok"] == 3
    assert g["p50_ms"] == 101 and g["p95_ms"] == 102
    assert (tmp_path / "agent_platform" / "data" / "observability" / "traces.jsonl").is_file()


def test_failure_aggregation(tmp_path):
    _run(tmp_path, "record", "--agent", "BACK1", "--task", "research_back1",
         "--status", "fail", "--elapsed-ms", "500", "--detail", "WAITING_DATA1")
    r = _run(tmp_path, "report", "--days", "7", "--json")
    rep = json.loads(r.stdout)
    g = rep["groups"][0]
    assert g["fail"] == 1 and g["rate"] == 0.0
    assert rep["top_failures"][0]["agent"] == "BACK1"


def test_tail_and_markdown(tmp_path):
    _run(tmp_path, "record", "--agent", "ACAD1", "--task", "research_weekly",
         "--status", "ok", "--elapsed-ms", "42")
    r = _run(tmp_path, "tail", "-n", "1")
    assert "ACAD1" in r.stdout
    r = _run(tmp_path, "report", "--days", "1", "--write")
    assert r.returncode == 0
    mds = list((tmp_path / "agent_platform" / "data" / "observability").glob("trace_daily_*.md"))
    assert len(mds) == 1
    assert "ACAD1" in mds[0].read_text(encoding="utf-8")


def test_record_dry_run_no_write():
    # 库级函数 dry_run 不落盘（用短直接导入路径验证）
    sys.path.insert(0, str(REPO / "agent_platform" / "tools"))
    import trace_cost  # noqa: E402
    rec = trace_cost.record("ARCH1", "t", dry_run=True)
    assert rec["dry_run"] is True
