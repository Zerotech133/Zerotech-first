# -*- coding: utf-8 -*-
"""黄忠验收工具单测（fake git/证据，不联网）。"""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

_TOOLS = Path(__file__).resolve().parent


def _load():
    spec = importlib.util.spec_from_file_location("huangzhong_accept",
                                                  _TOOLS / "huangzhong_accept.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git_ok(sha="a" * 40):
    def run(cmd, **kw):
        return SimpleNamespace(returncode=0,
                               stdout=sha + "\n田丰\n2026-10-28 07:00:00\nfeat: x\n",
                               stderr="")
    return run


def _evidence(tmp_path, failed=0, rate=1.0):
    base = tmp_path / "latest.json"
    base.write_text(json.dumps({"generated_at": "x",
                                "summary": {"passed": 100, "failed": failed,
                                            "error": 0}}), encoding="utf-8")
    rt = tmp_path / "rt"
    rt.mkdir()
    (rt / "redteam_1.json").write_text(json.dumps(
        {"total": 10, "passed": int(rate * 10), "pass_rate": rate,
         "generated_at": "x"}), encoding="utf-8")
    return base, rt


def test_accept_pass(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=0, rate=1.0)
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "PASS" and rep["blocking"] is False
    assert rep["rollback_point"].startswith("git revert")
    assert Path(rep["report_json"]).exists() and Path(rep["report_md"]).exists()


def test_accept_reject_on_baseline_failed(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=3, rate=1.0)
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("基线失败" in r for r in rep["reasons"])


def test_accept_reject_on_redteam_low(monkeypatch, tmp_path):
    h = _load()
    base, rt = _evidence(tmp_path, failed=0, rate=0.5)
    monkeypatch.setattr(h, "BASELINE", base)
    monkeypatch.setattr(h, "REDTEAM_DIR", rt)
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "REJECT"
    assert any("红队" in r for r in rep["reasons"])


def test_accept_observe_when_missing(monkeypatch, tmp_path):
    h = _load()
    monkeypatch.setattr(h, "BASELINE", tmp_path / "nope.json")
    monkeypatch.setattr(h, "REDTEAM_DIR", tmp_path / "nope_rt")
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=_git_ok())
    assert rep["verdict"] == "OBSERVE"
    assert rep["blocking"] is False


def test_git_failure_fail_visible(monkeypatch, tmp_path):
    h = _load()
    monkeypatch.setattr(h, "BASELINE", tmp_path / "nope.json")
    monkeypatch.setattr(h, "REDTEAM_DIR", tmp_path / "nope_rt")

    def bad(cmd, **kw):
        return SimpleNamespace(returncode=1, stdout="", stderr="bad rev")
    rep = h.accept("HEAD", out_dir=tmp_path / "out", runner=bad)
    assert rep["commit"]["ok"] is False
    assert rep["verdict"] == "OBSERVE"


def test_verdict_rules_pure():
    h = _load()
    assert h._verdict({"ok": True, "failed": 0, "error": 0},
                      {"ok": True, "pass_rate": 0.9})[0] == "PASS"
    assert h._verdict({"ok": True, "failed": 1, "error": 0},
                      {"ok": True, "pass_rate": 1.0})[0] == "REJECT"
    assert h._verdict({"ok": False}, {"ok": True, "pass_rate": 1.0})[0] == "OBSERVE"
