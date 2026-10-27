# -*- coding: utf-8 -*-
"""org_chart 测试（WO-ORG-CHART）。"""
from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

_WT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "org_chart", str(_WT / "tools" / "org_chart.py"))
O = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(O)

ORG = O.load(O.ORG)


def _clone():
    return copy.deepcopy(ORG)


def _dev(org):
    return next(d for d in org["departments"] if d["dept"] == "dev")


def test_real_org_passes():
    errors, c = O.validate(ORG)
    assert errors == [], errors
    assert c["profiles"]["ARCH1"]["report_to"] == "A0"
    assert c["profiles"]["DEV6"]["report_to"] == "DEV1"
    assert c["profiles"]["DEV8"]["report_to"] == "DEV5"
    assert c["a0a"] and c["a0a"].get("report_to") == "ARCH1"
    assert c["matrix"].get("DEV1")


def test_wrong_report_to_fails():
    org = _clone()
    p = next(x for x in _dev(org)["agent_profiles"] if x["agent_id"] == "DEV6")
    p["report_to"] = "ARCH1"
    errors, _ = O.validate(org)
    assert any("DEV6 report_to 应为 DEV1" in e for e in errors)


def test_missing_report_to_fails():
    org = _clone()
    p = next(x for x in _dev(org)["agent_profiles"] if x["agent_id"] == "DEV8")
    p.pop("report_to")
    errors, _ = O.validate(org)
    assert any("DEV8 report_to 应为 DEV5" in e for e in errors)


def test_a0a_wrong_fails():
    org = _clone()
    for d in org["departments"]:
        for p in d.get("agent_profiles") or []:
            if p.get("agent_id") == "A0A":
                p["report_to"] = "A0"
    errors, _ = O.validate(org)
    assert any("A0A 交付协调 report_to" in e for e in errors)


def test_render_contains_org():
    errors, c = O.validate(ORG)
    md = O.render_md(ORG, c)
    for k in ("曹操", "田丰", "马钧", "邓艾", "G1", "G2", "项目矩阵"):
        assert k in md
    html = O.render_html(ORG, c)
    for k in ("研发部组织架构", "tokens.css", "nav.js", "曹操", "G1"):
        assert k in html
