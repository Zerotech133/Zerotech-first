# -*- coding: utf-8 -*-
"""红队周报空窗口回归：无运行记录时不得把列表传给 %d。"""
from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MOD = ROOT / "tools" / "redteam_daily.py"


def _load():
    spec = importlib.util.spec_from_file_location("_redteam_weekly_empty_test", str(MOD))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_weekly_empty_window(tmp_path):
    mod = _load()
    rep = mod.weekly(
        days=7,
        daily_path=tmp_path / "none.jsonl",
        out_dir=tmp_path / "out",
    )
    assert rep["runs"] == 0
    assert rep["total_cases"] == 0
    assert rep["ok"] is False
    assert rep["insufficient_data"] is True
    report = Path(rep["report"]).read_text(encoding="utf-8")
    assert "数据不足" in report


def test_weekly_dry_does_not_write(tmp_path):
    mod = _load()
    rep = mod.weekly(
        days=7,
        daily_path=tmp_path / "none.jsonl",
        out_dir=tmp_path / "out",
        dry=True,
    )
    assert rep["ok"] is False
    assert not (tmp_path / "out").exists()


def test_weekly_dry_rows_do_not_count_as_live(tmp_path):
    mod = _load()
    daily = tmp_path / "daily.jsonl"
    daily.write_text(
        '{"ts":"2026-10-30T09:00:00+08:00","mode":"dry","total":2,"passed":2}\n'
        '{"ts":"2026-10-30T09:01:00+08:00","mode":"live","total":2,"passed":1}\n',
        encoding="utf-8",
    )
    rep = mod.weekly(days=7, daily_path=daily, out_dir=tmp_path / "out", dry=True)
    assert rep["runs_live"] == 1
    assert rep["runs_dry"] == 1
    assert rep["total_cases"] == 2
    assert rep["passed"] == 1
    assert rep["pass_rate"] == 0.5
