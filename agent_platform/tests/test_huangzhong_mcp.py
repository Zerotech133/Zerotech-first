# -*- coding: utf-8 -*-
"""huangzhong_mcp 单测（只读工具面；不触碰真实 Hermes / Windows 任务）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import huangzhong_mcp as R  # noqa: E402


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _make_work(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    work = repo / "work" / "huangzhong"
    (repo / "agent_platform" / "data" / "test_baseline").mkdir(parents=True)
    (repo / "portal" / "data").mkdir(parents=True)
    (repo / "work" / "p1" / "redteam").mkdir(parents=True)
    work.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    # 基线/红队
    (repo / "agent_platform" / "data" / "test_baseline" / "latest.json").write_text(
        json.dumps({"generated_at": _iso(now - timedelta(hours=1)),
                    "summary": {"passed": 2522, "failed": 0, "error": 0}}),
        encoding="utf-8")
    (repo / "work" / "p1" / "redteam" / "redteam_1.json").write_text(
        json.dumps({"generated_at": _iso(now - timedelta(hours=2)),
                    "pass_rate": 1.0}), encoding="utf-8")
    # 复核报告（含 ④）
    (work / "review_1.json").write_text(json.dumps({
        "generated_at": _iso(now - timedelta(minutes=5)),
        "summary": {"overall_pass": False, "passed": 5, "total": 6},
        "kpi": {"runs": 39, "merge_done": 1357, "coverage_rate": 0.0287},
        "items": [{"id": "①", "title": "样本量", "status": "ok", "measured": "41/10"},
                  {"id": "④", "title": "漏放=0", "status": "tracking",
                   "measured": "满窗 0/41 单"}]}, ensure_ascii=False), encoding="utf-8")
    # 验收单
    (work / "acceptance_abc12345.json").write_text(json.dumps({
        "verdict": "PASS", "commit": {"sha": "abc12345deadbeef"},
        "generated_at": _iso(now - timedelta(minutes=30)), "reasons": [],
        "evidence": {"baseline": {"age_hours": 1.0}, "redteam": {"pass_rate": 1.0}}}),
        encoding="utf-8")
    # 日更 + 事件
    rows = [{"ts": _iso(now - timedelta(hours=i)), "verdict": "PASS"}
            for i in range(3)]
    (work / "daily.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    evs = [{"ts": (now - timedelta(hours=i)).strftime("%Y-%m-%d %H:%M:%S"),
            "kind": "merge_done"} for i in range(40)]
    (repo / "portal" / "data" / "dev_events.jsonl").write_text(
        "\n".join(json.dumps(e) for e in evs), encoding="utf-8")
    return repo, work


def _patch(monkeypatch, repo: Path, work: Path):
    monkeypatch.setattr(R, "REPO", repo)
    monkeypatch.setattr(R, "WORK", work)


def test_initialize_and_tools_list():
    init = R.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert init["result"]["serverInfo"]["name"] == "huangzhong"
    lst = R.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    names = [t["name"] for t in lst["result"]["tools"]]
    assert names == ["huangzhong_status", "huangzhong_review_latest",
                     "huangzhong_evidence", "huangzhong_acceptance_latest",
                     "huangzhong_weekly_kpi", "huangzhong_tasks_health"]


def test_status_aggregate(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    res = R.call_tool("huangzhong_status", {})
    assert res["review"]["summary"]["passed"] == 5
    assert res["acceptance"]["verdict"] == "PASS"
    assert res["evidence"]["baseline"]["age_hours"] < 2
    assert res["evidence"]["ok"] is True
    assert res["leakage"]["status"] == "tracking"


def test_weekly_kpi(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    kpi = R.call_tool("huangzhong_weekly_kpi", {"window_days": 7})
    assert kpi["runs"] == 3 and kpi["merge_done"] == 40
    assert kpi["coverage_rate"] == round(3 / 40, 4)
    assert kpi["counts"]["PASS"] == 3


def test_evidence_stale(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    now = datetime.now(timezone.utc)
    (repo / "agent_platform" / "data" / "test_baseline" / "latest.json").write_text(
        json.dumps({"generated_at": _iso(now - timedelta(hours=72)),
                    "summary": {"failed": 0}}), encoding="utf-8")
    ev = R.call_tool("huangzhong_evidence", {})
    assert ev["baseline"]["stale"] is True
    assert ev["ok"] is False


def test_unknown_tool_error():
    resp = R.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                     "params": {"name": "nope", "arguments": {}}})
    assert resp["error"]["code"] == -32602


def test_empty_work_dir_fail_visible(tmp_path, monkeypatch):
    repo = tmp_path / "empty"
    work = repo / "work" / "huangzhong"
    work.mkdir(parents=True)
    _patch(monkeypatch, repo, work)
    res = R.call_tool("huangzhong_review_latest", {})
    assert res["ok"] is False and "error" in res


# ── Phase 2：受控写工具（触发面）──────────────────────────────────────────

class _FakeCmdResult:
    def __init__(self, rc=0, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def _list_tools():
    resp = R.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [t["name"] for t in resp["result"]["tools"]]


def test_write_tools_hidden_by_default(monkeypatch):
    monkeypatch.setattr(R, "WRITE_ENABLED", False)
    names = _list_tools()
    assert len(names) == 6
    assert "huangzhong_run_review" not in names


def test_write_tools_visible_when_enabled(monkeypatch):
    monkeypatch.setattr(R, "WRITE_ENABLED", True)
    names = _list_tools()
    assert len(names) == 8
    assert "huangzhong_run_review" in names and "huangzhong_run_acceptance" in names


def _prepare_write_env(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    monkeypatch.setattr(R, "WRITE_ENABLED", True)
    calls = []

    def fake_cmd(cmd, timeout=180):
        calls.append(cmd)
        return _FakeCmdResult(0, "{}")

    monkeypatch.setattr(R, "_run_tool_cmd", fake_cmd)
    monkeypatch.setattr(R, "_LAST_RUN", {"review": 0.0, "accept": 0.0})
    return repo, calls


def _call(name, **args):
    resp = R.handle({"jsonrpc": "2.0", "id": 9, "method": "tools/call",
                     "params": {"name": name, "arguments": args}})
    return resp


def test_run_review_requires_confirm_and_audits(tmp_path, monkeypatch):
    repo, calls = _prepare_write_env(tmp_path, monkeypatch)
    resp = _call("huangzhong_run_review", confirm="RUN-REVIEW")
    assert resp["result"]["isError"] is False
    assert calls and "huangzhong_review.py" in str(calls[0])
    ev = (repo / "portal" / "data" / "dev_events.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert any("code_action" in line and "huangzhong_run_review" in line for line in ev)
    # 错口令 → 拒绝
    bad = _call("huangzhong_run_review", confirm="WRONG")
    assert bad["result"]["isError"] is True
    # 60s 限频 → 第二次拒绝（第一次已刷新 _LAST_RUN）
    again = _call("huangzhong_run_review", confirm="RUN-REVIEW")
    assert again["result"]["isError"] is True
    assert "限频" in again["result"]["content"][0]["text"]


def test_run_acceptance_ok(tmp_path, monkeypatch):
    _prepare_write_env(tmp_path, monkeypatch)
    resp = _call("huangzhong_run_acceptance", confirm="RUN-ACCEPT")
    assert resp["result"]["isError"] is False
    payload = json.loads(resp["result"]["content"][0]["text"])
    assert payload["ok"] is True
    assert payload["acceptance"]["verdict"] == "PASS"


def test_write_tools_disabled_blocks_call(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    monkeypatch.setattr(R, "WRITE_ENABLED", False)
    resp = _call("huangzhong_run_review", confirm="RUN-REVIEW")
    assert resp["result"]["isError"] is True
    assert "disabled" in resp["result"]["content"][0]["text"]
