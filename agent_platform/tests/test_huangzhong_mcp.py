# -*- coding: utf-8 -*-
"""huangzhong_mcp 单测（只读工具面；不触碰真实 Hermes / Windows 任务）。"""
from __future__ import annotations

import json
import subprocess
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
                     "huangzhong_weekly_kpi", "huangzhong_tasks_health",
                     "huangzhong_health", "huangzhong_preflight",
                     "huangzhong_brief"]


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
    assert len(names) == 9
    assert "huangzhong_run_review" not in names


def test_write_tools_visible_when_enabled(monkeypatch):
    monkeypatch.setattr(R, "WRITE_ENABLED", True)
    names = _list_tools()
    assert len(names) == 11
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


# ── P2-C：新增 3 只读工具（health / preflight / brief）─────────────────────

HEALTH_PAYLOAD = {
    "ok": True, "tool": "huangzhong_health", "version": "v1",
    "status": "ok",
    "summary": {"ok": 7, "warn": 0, "fail": 0, "skipped": 0, "total": 7},
    "checks": [
        {"id": "acceptance_latest", "name": "最新验收单 age", "status": "ok",
         "detail": "age=28.4 min；阈值 ≤90 min"},
        {"id": "review_latest", "name": "最新复核报告 age", "status": "ok",
         "detail": "age=12.0 min"}],
    "warnings": [],
    "generated_at": "2026-10-31T19:00:00+08:00",
    "read_only": True,
}
PREFLIGHT_PAYLOAD = {
    "ok": True, "tool": "huangzhong_1104_preflight.py",
    "ready": False,
    "blockers": [{"check": "evidence", "message": "基线证据超龄 72h > 48h"}],
    "warnings": ["④ 满窗样本不足"],
    "checks": [{"id": "1", "key": "evidence", "name": "证据新鲜度（48h 认证门）",
                "status": "✗", "result": "基线：超龄 72h"}],
    "generated_at": "2026-10-31T19:00:00+08:00",
    "read_only": True,
}
BRIEF_PAYLOAD = {
    "ok": True, "tool": "huangzhong_brief.py",
    "conclusion": "六条 5/6 ✓；待办：④；近7天事件 18 条（PASS 18 / REJECT 0 / OBSERVE 0）",
    "generated_at": "2026-10-31T19:00:00+08:00", "date": "2026-10-31", "days": 7,
    "kpi": {"runs": 65, "merge_done": 1540, "coverage": 0.0422,
            "counts": {"PASS": 65, "REJECT": 0, "OBSERVE": 0}},
    "events": {"status": "✓", "total": 18,
               "counts": {"PASS": 18, "REJECT": 0, "OBSERVE": 0},
               "bad_lines": 0, "window_days": 7},
    "review": {"status": "✓", "generated_at": "2026-10-31T18:30:00+08:00",
               "age_hours": 0.5,
               "summary": {"overall_pass": False, "passed": 5, "total": 6}},
    "acceptance": {"status": "✓", "verdict": "PASS", "commit": "abc123456789",
                   "generated_at": "2026-10-31T18:45:00+08:00", "age_hours": 0.48},
    "push_state": {"enabled": True, "state": "ON", "source": "file",
                   "updated_at": "2026-10-31T14:41:57+08:00", "by": "DSH-Lead"},
    "incidents": {"status": "✓", "rows": 0, "recent_window": 0, "latest": None},
    "warnings": [],
}


def _fake_tool_cmd(monkeypatch, payload, rc=0):
    """注入 _run_tool_cmd：返回给定 payload（dict → JSON 文本；str 原样）。"""
    calls = []

    def fake(cmd, timeout=180):
        calls.append({"cmd": list(cmd), "timeout": timeout})
        text = payload if isinstance(payload, str) else json.dumps(
            payload, ensure_ascii=False)
        return _FakeCmdResult(rc, text)

    monkeypatch.setattr(R, "_run_tool_cmd", fake)
    return calls


def test_health_tool_summary(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    calls = _fake_tool_cmd(monkeypatch, HEALTH_PAYLOAD)
    out = R.call_tool("huangzhong_health", {})
    assert out["ok"] is True and out["status"] == "ok"
    assert out["summary"]["total"] == 7 and out["checks_total"] == 2
    assert out["checks"][0]["id"] == "acceptance_latest"
    assert out["read_only"] is True and out["run"]["rc"] == 0
    cmd = calls[0]["cmd"]
    assert any("huangzhong_health.py" in part for part in cmd)
    assert "--json" in cmd
    assert calls[0]["timeout"] == R.TOOL_RUN_TIMEOUT_S


def test_preflight_tool_summary(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    calls = _fake_tool_cmd(monkeypatch, PREFLIGHT_PAYLOAD)
    out = R.call_tool("huangzhong_preflight", {})
    assert out["ok"] is True
    assert out["ready"] is False          # 未就绪≠执行失败，数据有效
    assert out["blockers_total"] == 1
    assert out["blockers"][0]["check"] == "evidence"
    assert "超龄" in out["blockers"][0]["message"]
    assert out["warnings_total"] == 1 and "满窗" in out["warnings"][0]["message"]
    assert out["checks"][0]["id"] == "1"
    assert any("huangzhong_1104_preflight.py" in part for part in calls[0]["cmd"])


def test_brief_tool_summary(tmp_path, monkeypatch):
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    calls = _fake_tool_cmd(monkeypatch, BRIEF_PAYLOAD)
    out = R.call_tool("huangzhong_brief", {})
    assert out["ok"] is True
    assert "六条 5/6" in out["conclusion"]
    assert out["kpi"]["runs"] == 65
    assert out["events"]["counts"]["PASS"] == 18
    assert out["review"]["summary"]["passed"] == 5
    assert out["acceptance"]["verdict"] == "PASS"
    assert out["push_state"]["state"] == "ON"
    assert any("huangzhong_brief.py" in part for part in calls[0]["cmd"])


def test_new_readonly_tools_listed_no_confirm(monkeypatch):
    """新增 3 工具在 tools/list 中、无 confirm 必填（只读）。"""
    monkeypatch.setattr(R, "WRITE_ENABLED", False)
    resp = R.handle({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
    tools = {t["name"]: t for t in resp["result"]["tools"]}
    for name in ("huangzhong_health", "huangzhong_preflight", "huangzhong_brief"):
        assert name in tools
        assert tools[name]["inputSchema"]["properties"] == {}
        assert "required" not in tools[name]["inputSchema"]
    assert len(tools) == 9


def test_new_readonly_tools_fail_visible(tmp_path, monkeypatch):
    """子进程超时/非 JSON/自报 ok=false → isError=True（fail-visible，不编造）。"""
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    names = ("huangzhong_health", "huangzhong_preflight", "huangzhong_brief")

    def timeout(cmd, timeout=180):
        raise subprocess.TimeoutExpired(cmd, timeout)

    monkeypatch.setattr(R, "_run_tool_cmd", timeout)
    for name in names:
        resp = _call(name)
        assert resp["result"]["isError"] is True
        assert "超时" in resp["result"]["content"][0]["text"]

    monkeypatch.setattr(R, "_run_tool_cmd",
                        lambda cmd, timeout=180: _FakeCmdResult(0, "not json"))
    for name in names:
        assert _call(name)["result"]["isError"] is True

    monkeypatch.setattr(R, "_run_tool_cmd",
                        lambda cmd, timeout=180: _FakeCmdResult(
                            0, json.dumps({"ok": False, "error": "数据缺失"})))
    resp = _call("huangzhong_health")
    assert resp["result"]["isError"] is True
    assert "自报" in resp["result"]["content"][0]["text"]


def test_readonly_output_cap_truncates(tmp_path, monkeypatch):
    """超 8KB 输出被截断并标记 output_truncated（不返回半截 JSON）。"""
    repo, work = _make_work(tmp_path)
    _patch(monkeypatch, repo, work)
    big = {"ok": True, "status": "ok", "summary": {"ok": 1},
           "checks": [{"id": "c%d" % i, "name": "检查项%d" % i,
                       "status": "ok", "detail": "x" * 400} for i in range(30)],
           "warnings": [], "generated_at": "2026-10-31T19:00:00+08:00"}
    _fake_tool_cmd(monkeypatch, big)
    monkeypatch.setattr(R, "TOOL_OUTPUT_CAP_BYTES", 500)  # 收紧上限便于断言
    out = R.call_tool("huangzhong_health", {})
    assert out.get("output_truncated") is True
    assert len(json.dumps(out, ensure_ascii=False).encode("utf-8")) <= 500


def test_new_readonly_tools_do_not_change_write_semantics(tmp_path, monkeypatch):
    """新增只读工具不改变写工具 confirm/限频语义。"""
    _prepare_write_env(tmp_path, monkeypatch)
    monkeypatch.setattr(R, "_run_tool_cmd",
                        lambda cmd, timeout=180: _FakeCmdResult(
                            0, json.dumps(HEALTH_PAYLOAD)))
    out = R.call_tool("huangzhong_health", {})  # 只读：无需 confirm
    assert out["ok"] is True
    bad = _call("huangzhong_run_review", confirm="WRONG")  # 写工具仍需 confirm
    assert bad["result"]["isError"] is True
