# -*- coding: utf-8 -*-
"""F2 身份一致性：display_name 唯一、赵云治理/风控区分、视图与 registry 对齐。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_PLATFORM = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_PLATFORM))

REG = _PLATFORM / "registry"


def _load(name):
    return json.loads((REG / name).read_text(encoding="utf-8"))


def _org_profiles():
    """递归收集 org.json 内所有 agent_profiles 条目（含 R1/R2/R3）。"""
    out = []
    d = _load("org.json")

    def walk(o):
        if isinstance(o, dict):
            if "agent_id" in o and ("role_profile" in o or "display_name" in o):
                out.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(d)
    return out


def _display_map():
    m = {}
    for s in _load("profile_seats.json").get("seats") or []:
        if s.get("display_name"):
            m[s["agent_id"]] = s["display_name"]
    for p in _org_profiles():
        if p.get("display_name"):
            m[p["agent_id"]] = p["display_name"]
    return m


def test_zhaoyun_display_name():
    seats = {s["agent_id"]: s for s in _load("profile_seats.json")["seats"]}
    z = seats["ZHAOYUN"]
    assert z["cn_name"] == "赵云"
    assert z["display_name"] == "赵云·治理"


def test_risk_seat_names():
    profiles = {p["agent_id"]: p for p in _org_profiles()}
    assert profiles["R1"]["display_name"] == "甘宁·风控R1"
    assert profiles["R2"]["display_name"] == "赵云·风控R2"
    assert profiles["R2"]["cn_name"] == "赵云"
    assert profiles["R3"]["display_name"] == "陆逊·风控R3"


def test_display_names_unique():
    m = _display_map()
    vals = list(m.values())
    assert len(vals) == len(set(vals)), "display_name 重复: %s" % vals
    assert m["ZHAOYUN"] != m["R2"]


def test_view_identity_matches_registry():
    from legion_command.zhaoyun_view import IDENTITY

    m = _display_map()
    assert IDENTITY["display_name"] == m["ZHAOYUN"] == "赵云·治理"
    # 同名不同 id 的歧义已用 display_name 消解
    assert IDENTITY["name"] == "赵云" and m["R2"].startswith("赵云·")
