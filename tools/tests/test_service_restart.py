# -*- coding: utf-8 -*-
"""service_restart 单测（fake health/starter/sleep；不碰真实服务）。"""
import importlib.util
from pathlib import Path

TOOL = Path(__file__).resolve().parents[1] / "service_restart.py"


def _load():
    spec = importlib.util.spec_from_file_location("service_restart_test", TOOL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_dry_run_no_side_effect(tmp_path):
    m = _load()
    flag = tmp_path / "flag"
    rep = m.restart(flag, apply=False)
    assert rep["ok"] and rep["dry_run"] is True
    assert not flag.exists()


def test_restart_success_consumes_flag(tmp_path):
    m = _load()
    flag = tmp_path / "flag"
    calls = []

    def starter(task):
        calls.append(task)
        flag.unlink(missing_ok=True)  # 模拟任务消费哨兵
        return {"ok": True, "rc": 0}

    reps = iter([{"ok": False}, {"ok": True}])
    rep = m.restart(flag, apply=True, starter=starter,
                    health_fn=lambda u: next(reps, {"ok": True}),
                    timeout=30, sleep=lambda s: None)
    assert rep["ok"] and rep["restarted"] is True and rep["flag_consumed"] is True
    assert calls == ["LegionOps"]


def test_restart_timeout_fail_visible(tmp_path):
    m = _load()
    flag = tmp_path / "flag"
    rep = m.restart(flag, apply=True,
                    starter=lambda t: {"ok": True, "rc": 0},
                    health_fn=lambda u: {"ok": False},
                    timeout=31, sleep=lambda s: None)
    assert rep["ok"] is False and rep["error"] == "timeout"


def test_task_start_failure(tmp_path):
    m = _load()
    flag = tmp_path / "flag"
    rep = m.restart(flag, apply=True,
                    starter=lambda t: {"ok": False, "rc": 1, "stderr": "denied"},
                    health_fn=lambda u: {"ok": True}, timeout=30)
    assert rep["ok"] is False and rep["error"] == "task_start_failed"
    assert flag.exists()  # 哨兵保留，便于人工处置


def test_probe_failure(tmp_path):
    m = _load()
    flag = tmp_path / "flag"
    states = {"health": 0}

    def health(u):
        states["health"] += 1
        if "stats" in u:
            return {"ok": False, "error": "probe 500"}
        return {"ok": True}

    def starter(t):
        flag.unlink(missing_ok=True)
        return {"ok": True, "rc": 0}

    rep = m.restart(flag, apply=True, starter=starter, health_fn=health,
                    probe="/api/stats", timeout=30, sleep=lambda s: None)
    assert rep["ok"] is False and rep["error"] == "probe_failed"
