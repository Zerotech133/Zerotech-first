# -*- coding: utf-8 -*-
"""黄忠 W1 单测：样例文档在位 + 每日观察/周报工具。"""
import importlib.util
import json
from pathlib import Path

_TOOLS = Path(__file__).resolve().parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, _TOOLS / (name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fake_verdict(v="PASS", failed=0, rate=1.0):
    return {
        "commit": {"sha": "a" * 40}, "verdict": v, "blocking": False,
        "reasons": [] if v == "PASS" else ["基线失败"],
        "evidence": {"baseline": {"failed": failed},
                     "redteam": {"pass_rate": rate}},
        "report_md": "x.md",
    }


def test_w1_doc_exists():
    root = _TOOLS.parent
    p = root / "docs" / "plans" / "2026-10-28_黄忠W1验收样例与批改标准_v1.md"
    assert p.is_file()
    t = p.read_text(encoding="utf-8")
    for key in ("验收判据样例", "七检解读", "回滚点样例", "Rubric", "批改标准"):
        assert key in t


def test_daily_appends(tmp_path):
    d = _load("huangzhong_daily")
    rep = d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path)
    assert rep["verdict"] == "PASS"
    lines = (tmp_path / "daily.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["commit"] == "a" * 12


def test_daily_multiple(tmp_path):
    d = _load("huangzhong_daily")
    d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path)
    d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=2), out_dir=tmp_path)
    lines = (tmp_path / "daily.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2


def test_weekly_aggregates(tmp_path):
    d = _load("huangzhong_daily")
    d.run_daily(accept_fn=lambda c: _fake_verdict(), out_dir=tmp_path)
    d.run_daily(accept_fn=lambda c: _fake_verdict("REJECT", failed=1), out_dir=tmp_path)
    d.run_daily(accept_fn=lambda c: _fake_verdict("OBSERVE"), out_dir=tmp_path)
    rep = d.weekly(days=7, out_dir=tmp_path)
    assert rep["runs"] == 3
    assert rep["counts"] == {"PASS": 1, "REJECT": 1, "OBSERVE": 1}
    assert Path(rep["report_md"]).exists()
    assert "观察周报" in Path(rep["report_md"]).read_text(encoding="utf-8")


def test_weekly_empty(tmp_path):
    d = _load("huangzhong_daily")
    rep = d.weekly(days=7, out_dir=tmp_path)
    assert rep["runs"] == 0 and rep["pass_rate"] == 0.0
