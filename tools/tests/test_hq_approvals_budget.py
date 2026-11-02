# -*- coding: utf-8 -*-
"""HQ-03 审批 + HQ-04 预算只读模型测试。"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))

import hq_approvals as ha  # noqa: E402
import hq_budget as hb  # noqa: E402


def test_approvals_real_shape():
    a = ha.build_approvals()
    s = a["summary"]
    assert a["read_only"] is True
    assert s["total_decisions"] >= 40
    assert s["by_decision"].get("approved", 0) >= 40
    assert s["pending"] == 0
    assert a["policy"]["m4_policy"].get("owner") == "A0"


def test_approvals_risks_and_json(capsys):
    a = ha.build_approvals()
    assert any("100% approved" in x for x in a["risks"])
    rc = ha.main(["--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["summary"]["total_decisions"] >= 40


def test_budget_shape():
    b = hb.build_budget(days=30)
    assert b["read_only"] is True
    assert "usage_calls" in b["totals"]
    assert b["budget"]["token_budget"] > 0
    assert isinstance(b["by_domain"], dict)


def test_budget_json_pure(capsys):
    rc = hb.main(["--days", "30", "--json"])
    assert rc == 0
    d = json.loads(capsys.readouterr().out)
    assert d["budget"]["token_utilization"] is not None
    assert "risks" in d
