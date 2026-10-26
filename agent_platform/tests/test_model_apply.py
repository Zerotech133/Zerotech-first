# -*- coding: utf-8 -*-
"""模型覆盖 L2 编排测试（WO-MODEL-APPLY）。"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "model_apply", str(_WT / "agent_platform" / "model_apply" / "apply.py"))
MA = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(MA)


def _sandbox(tmp_path, monkeypatch, overrides=None):
    ov = tmp_path / "overrides.json"
    ov.write_text(json.dumps({"overrides": overrides or []}), encoding="utf-8")
    monkeypatch.setattr(MA, "OVERRIDES", ov)
    monkeypatch.setattr(MA, "MARKER", tmp_path / "applied.json")


def test_plan_latest_per_seat(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, [
        {"seat": "R2", "candidate": "deepseek/deepseek-flash", "approved_by": "老田"},
        {"seat": "R2", "candidate": "ollama/qwen3.5:9b", "approved_by": "老田"},
        {"seat": "ZHAOYUN", "candidate": "bad/model", "approved_by": "老田"},
    ])
    p = MA.plan()
    assert p["ok"] and p["count"] == 2 and p["l2_required"] is True
    seats = {x["seat"]: x for x in p["pending"]}
    assert seats["R2"]["candidate"] == "ollama/qwen3.5:9b"
    assert seats["ZHAOYUN"]["candidate_valid"] is False
    assert p["plan_digest"]


def test_apply_requires_approval_and_confirm(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, [
        {"seat": "R2", "candidate": "deepseek/deepseek-flash", "approved_by": "老田"}])
    assert MA.apply("", MA.CONFIRM)["error"] == "l2_approval_required"
    assert MA.apply("老田", "yes")["error"] == "l2_confirm_required"
    rep = MA.apply("老田", MA.CONFIRM)
    assert rep["ok"] and rep["applied"] is False and rep["marker_written"]
    assert rep["marker"]["approved_by"] == "老田" and rep["marker"]["executed"] is False


def test_no_pending(tmp_path, monkeypatch):
    _sandbox(tmp_path, monkeypatch, [])
    assert MA.apply("老田", MA.CONFIRM)["error"] == "no_pending_overrides"
