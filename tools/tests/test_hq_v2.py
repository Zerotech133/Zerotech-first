# -*- coding: utf-8 -*-
"""HQ v2：五角色/议程/商业只读模型。"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_agenda  # noqa: E402
import hq_commercial  # noqa: E402
import hq_roles  # noqa: E402


def test_five_roles():
    d = hq_roles.build_roles()
    assert d["read_only"] is True
    assert d["summary"]["roles"] == 5
    names = {r["name"] for r in d["roles"]}
    assert {"田丰", "诸葛亮", "赵云", "黄忠", "关羽"} <= names
    for r in d["roles"]:
        assert r["duty"] and r["seat_id"]


def test_agenda_has_classification():
    d = hq_agenda.build_agenda()
    assert d["read_only"] is True
    assert "by_kind" in d["summary"]
    assert isinstance(d["items"], list)


def test_commercial_summary():
    d = hq_commercial.build_commercial()
    assert d["read_only"] is True
    assert d["summary"]["tenants"] >= 0
    assert "growth" in d
    assert d["note"]
