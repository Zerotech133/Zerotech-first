# -*- coding: utf-8 -*-
"""A 阶段组级看板生成器测试（WO-A-STAGE-BOARD）。"""
from __future__ import annotations

import importlib.util
import time
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_TOOL = _WT / "tools" / "a_phase_group_board.py"
_spec = importlib.util.spec_from_file_location("a_phase_group_board", str(_TOOL))
B = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(B)

NOW = time.mktime(time.strptime("2026-10-27 15:00:00", "%Y-%m-%d %H:%M:%S"))

ORG = {"departments": [{"dept": "dev", "groups": [
    {"id": "G1", "name": "后端/平台组", "lead": "DEV1",
     "members": ["DEV6", "DEV7"]},
    {"id": "G2", "name": "前端/业务组", "lead": "DEV5",
     "members": ["DEV8"]},
]}]}

TASKS = {
    "T1": {"owner": "DEV1", "status": "done", "created": "2026-10-27 10:00:00",
           "merged": "2026-10-27 11:00:00", "dept": "dev", "acceptance_cmd": "pytest -q"},
    "T2": {"owner": "DEV6", "status": "doing", "created": "2026-10-26 10:00:00",
           "dept": "dev", "acceptance_cmd": ""},
    "T3": {"owner": "DEV5", "status": "done", "created": "2026-10-25 09:00:00",
           "merged": "2026-10-25 10:00:00", "dept": "dev", "acceptance_cmd": "pytest -q"},
    "T4": {"owner": "ARCH1", "status": "doing", "created": "2026-10-24 09:00:00"},
}


def test_compute_groups():
    rep = B.compute(B.dev_groups(ORG), TASKS, window_days=7, now=NOW)
    g1, g2 = rep["dept"]["groups"]
    assert g1["lead"] == "DEV1" and "DEV6" in g1["members"]
    s = g1["stats"]
    assert s["total"] == 2 and s["doing"] == 1 and s["done_recent"] == 1
    assert s["lead_n"] == 1 and s["lead_p50_min"] == 60.0
    assert s["acceptance"] == {"covered": 1, "total": 2}
    assert s["members"]["DEV6"]["doing"] == 1
    assert g2["stats"]["total"] == 1 and g2["stats"]["done_recent"] == 1
    # ARCH1 不在任何组 → 不计入组
    assert rep["dept"]["total"] == 4


def test_render_and_check(tmp_path):
    rep = B.compute(B.dev_groups(ORG), TASKS, window_days=7, now=NOW)
    out = tmp_path / "board.html"
    out.write_text(B.render_html(rep), encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    for k in ("组级看板", "<table", "tokens.css", "nav.js", "G1", "G2"):
        assert k in text
    assert B.main(["--org", "x", "--check", "--out", str(out)]) == 0
    assert B.main(["--check", "--out", str(tmp_path / "missing.html")]) == 1


def test_missing_tasks_returns_empty(tmp_path):
    assert B.load_tasks(tmp_path / "nope.json") == {}
    assert B.load_json(tmp_path / "nope.json", {}) == {}
