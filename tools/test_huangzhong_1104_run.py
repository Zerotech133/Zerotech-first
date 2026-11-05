# -*- coding: utf-8 -*-
"""huangzhong_1104_run.py 单测（fake _run，不真实执行子工具、不写 work/）。"""
import datetime as dt
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


# ------------------------------------- 目标日期参数化（HZ-FINAL-DATE-20261104）

TZ8 = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 10, 31, 12, 0, tzinfo=TZ8)
NOW_ISO = NOW.isoformat(timespec="seconds")


def _capture(tmp_path, now=None, target_date=None, run_rc=None, override=None):
    """跑 run_pipeline 并捕获每个子进程命令行（fake _run，不真实执行）。"""
    h = _load()
    calls = []
    fake = _fake(run_rc=run_rc, override=override)

    def run_fn(cmd, cwd=None, timeout=None):
        calls.append([str(c) for c in cmd])
        return fake(cmd, cwd=cwd, timeout=timeout)

    rep = h.run_pipeline(tmp_path, write=True, run_fn=run_fn,
                         now=now, target_date=target_date)
    return h, rep, calls


def _cmds_by_step(calls):
    out = {}
    for cmd in calls:
        stem = _name_of(cmd)
        out[stem] = " ".join(cmd)
    return out


def test_default_target_is_1104_unchanged(tmp_path):
    """默认（不传 target_date/now）= v1 11-04：文件名/标题不变，且不追加新参数。"""
    _, rep, calls = _capture(tmp_path)
    assert rep["target_time"] == "2026-11-04T10:00:00+08:00"
    path = Path(rep["report_md"])
    assert path.name.startswith("2026-11-04_黄忠复核执行汇总_")
    md = path.read_text(encoding="utf-8")
    assert md.startswith("# 黄忠 11-04 复核执行汇总（")
    assert "deliverables/2026-11-04_黄忠观察期复核回执.md" in md
    joined = [" ".join(c) for c in calls]
    assert not any("--target-date" in c for c in joined)
    assert not any("--now" in c for c in joined)
    assert rep["status"] == "ok"
    assert len(rep["steps"]) == 12


def test_now_only_keeps_1104_target(tmp_path):
    """仅显式 --now（不传目标日期）：时钟下发，但目标日期仍默认 11-04。"""
    _, rep, calls = _capture(tmp_path, now=NOW)
    assert rep["target_time"] == "2026-11-04T10:00:00+08:00"
    assert Path(rep["report_md"]).name.startswith(
        "2026-11-04_黄忠复核执行汇总_")
    by_step = _cmds_by_step(calls)
    assert "--now %s" % NOW_ISO in by_step["huangzhong_1104_preflight"]
    assert "--target-date" not in by_step["huangzhong_1104_preflight"]
    assert "--target-date" not in by_step["huangzhong_1104_receipt"]


def test_explicit_target_1110_forwards_and_renames(tmp_path):
    """显式 2026-11-10：汇总切日期、preflight/receipt 收到目标日期与固定时钟。"""
    _, rep, calls = _capture(tmp_path, now=NOW, target_date="2026-11-10")
    assert rep["target_time"] == "2026-11-10T10:00:00+08:00"
    path = Path(rep["report_md"])
    assert path.name.startswith("2026-11-10_黄忠复核执行汇总_")
    md = path.read_text(encoding="utf-8")
    assert md.startswith("# 黄忠 11-10 复核执行汇总（")
    assert "deliverables/2026-11-10_黄忠观察期复核回执.md" in md
    by_step = _cmds_by_step(calls)
    assert "--target-date 2026-11-10T10:00" in by_step["huangzhong_1104_preflight"]
    assert "--now %s" % NOW_ISO in by_step["huangzhong_1104_preflight"]
    assert "--target-date 2026-11-10T10:00" in by_step["huangzhong_1104_receipt"]
    assert "--now %s" % NOW_ISO in by_step["huangzhong_1104_receipt"]
    # 不支持日期参数的工具不得被误传（避免子工具 argparse 报错）
    for stem in ("huangzhong_review", "huangzhong_daily",
                 "huangzhong_risk_coverage", "huangzhong_health"):
        assert "--target-date" not in by_step[stem]
        assert "--now" not in by_step[stem]
    assert rep["status"] == "ok"


def test_target_date_1110_run_keeps_gate_semantics(tmp_path):
    """目标日期只改复算口径：GO 门汇总判定（critical/rc/blockers）逻辑不变。"""
    bad = dict(SAMPLES["huangzhong_1104_preflight"])
    bad["blockers"] = [{"check": "evidence", "message": "stale"}]
    _, rep, _ = _capture(tmp_path, now=NOW, target_date="2026-11-10",
                         override={"huangzhong_1104_preflight": bad})
    assert rep["status"] == "fail"
    pre = next(s for s in rep["steps"] if s["name"] == "preflight")
    assert pre["summary"]["blockers"] == 1
    assert pre["ok"] is True          # rc=0 被接受；失败来自 blockers（与 11-04 判定同构）
    assert rep["hard_bad"] == []
    # 关键步骤 rc=2 仍是硬失败（门未因目标日期放宽）
    _, rep2, _ = _capture(tmp_path / "x", now=NOW, target_date="2026-11-10",
                          run_rc={"huangzhong_daily": 2})
    assert rep2["status"] == "fail"
    assert "weekly" in rep2["critical_bad"] and "weekly" in rep2["hard_bad"]


def test_main_forwards_target_and_now(tmp_path, capsys):
    h = _load()
    seen = {}

    def fake_pipeline(out, timeout=600, write=True, **kw):
        seen.update(kw)
        return {"ok": True, "status": "ok", "steps": [], "report_md": None,
                "critical_bad": [], "hard_bad": [], "warn_steps": []}

    h.run_pipeline = fake_pipeline
    rc = h.main(["--json", "--no-write", "--now", NOW_ISO,
                 "--target-date", "2026-11-10"])
    assert rc == 0
    capsys.readouterr()
    assert seen["target_date"] == "2026-11-10"
    assert isinstance(seen["now"], dt.datetime)
    assert seen["now"].date().isoformat() == "2026-10-31"


def test_main_default_does_not_pass_new_kwargs(tmp_path, capsys):
    """默认 main 调用 run_pipeline 不带新 kwargs（兼容既有 monkeypatch 签名）。"""
    h = _load()
    seen = {}

    def fake_pipeline(out, timeout=600, write=True):
        seen["args"] = (out, timeout, write)
        return {"ok": True, "status": "ok", "steps": [], "report_md": None,
                "critical_bad": [], "hard_bad": [], "warn_steps": []}

    h.run_pipeline = fake_pipeline
    assert h.main(["--json", "--no-write"]) == 0
    capsys.readouterr()
    assert seen["args"][2] is False


def test_main_invalid_target_date_rc2(tmp_path, capsys):
    h = _load()
    rc = h.main(["--json", "--no-write", "--target-date", "2026/11/10"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err


def test_main_invalid_now_rc2(tmp_path, capsys):
    h = _load()
    rc = h.main(["--json", "--no-write", "--now", "bad-time"])
    assert rc == 2
    assert "参数错误" in capsys.readouterr().err

