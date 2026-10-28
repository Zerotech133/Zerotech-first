# -*- coding: utf-8 -*-
"""O8 P3d 依赖 triage 周报段单测（不联网）。"""
import importlib.util
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("weekly_dep_triage", HERE / "weekly_dep_triage.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

PAYLOAD = {
    "checked": 12, "fresh_count": 2, "error_count": 1, "cutoff": "2026-10-22",
    "fresh": [
        {"name": "fastapi", "version": "0.162.0", "released": "2026-10-23", "age_days": 4.6},
        {"name": "openai", "version": "3.39.0", "released": "2026-10-26", "age_days": 1.6},
    ],
    "errors": [{"name": "anthropic", "version": "2.3.0", "error": "URLError: timeout"}],
}


def test_build_cmd():
    assert m.build_cmd(0)[-1] == "--json"
    assert m.build_cmd(9)[-2:] == ["--limit", "9"]


def test_summarize():
    t = m.summarize(PAYLOAD, "2026-W44")
    assert t["week"] == "2026-W44"
    assert t["checked"] == 12
    assert t["fresh_count"] == 2
    assert t["error_count"] == 1
    assert t["warn_only"] is True


def test_render_markdown_with_fresh():
    t = m.summarize(PAYLOAD, "2026-W44")
    md = m.render_markdown(t)
    assert "依赖 triage" in md
    assert "fastapi" in md and "0.162.0" in md
    assert "不阻断" in md


def test_render_markdown_no_fresh():
    t = m.summarize({"checked": 3, "fresh_count": 0, "errors": []}, "2026-W44")
    md = m.render_markdown(t)
    assert "无** 7 天内新发布依赖" in md


def test_run_triage_fake_runner():
    def fake_run(cmd, **kwargs):
        return type("P", (), {"stdout": json.dumps(PAYLOAD), "returncode": 0})()
    t = m.run_triage("2026-W44", runner=fake_run)
    assert t["ok"] is True and t["fresh_count"] == 2


def test_run_triage_fail_open():
    def boom(cmd, **kwargs):
        raise RuntimeError("network down")
    t = m.run_triage("2026-W44", runner=boom)
    assert t["ok"] is False and "network down" in t["error"]


def test_main_saves_and_returns_zero(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "run_triage",
                        lambda week, limit=0, timeout=600: m.summarize(PAYLOAD, week))
    rc = m.main(["--week", "2026-W44", "--out-dir", str(tmp_path)])
    assert rc == 0
    assert (tmp_path / "2026-W44.json").exists()
