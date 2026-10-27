# -*- coding: utf-8 -*-
"""dev_metrics_daily 测试（WO-P0-DEV8-METRICS）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "dev_metrics_daily", str(_WT / "tools" / "dev_metrics_daily.py"))
M = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(M)


def _metrics(**kw):
    base = {"dev_tasks_recent": 6, "owner_legal_rate_recent": 0.9,
            "dev_dept_tagged": 10, "dev_dept_with_acceptance": 10,
            "e3_trigger": {"ARCH1": {"ok": 1}, "DEV1": {"ok": 1},
                           "DEV5": {"ok": 1}, "OPS1": {"ok": 1}}}
    base.update(kw)
    return base


def test_alerts_all_pass():
    assert M.evaluate_alerts(_metrics()) == []


def test_alerts_each_threshold():
    a = M.evaluate_alerts(_metrics(dev_tasks_recent=2))
    assert any("采集份额" in x for x in a)
    a = M.evaluate_alerts(_metrics(owner_legal_rate_recent=0.5))
    assert any("归属率" in x for x in a)
    a = M.evaluate_alerts(_metrics(dev_dept_with_acceptance=5))
    assert any("验证覆盖" in x for x in a)
    a = M.evaluate_alerts(_metrics(e3_trigger={"ARCH1": {"ok": 0}}))
    assert any("E3 触发" in x for x in a)


def test_cli_write_and_check(tmp_path):
    out = tmp_path / "daily.json"
    assert M.main(["--out", str(out)]) == 0
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema"] == "dev_daily_metrics/v1"
    assert isinstance(data["alerts"], list)
    assert M.main(["--check", "--out", str(out)]) == 0
    assert M.main(["--check", "--out", str(tmp_path / "missing.json")]) == 1
