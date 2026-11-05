# -*- coding: utf-8 -*-
"""FutQuant 晨报哨兵的单测：产物名与 all-error 可读性。"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import futquant_notify as fn


def _write(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False), encoding="utf-8")
    os.utime(path, None)


def test_today_artifacts_use_real_pipeline_names(tmp_path, monkeypatch):
    for name in ("fut_scan", "fut_strat", "fut_walkforward", "cn_fut_backtest"):
        _write(tmp_path / (name + "_20261105.json"), {"ok": True})
    monkeypatch.setattr(fn, "OUT_DIR", str(tmp_path))

    arts = fn._today_artifacts()

    assert [name for name, _ in arts] == list(fn.ARTIFACT_PREFIXES)
    assert all(ok for _, ok in arts)


def test_matrix_readout_flags_all_error(tmp_path, monkeypatch):
    rows = [
        {"symbol": "GC=F", "error": "Failed to fetch data: HTTP Error 403"},
        {"symbol": "SI=F", "error": "Failed to fetch data: HTTP Error 403"},
    ]
    _write(tmp_path / "fut_strat_20261105.json", {"results": rows})
    monkeypatch.setattr(fn, "OUT_DIR", str(tmp_path))

    m = fn._matrix_readout()

    assert m["n"] == 2
    assert m["n_ok"] == 0
    assert m["all_error"] is True
    assert "403" in m["sample_error"]


def test_summary_surfaces_degraded_f3_and_real_artifacts(monkeypatch):
    monkeypatch.setattr(
        fn,
        "_today_artifacts",
        lambda: [(name, True) for name in fn.ARTIFACT_PREFIXES],
    )
    monkeypatch.setattr(
        fn,
        "_matrix_readout",
        lambda: {
            "n": 14,
            "n_ok": 0,
            "beat": 0,
            "best": None,
            "all_error": True,
            "sample_error": "Failed to fetch data for 'GC=F': HTTP Error 403",
        },
    )

    text = fn._summary((0, 0, 0, 0, "2026-11-05T09:34:09"), "2026-11-05T09:34:09")

    assert "⚠️" in text.splitlines()[0]
    assert "fut_strat" in text
    assert "产物 fut_backtest:" not in text
    assert "F3 矩阵: ❌ 14/14 全部失败" in text
