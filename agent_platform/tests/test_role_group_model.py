# -*- coding: utf-8 -*-
"""角色分工/分组适配模型测试（WO-ROLE-GROUP-MODEL）。"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "role_group_model", str(_WT / "tools" / "role_group_model.py"))
R = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(R)

ORG = R.load(R.ORG)
CFG = R.load(R.CFG)


def _clone():
    return copy.deepcopy(ORG), copy.deepcopy(CFG)


def _dev(org):
    return next(d for d in org["departments"] if d["dept"] == "dev")


def test_real_roster_passes():
    rep = R.validate(ORG, CFG)
    assert rep["errors"] == [], rep["errors"]
    assert len(rep["rows"]) == 11
    seats = {r["seat"] for r in rep["rows"]}
    assert {"ARCH1", "DEV1", "DEV2", "DEV3", "DEV4", "DEV5",
            "DEV6", "DEV7", "DEV8", "OPS1", "DOC1"} <= seats
    disc = {r["seat"]: r["discipline"] for r in rep["rows"]}
    assert disc["DEV6"] == "backend" and disc["DEV2"] == "ai"
    assert disc["DEV5"] == "frontend" and disc["DEV3"] == "quant"


def test_missing_discipline_fails():
    org, cfg = _clone()
    p = next(x for x in _dev(org)["agent_profiles"] if x["agent_id"] == "DEV1")
    p.pop("discipline")
    rep = R.validate(org, cfg)
    assert any("DEV1 缺 discipline" in e for e in rep["errors"])


def test_wrong_model_fails():
    org, cfg = _clone()
    p = next(x for x in _dev(org)["agent_profiles"] if x["agent_id"] == "DEV1")
    p["llm_binding"] = "ollama/qwen3.5:9b"
    rep = R.validate(org, cfg)
    assert any("DEV1 绑定" in e for e in rep["errors"])


def test_group_name_mismatch_fails():
    org, cfg = _clone()
    g = next(x for x in _dev(org)["groups"] if x["id"] == "G1")
    g["name"] = "后端组"
    rep = R.validate(org, cfg)
    assert any("组 G1 名称不一致" in e for e in rep["errors"])


def test_lead_must_be_e3():
    org, cfg = _clone()
    p = next(x for x in _dev(org)["agent_profiles"] if x["agent_id"] == "DEV1")
    p["grade"] = "E2"
    rep = R.validate(org, cfg)
    assert any("DEV1 是 G1 组长但职级" in e for e in rep["errors"])


def test_md_has_rows():
    rep = R.validate(ORG, CFG)
    md = R.render_md(rep)
    for k in ("DEV1", "DEV2", "前端", "后端", "AI/模型", "PASS"):
        assert k in md
