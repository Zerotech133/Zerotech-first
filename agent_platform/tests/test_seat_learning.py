# -*- coding: utf-8 -*-
"""seat_learning 测试（WO-LEARNING-DEV7）。"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "seat_learning", str(_WT / "tools" / "seat_learning.py"))
L = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(L)

PLAN = L.load("DEV7")[0]


def _clone():
    return copy.deepcopy(PLAN)


def test_real_plan_passes():
    errors = L.validate(PLAN)
    assert errors == [], errors
    s = L.summary(PLAN)
    assert s["seat"] == "DEV7" and s["modules"] == "0/4 done"
    assert s["assessment"] == "planned"


def test_missing_targets_fails():
    p = _clone()
    p["target_capabilities"] = []
    assert any("target_capabilities" in e for e in L.validate(p))


def test_done_module_needs_evidence():
    p = _clone()
    p["modules"][0]["status"] = "done"
    p["modules"][0]["evidence"] = ""
    assert any("done 但无 evidence" in e for e in L.validate(p))


def test_bad_source_path_fails():
    p = _clone()
    p["modules"][0]["source"] = "agent_platform/prompts/no_such_file.md"
    assert any("材料路径不存在" in e for e in L.validate(p))


def test_bad_status_and_assessment():
    p = _clone()
    p["modules"][0]["status"] = "finished"
    p["assessment"]["status"] = "passed"
    errs = L.validate(p)
    assert any("status 非法" in e for e in errs)
    assert any("passed 但无 evidence" in e for e in errs)


def test_cli_check_real():
    assert L.main(["--check", "DEV7"]) == 0
    assert L.main(["--check", "NOPE"]) == 1
