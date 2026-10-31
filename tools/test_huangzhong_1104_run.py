# -*- coding: utf-8 -*-
"""huangzhong_1104_run.py 单测（fake _run，不真实执行子工具、不写 work/）。"""
import importlib.util
import json
from pathlib import Path

import pytest

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location(
        "huangzhong_1104_run", _TOOLS / "huangzhong_1104_run.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class R:
    def __init__(self, rc, stdout="", stderr=""):
        self.returncode = rc
        self.stdout = stdout
        self.stderr = stderr


def _name_of(cmd):
    for tok in cmd:
        if tok.endswith(".py"):
            return Path(tok).stem
    return ""


SAMPLES = {
    "huangzhong_1104_preflight": {"ok": True, "ready": True, "blockers": [], "warnings": [],
                                  "maturity": {"mature_at_1030": 2,
                                               "earliest_window_end": "2026-11-04T07:18:01+08:00"}},
    "huangzhong_review": {"ok": True, "summary": {"passed": 5, "total": 6, "overall_pass": False}},
    "huangzhong_daily": {"ok": True, "runs": 70, "merge_done": 1600, "coverage_rate": 0.0437},
    "huangzhong_1104_receipt": {"ok": True, "draft": {"path": "x.md"}},
    "huangzhong_risk_coverage": {"ok": True, "summary": {"risk_weighted_coverage": 0.04,
                                                          "equal_coverage_all": 0.043,
                                                          "merges_total": 1400}},
    "huangzhong_corpus": {"ok": True, "total": 70, "legacy_count": 6, "drill_count": 2},
    "huangzhong_peer_verify": {"ok": True, "status": "ok", "agreement_rate": 1.0},
    "huangzhong_evidence_lint": {"ok": True, "completeness": 1.0, "scanned": 64},
    "huangzhong_incidents": {"ok": True, "exists": True, "rows": 0, "issue_count": 0},
    "huangzhong_health": {"ok": True, "status": "ok"},
    "huangzhong_perf_signal": {"ok": True, "status": "ok"},
    "huangzhong_security_signal": {"ok": True, "status": "ok"},
}


def _fake(run_rc=None, override=None):
    run_rc = run_rc or {}
    override = override or {}

    def run(cmd, cwd=None, timeout=None):
        stem = _name_of(cmd)
        payload = override.get(stem, SAMPLES[stem])
        rc = run_rc.get(stem, 0)
        return R(rc, json.dumps(payload, ensure_ascii=False), "")
    return run


def _run(tmp_path, **kw):
    h = _load()
    return h, h.run_pipeline(tmp_path, write=kw.pop("write", True),
                             run_fn=_fake(**kw))


def test_all_ok(tmp_path):
    h, rep = _run(tmp_path)
    assert rep["status"] == "ok"
    assert len(rep["steps"]) == 12
    assert rep["critical_bad"] == [] and rep["hard_bad"] == []
    assert rep["report_md"] and Path(rep["report_md"]).exists()
    md = Path(rep["report_md"]).read_text(encoding="utf-8")
    assert "11-04 复核执行汇总" in md and "preflight" in md and "三口径" in md


def test_review_rc1_is_normal(tmp_path):
    _, rep = _run(tmp_path, run_rc={"huangzhong_review": 1})
    assert rep["status"] == "ok"
    assert next(s for s in rep["steps"] if s["name"] == "review")["ok"] is True


def test_preflight_blockers_fail(tmp_path):
    bad = dict(SAMPLES["huangzhong_1104_preflight"])
    bad["blockers"] = [{"check": "evidence", "message": "stale"}]
    _, rep = _run(tmp_path, override={"huangzhong_1104_preflight": bad})
    assert rep["status"] == "fail"
    assert next(s for s in rep["steps"] if s["name"] == "preflight")["summary"]["blockers"] == 1


def test_critical_hard_fail(tmp_path):
    _, rep = _run(tmp_path, run_rc={"huangzhong_daily": 2})
    assert rep["status"] == "fail"
    assert "weekly" in rep["critical_bad"] and "weekly" in rep["hard_bad"]


def test_warn_step_rc1(tmp_path):
    _, rep = _run(tmp_path, run_rc={"huangzhong_health": 1,
                                    "huangzhong_perf_signal": 1})
    assert rep["status"] == "warn"
    assert set(rep["warn_steps"]) == {"health", "perf_signal"}
    assert rep["ok"] is True


def test_timeout_is_fail_visible(tmp_path):
    h = _load()
    def boom(cmd, cwd=None, timeout=None):
        if _name_of(cmd) == "huangzhong_1104_preflight":
            raise TimeoutError("timeout")
        return _fake()(cmd, cwd, timeout)
    rep = h.run_pipeline(tmp_path, run_fn=boom)
    pre = next(s for s in rep["steps"] if s["name"] == "preflight")
    assert pre["ok"] is False and "TimeoutError" in (pre["error"] or "")
    assert rep["status"] == "fail"


def test_no_write(tmp_path):
    h = _load()
    rep = h.run_pipeline(tmp_path / "out", write=False, run_fn=_fake())
    assert rep["report_md"] is None
    assert not (tmp_path / "out").exists()


def test_main_rc_mapping(tmp_path, capsys):
    h = _load()
    # 直接测 rc 映射（不真实执行）：替换 run_pipeline
    h.run_pipeline = lambda out, timeout=600, write=True: {
        "ok": True, "status": "warn", "steps": [], "report_md": None,
        "critical_bad": [], "hard_bad": [], "warn_steps": []}
    assert h.main(["--json", "--no-write"]) == 1
    h.run_pipeline = lambda out, timeout=600, write=True: {
        "ok": False, "status": "fail", "steps": [], "report_md": None,
        "critical_bad": ["x"], "hard_bad": [], "warn_steps": []}
    assert h.main(["--no-write"]) == 2
    h.run_pipeline = lambda out, timeout=600, write=True: {
        "ok": True, "status": "ok", "steps": [], "report_md": None,
        "critical_bad": [], "hard_bad": [], "warn_steps": []}
    assert h.main(["--json", "--no-write"]) == 0
