# -*- coding: utf-8 -*-
"""qa_acceptance（问答链路端到端验收）契约测试 —— mock 不调真 LLM。

覆盖：
  1. CLI 段：状态分类（success/no_evidence/fallback）、guard 字段、deadline 跳过、pass 判定；
  2. HTTP 段：进程内 handle 5 用例（retrieval/single/multi/guard=0/guard=2）状态码/meta；
  3. 门户段：fake fetch 的 200/schema v2 通过 与 失败；
  4. 结论：go / conditional / no-go + 需修项；全流程 schema + JSON/MD 落盘 + CLI。
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ACC_PATH = REPO / "tools" / "kb_corpus" / "qa_acceptance.py"
V3_BANK = REPO / "research" / "reports" / "kb_qa_bank_v3_20261101.json"


def _load():
    spec = importlib.util.spec_from_file_location("qa_acceptance_under_test", ACC_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


acc = _load()


def questions(n=3):
    return [{"id": f"q{i}", "question": f"问题{i}", "category": "是什么",
             "theme": "AI算力/半导体"} for i in range(n)]


def mock_ask_success(question, **kw):
    assert kw.get("mode") == "multi" and kw.get("guard") is True
    assert kw.get("on_guard_fail") == "fallback"
    return {"question": question, "answer": "答案 [u1] p.1", "mode": "multi",
            "citations": [{"unit_id": "u1", "page": 1}],
            "fallback": False, "refusal": False, "guard_enabled": True,
            "guard_passed": True, "guard_action": "pass", "guard_issues": [],
            "citation_resolvable_rate": 1.0, "support_rate": 1.0,
            "inference_ratio": 0.5, "distinct_cited_units": 1}


def mock_ask_mixed(question, **kw):
    idx = int(question.replace("问题", ""))
    base = {"question": question, "mode": "multi", "citations": [], "fallback": False,
            "refusal": False, "guard_enabled": True, "guard_passed": True,
            "guard_action": "pass", "guard_issues": [], "citation_resolvable_rate": 1.0,
            "support_rate": 1.0, "inference_ratio": 0.0, "distinct_cited_units": 2}
    if idx == 0:
        return {**base, "answer": "答案", "citations": [{"unit_id": "u1"}]}
    if idx == 1:
        return {**base, "answer": "知识库中没有足够依据回答该问题。",
                "confidence": {"level": "none"}, "refusal": True}
    return {**base, "answer": "（质量守卫回退）摘录", "fallback": True,
            "guard_passed": False, "guard_action": "fallback",
            "guard_issues": ["inference_heavy"], "citations": [{"unit_id": "u2"}]}


# ---------------------------------------------------------------------------
# 1. CLI 段
# ---------------------------------------------------------------------------
def test_cli_checks_status_and_guard_fields():
    res = acc.run_cli_checks(questions(3), ask_fn=mock_ask_mixed, clock=lambda: 0.0)
    rows = res["rows"]
    assert [r["status"] for r in rows] == ["success", "no_evidence", "fallback"]
    assert all(r["mode"] == "multi" and r["guard_enabled"] is True for r in rows)
    assert rows[2]["guard_passed"] is False and rows[2]["guard_issues"] == ["inference_heavy"]
    s = res["summary"]
    assert s["actual"] == 3 and s["success"] == 1 and s["no_evidence"] == 1 and s["fallback"] == 1
    assert s["guard_passed"] == 2 and s["guard_failed"] == 1
    assert s["errors"] == [] and s["pass"] is True


def test_cli_deadline_skip_and_error():
    ticks = iter([0.0, 9999.0] + [9999.0] * 20)

    def clock():
        try:
            return next(ticks)
        except StopIteration:
            return 9999.0

    def boom(*a, **k):
        raise RuntimeError("LLM 挂起（测试）")

    res = acc.run_cli_checks(questions(2), ask_fn=boom, clock=clock, deadline_seconds=1)
    assert res["summary"]["skipped"] == 2 and res["summary"]["actual"] == 0
    res2 = acc.run_cli_checks(questions(1), ask_fn=boom, clock=lambda: 0.0, deadline_seconds=60)
    assert res2["summary"]["errors"] and res2["summary"]["pass"] is False


# ---------------------------------------------------------------------------
# 2. HTTP 段
# ---------------------------------------------------------------------------
def test_http_checks_in_process():
    res = acc.run_http_checks()
    assert res["summary"]["cases"] == 5
    checks = {c["case"]: c for c in res["summary"]["checks"]}
    assert set(checks) == {"retrieval", "single", "multi", "multi_guard0", "guard_invalid"}
    assert checks["retrieval"]["status"] == 200 and checks["retrieval"]["pass"]
    assert checks["single"]["status"] == 200 and checks["single"]["pass"]
    assert checks["multi"]["status"] == 200 and checks["multi"]["pass"]
    assert checks["multi_guard0"]["status"] == 200 and checks["multi_guard0"]["pass"]
    assert checks["guard_invalid"]["status"] == 400 and checks["guard_invalid"]["pass"]
    assert res["summary"]["wired"] is True  # 2026-11-01 L2 批准后已注册到 8091
    assert res["summary"]["pass"] is True


# ---------------------------------------------------------------------------
# 3. 门户段 + 结论
# ---------------------------------------------------------------------------
def test_portal_checks_and_conclusion():
    page = "<html>💬 问答示例（静态） 守卫通过 multi</html>".encode()
    good_json = json.dumps({"schema_version": 2, "mode": "multi",
                            "guard": {"enabled": True},
                            "samples": [{"question": "q"}] * 12}).encode()

    def fetch_ok(url):
        return (200, page) if url.endswith("kb_reports.html") else (200, good_json)

    portal = acc.run_portal_checks(fetch_fn=fetch_ok)
    assert portal["summary"]["pass"] is True
    assert portal["rows"][0]["has_qa_block"] is True
    assert portal["rows"][1]["schema_version"] == 2 and portal["rows"][1]["samples"] == 12

    def fetch_bad(url):
        return (500, b"")

    bad = acc.run_portal_checks(fetch_fn=fetch_bad)
    assert bad["summary"]["pass"] is False

    cli_pass = {"summary": {"pass": True}}
    http_pass = {"summary": {"pass": True, "checks": []}}
    conc = acc.build_conclusion(cli_pass, http_pass, portal)
    assert conc["can_go_live"] == "go" and not conc["fixes_needed"]
    http_bad = {"summary": {"pass": False, "checks": [
        {"case": "single", "status": 400, "expected": 200, "pass": False}]}}
    conc2 = acc.build_conclusion(cli_pass, http_bad, portal)
    assert conc2["can_go_live"] == "conditional" and conc2["fixes_needed"]
    conc3 = acc.build_conclusion({"summary": {"pass": False}}, http_bad, bad)
    assert conc3["can_go_live"] == "no-go"


# ---------------------------------------------------------------------------
# 4. 全流程 schema + 落盘 + CLI
# ---------------------------------------------------------------------------
def test_run_acceptance_schema_and_outputs(tmp_path):
    page = "<html>问答示例 守卫通过</html>".encode()
    good_json = json.dumps({"schema_version": 2, "mode": "multi",
                            "guard": {"enabled": True},
                            "samples": [{"question": "q"}] * 12}).encode()

    def fetch_ok(url):
        return (200, page) if url.endswith("kb_reports.html") else (200, good_json)

    payload = acc.run_acceptance(n=3, ask_fn=mock_ask_success, fetch_fn=fetch_ok,
                                 clock=lambda: 0.0)
    assert payload["kind"] == "qa_acceptance"
    for key in ("cli", "http", "portal", "conclusion", "config"):
        assert key in payload, key
    assert payload["conclusion"]["cli_pass"] is True
    assert payload["conclusion"]["portal_pass"] is True
    jp, mp = acc.write_outputs(payload, tmp_path / "acc.json", tmp_path / "acc.md")
    assert jp.exists() and mp.exists()
    md = mp.read_text(encoding="utf-8")
    for anchor in ("验收结论", "一、CLI 端到端", "二、HTTP 预接线", "三、门户核查", "风险与说明"):
        assert anchor in md, anchor
    assert json.loads(jp.read_text(encoding="utf-8"))["cli"]["summary"]["actual"] == 3
    proc = subprocess.run([sys.executable, "-X", "utf8", str(ACC_PATH), "--help"],
                          capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
    assert proc.returncode == 0 and "run" in proc.stdout
    with _raises(acc.AcceptanceError):
        acc.load_questions(tmp_path / "nope.json")


class _raises:
    def __init__(self, exc):
        self.exc = exc

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        assert exc_type is not None and issubclass(exc_type, self.exc), \
            f"期望抛出 {self.exc}，实际 {exc_type}"
        return True
