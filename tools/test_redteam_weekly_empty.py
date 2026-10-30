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
    assert Path(rep["report"]).exists()
